"""Talks to Claude: builds the prompt (personality + memory), streams the reply, runs tools,
and hands finished sentences to the mouth so it starts speaking before the reply is done."""
import datetime as dt
import logging
import queue
import re
import threading
import time
import uuid
from zoneinfo import ZoneInfo

import anthropic

from . import history as history_mod
from . import memory as memory_mod
from . import settings as settings_mod
from .config import load_personality
from .speech import clean_for_speech
from .state import MOODS

log = logging.getLogger("brain")

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
_TAG = re.compile(r"\[(\w+)\]")
_SENT_END = re.compile(r"(?<=[.!?])\s+")


class SentenceStream:
    """Turns streamed text into (mood, sentence) pairs as soon as each sentence is complete."""

    def __init__(self):
        self.buf = ""
        self.mood = None

    def feed(self, text: str):
        self.buf += text
        out = []
        parts = _SENT_END.split(self.buf)
        self.buf = parts.pop()  # last piece may be unfinished
        for p in parts:
            out.extend(self._emit(p))
        return out

    def flush(self):
        rest, self.buf = self.buf, ""
        return self._emit(rest)

    def _emit(self, sentence):
        m = _TAG.search(sentence)
        if m and m.group(1).lower() in MOODS:
            self.mood = m.group(1).lower()
        clean = clean_for_speech(sentence)
        return [(self.mood, clean)] if clean else []


class Mouth(threading.Thread):
    """Speaks sentences one at a time in the background."""

    def __init__(self, state, voice, speaker):
        super().__init__(daemon=True)
        self.state, self.voice, self.speaker = state, voice, speaker
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

    def say(self, mood, text):
        self.q.put((mood, text))

    def wait(self):
        self.q.join()

    def interrupt(self):
        self.stop_event.set()
        while not self.q.empty():
            try:
                self.q.get_nowait()
                self.q.task_done()
            except queue.Empty:
                break

    def run(self):
        while True:
            mood, text = self.q.get()
            try:
                if not self.stop_event.is_set():
                    self.state.set(mode="speaking", mood=mood, caption=text)
                    pcm, rate = self.voice.synth(text)
                    self.speaker.play(pcm, rate, stop_event=self.stop_event)
            except Exception as e:
                log.exception("speech failed: %s", e)
            finally:
                self.q.task_done()


class Brain:
    def __init__(self, cfg, state, mouth):
        self.cfg, self.state, self.mouth = cfg, state, mouth
        self.client = anthropic.Anthropic()
        self.memory = memory_mod.MemoryStore()
        self.history = history_mod.History()
        self.messages: list = []
        self.session = None
        self.last_turn = 0.0
        self.system = None
        c = cfg["claude"]
        self.model = c["model"]
        self.max_tokens = int(c.get("max_tokens", 1024))
        self.keep = int(c.get("history_turns", 20))
        self.timeout = float(c.get("session_timeout_min", 10)) * 60
        self.tz = ZoneInfo(cfg["buddy"].get("timezone", "UTC"))
        self.tools = [memory_mod.TOOL_SPEC, history_mod.TOOL_SPEC, settings_mod.VOLUME_TOOL]
        if c.get("web_search", True):
            self.tools.append(WEB_SEARCH_TOOL)

    # ---------- session / prompt ----------
    def _new_session_if_needed(self):
        if self.session and time.time() - self.last_turn < self.timeout:
            return
        self.session = uuid.uuid4().hex[:12]
        self.messages = []
        name = self.cfg["buddy"]["user_name"]
        text = load_personality(self.cfg) + memory_mod.MEMORY_INSTRUCTIONS.format(
            user=name, memory=self.memory.dump_for_prompt()
        ) + (
            "\nOTHER TOOLS\n- search_history: search older conversations.\n"
            "- set_volume: make your voice louder or quieter when asked.\n"
            "- web_search (if available): current info like weather, news, hours, prices. "
            "Summarize results in a sentence or two; never read URLs aloud.\n"
        )
        self.system = [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
        log.info("New session %s (memory preloaded, %d chars)", self.session, len(text))

    def _trim(self):
        # keep the tail, but always start on a plain user message (never split tool_use/tool_result pairs)
        while self.messages and (len(self.messages) > self.keep or not isinstance(self.messages[0]["content"], str)):
            self.messages.pop(0)

    def _now(self):
        return dt.datetime.now(self.tz).strftime("%A %B %d %Y, %I:%M %p")

    # ---------- main entry ----------
    def respond(self, user_text: str):
        self._new_session_if_needed()
        self.last_turn = time.time()
        self.history.add(self.session, "user", user_text)
        self.messages.append({"role": "user", "content": f"[{self._now()}] {user_text}"})
        self._trim()

        self.mouth.stop_event.clear()
        spoken = []
        turn_start = len(self.messages) - 1
        try:
            for _ in range(8):  # max tool rounds
                splitter = SentenceStream()
                self.state.set(mode="thinking")
                with self.client.messages.stream(
                    model=self.model, max_tokens=self.max_tokens, system=self.system,
                    messages=self.messages, tools=self.tools,
                ) as stream:
                    for chunk in stream.text_stream:
                        for mood, sent in splitter.feed(chunk):
                            spoken.append(sent)
                            self.mouth.say(mood, sent)
                    for mood, sent in splitter.flush():
                        spoken.append(sent)
                        self.mouth.say(mood, sent)
                    final = stream.get_final_message()

                self.messages.append({
                    "role": "assistant",
                    "content": [b.model_dump(exclude_none=True) for b in final.content],
                })
                if final.stop_reason == "pause_turn":  # server tool (web search) needs another round
                    continue
                if final.stop_reason != "tool_use":
                    break
                results = []
                for block in final.content:
                    if block.type != "tool_use":
                        continue
                    out = self._run_tool(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                self.messages.append({"role": "user", "content": results})
        except Exception as e:
            log.exception("Claude request failed: %s", e)
            del self.messages[turn_start:]  # drop the half-finished turn so the next one starts clean
            if isinstance(e, anthropic.AuthenticationError):
                msg = "My API key isn't working. Check the key in the dot env file."
            elif isinstance(e, anthropic.APIConnectionError):
                msg = "I can't reach the internet right now."
            else:
                msg = "Sorry, something went wrong in my brain. Try again in a moment."
            self.mouth.say("sad", msg)
        self.mouth.wait()
        self.history.add(self.session, "assistant", " ".join(spoken))
        self.last_turn = time.time()

    def _run_tool(self, name, args):
        log.info("tool %s %s", name, {k: (str(v)[:60]) for k, v in (args or {}).items()})
        if name == "memory":
            self.state.set(mood="thinking")
            return self.memory.handle(args)
        if name == "search_history":
            return self.history.search(args.get("query", ""), args.get("limit", 10), exclude_session=self.session)
        if name == "set_volume":
            return settings_mod.apply_volume(self.state, args.get("action", "set"), args.get("level"))
        return f"Error: unknown tool {name}"
