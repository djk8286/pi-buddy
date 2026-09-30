"""Entry point: face in the main thread, listening/thinking loop in a worker thread."""
import logging
import os
import sys
import threading
import time

from .config import DATA, load_config
from .state import BuddyState

log = logging.getLogger("main")


def conversation_loop(cfg, state):
    from .audio import Mic, Speaker, WakeWord
    from .brain import Brain, Mouth
    from .speech import Ears, Voice

    v = cfg["voice"]
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("No ANTHROPIC_API_KEY — put it in the .env file")
        state.set(caption="Loading ears...")
        ears = Ears(v.get("whisper_model", "base.en"))
        state.set(caption="Loading voice...")
        voice = Voice(v.get("piper_voice", "voices/en_US-lessac-medium.onnx"))
        speaker = Speaker(state, v.get("output_device"))
        mic = Mic(v.get("input_device"))
        wake = WakeWord(v.get("wake_word", "hey_jarvis"), float(v.get("wake_threshold", 0.5)))
        mouth = Mouth(state, voice, speaker)
        mouth.start()
        brain = Brain(cfg, state, mouth)
    except Exception as e:
        log.exception("Startup failed")
        state.set(mode="error", mood="sad", caption=f"Startup error: {e}")
        return

    how = f'Say "{v.get("wake_word", "hey_jarvis").replace("_", " ")}" or tap me' if wake.available else "Tap me to talk"
    state.set(mode="idle", mood="happy", caption=how)
    log.info("Ready. %s", how)
    follow_up_until = 0.0

    while not state.quit_event.is_set():
        # ----- wait for wake word, a tap, or a follow-up reply -----
        triggered = False
        if time.time() < follow_up_until:
            triggered = True  # listen straight away for a reply
        else:
            state.set(mode="idle")
            wake.reset()
            while not state.quit_event.is_set():
                if state.tap_event.is_set():
                    state.tap_event.clear()
                    triggered = True
                    break
                block = mic.read(timeout=0.2)
                if block is None:
                    continue
                if wake.heard(block):
                    triggered = True
                    break
                mic.track_noise(block)
            if triggered:
                speaker.chime("wake")
                mic.flush()
        if not triggered:
            break

        # ----- record what the user says -----
        state.set(mode="listening", mood="curious")
        wait_s = float(v.get("follow_up_seconds", 6)) if follow_up_until else 6.0
        audio = mic.record_utterance(silence_s=float(v.get("silence_seconds", 0.9)), wait_s=wait_s,
                                     stop_event=state.quit_event)
        follow_up_until = 0.0
        if audio is None:
            state.set(mode="idle", mood="neutral")
            continue

        state.set(mode="thinking", mood="thinking", caption="...")
        text = ears.transcribe(audio)
        log.info("Heard: %r", text)
        if not text:
            state.set(mode="idle", mood="neutral")
            continue
        state.set(caption=f'"{text}"')

        # ----- think + speak (tap while it's talking to interrupt) -----
        mic.muted = True  # don't listen to ourselves
        state.tap_event.clear()
        worker = threading.Thread(target=brain.respond, args=(text,), daemon=True)
        worker.start()
        while worker.is_alive():
            if state.tap_event.is_set():
                state.tap_event.clear()
                mouth.interrupt()
            worker.join(timeout=0.1)
        time.sleep(0.2)
        mic.flush()
        mic.muted = False
        state.set(mode="idle")
        follow_up_until = time.time() + float(v.get("follow_up_seconds", 6))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    cfg = load_config()
    fh = logging.FileHandler(DATA / "buddy.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)

    from .face import Face

    state = BuddyState()
    face = Face(state, cfg)
    threading.Thread(target=conversation_loop, args=(cfg, state), daemon=True).start()
    face.run()


if __name__ == "__main__":
    main()
