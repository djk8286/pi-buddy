"""Microphone input, wake word, end-of-speech detection, and speaker output.

Audio goes through the Pi's own `arecord` / `aplay` tools (ALSA "default" device, which PipeWire
handles). Python's PortAudio library can't see PipeWire devices on Pi OS, so we don't use it.
"""
import collections
import logging
import queue
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("audio")

RATE = 16000
BLOCK = 1280  # 80 ms at 16 kHz — the frame size openWakeWord expects


_AUTO_DEV = None


def _dev(v):
    """Device for aplay/arecord. Blank config = PipeWire if available (lets several programs share
    the speakers), otherwise ALSA 'default'."""
    global _AUTO_DEV
    if v not in ("", None):
        return str(v).strip()
    if _AUTO_DEV is None:
        try:
            names = subprocess.run(["aplay", "-L"], capture_output=True, text=True, timeout=5).stdout.split()
            _AUTO_DEV = "pipewire" if "pipewire" in names else "default"
        except Exception:
            _AUTO_DEV = "default"
        log.info("Audio device: %s", _AUTO_DEV)
    return _AUTO_DEV


class Mic:
    """Always-on microphone (arecord) that pushes 80 ms int16 blocks into a queue."""

    def __init__(self, device=None):
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.noise_floor = 300.0
        self.muted = False
        self.proc = subprocess.Popen(
            ["arecord", "-q", "-D", _dev(device), "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        time.sleep(0.6)
        if self.proc.poll() is not None:  # arecord quit right away = no usable microphone
            err = self.proc.stderr.read().decode(errors="replace").strip().splitlines()
            raise RuntimeError(err[-1] if err else "arecord could not open the microphone")
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        nbytes = BLOCK * 2
        while True:
            data = self.proc.stdout.read(nbytes)
            if not data or len(data) < nbytes:
                log.warning("Microphone stream ended")
                return
            if self.muted:
                continue
            try:
                self.q.put_nowait(np.frombuffer(data, dtype=np.int16).copy())
            except queue.Full:
                pass

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()

    def read(self, timeout=0.5):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    @staticmethod
    def rms(block) -> float:
        return float(np.sqrt(np.mean(block.astype(np.float32) ** 2))) if len(block) else 0.0

    def track_noise(self, block):
        # slow-moving average of background noise, used for the speech threshold
        self.noise_floor = 0.95 * self.noise_floor + 0.05 * self.rms(block)

    def record_utterance(self, silence_s=0.9, wait_s=5.0, max_s=20.0, stop_event=None):
        """Record until the speaker goes quiet. Returns float32 audio, or None if nobody spoke."""
        threshold = max(self.noise_floor * 3.0, 500.0)
        frames, started, quiet, t0 = [], False, 0.0, time.time()
        blk_s = BLOCK / RATE
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            b = self.read(timeout=1.0)
            if b is None:
                continue
            loud = self.rms(b) > threshold
            if not started:
                frames = (frames + [b])[-4:]  # keep ~300 ms of pre-roll so first word isn't clipped
                if loud:
                    started = True
                elif time.time() - t0 > wait_s:
                    return None
                continue
            frames.append(b)
            quiet = 0.0 if loud else quiet + blk_s
            if quiet >= silence_s or len(frames) * blk_s >= max_s:
                break
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return audio


class WakeWord:
    """Wraps openWakeWord. If it isn't installed, `available` is False and tap-to-talk still works."""

    def __init__(self, name="hey_jarvis", threshold=0.5):
        self.available = False
        self.name, self.threshold = name, threshold
        try:
            import openwakeword
            from openwakeword.model import Model
            try:
                openwakeword.utils.download_models([name])
            except Exception as e:  # already downloaded or offline
                log.debug("wake word download skipped: %s", e)
            self.model = Model(wakeword_models=[name], inference_framework="onnx")
            self.available = True
            log.info("Wake word '%s' ready", name)
        except Exception as e:
            log.warning("Wake word unavailable (%s) — tap the screen to talk.", e)

    def heard(self, block) -> bool:
        if not self.available:
            return False
        scores = self.model.predict(block)
        return any(v >= self.threshold for v in scores.values())

    def reset(self):
        if self.available:
            self.model.reset()


class _Job:
    def __init__(self, pcm, levels_src):
        self.pcm = pcm              # volume-scaled audio to play
        self.src = levels_src       # original audio, used for mouth movement
        self.done = threading.Event()
        self.cancel = threading.Event()
        self.error = None


class Speaker:
    """Keeps ONE aplay stream open and feeds it silence between sentences.

    The case's HDMI audio chip/amp 'wakes up' with a crackle whenever audio starts after a pause,
    so we never let it pause. Also reports loudness on a clock matched to playback so the mouth
    moves in sync with what you hear."""

    RATE = 22050
    BLOCK = 441          # 20 ms
    LEAD = 0.2           # how far ahead of the speaker we keep the buffer filled

    def __init__(self, state, device=None, volume=50):
        from . import settings
        self.state = state
        self.device = _dev(device)
        with state.lock:
            state.volume = int(settings.load().get("volume", volume))  # saved setting wins
        self.q: queue.Queue = queue.Queue()
        self.timeline = collections.deque()   # (play_time, level) or (play_time, None, job)
        self.tl_lock = threading.Lock()
        self.proc = None
        threading.Thread(target=self._writer, daemon=True).start()
        threading.Thread(target=self._mouth_clock, daemon=True).start()

    # ---------- public ----------
    def play(self, pcm: np.ndarray, rate: int, stop_event=None):
        if len(pcm) == 0:
            return
        src = pcm.astype(np.float32)
        if rate != self.RATE:  # simple resample to the stream rate
            n = int(len(src) * self.RATE / rate)
            src = np.interp(np.linspace(0, len(src) - 1, n), np.arange(len(src)), src)
        with self.state.lock:
            gain = (self.state.volume / 100.0) ** 2  # squared so the scale feels even to the ear
        out = src * gain
        fade = min(len(out) // 2, int(self.RATE * 0.01))  # 10 ms fade in/out: no clicks
        if fade:
            ramp = np.linspace(0, 1, fade)
            out[:fade] *= ramp
            out[-fade:] *= ramp[::-1]
        job = _Job(np.clip(out, -32768, 32767).astype(np.int16), src)
        self.q.put(job)
        while not job.done.wait(0.05):
            if stop_event is not None and stop_event.is_set():
                job.cancel.set()
                break
        if job.error:
            raise RuntimeError(job.error)

    def chime(self, kind="wake"):
        """Short beep so you know it's listening (wake) or done (done)."""
        rate = self.RATE
        tones = {"wake": (660, 880), "done": (880, 660), "error": (300, 200)}[kind]
        parts = []
        for f in tones:
            t = np.linspace(0, 0.08, int(rate * 0.08), False)
            env = np.minimum(1, np.minimum(t, 0.08 - t) * 60)
            parts.append((np.sin(2 * np.pi * f * t) * env * 6000).astype(np.int16))
        try:
            self.play(np.concatenate(parts), rate)
        except Exception as e:
            log.debug("chime failed: %s", e)

    # ---------- internals ----------
    def _open(self):
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE",
             "-r", str(self.RATE), "-c", "1", "-B", "300000"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        time.sleep(0.15)
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode(errors="replace").strip()
            self.proc = None
            raise RuntimeError(err.splitlines()[-1] if err else "aplay could not open the speakers")
        log.info("Speaker stream open on '%s'", self.device)

    def _fail_pending(self, msg, job=None):
        jobs = [job] if job else []
        while True:
            try:
                jobs.append(self.q.get_nowait())
            except queue.Empty:
                break
        for j in jobs:
            j.error = msg
            j.done.set()

    def _writer(self):
        silence = bytes(self.BLOCK * 2)
        job, pos, start, written = None, 0, 0.0, 0
        warned = False
        while True:
            if self.proc is None or self.proc.poll() is not None:
                try:
                    self._open()
                    start, written, warned = time.monotonic(), 0, False
                except Exception as e:
                    if not warned:
                        log.error("Speaker unavailable: %s (retrying)", e)
                        warned = True
                    self._fail_pending(f"aplay failed: {e}", job)
                    job = None
                    time.sleep(2)
                    continue
            if job is None:
                try:
                    job, pos = self.q.get_nowait(), 0
                except queue.Empty:
                    pass
            if job is not None and job.cancel.is_set():
                job.done.set()
                job = None
            now_play = start + written / self.RATE
            if job is not None:
                chunk = job.pcm[pos:pos + self.BLOCK]
                level = min(1.0, Mic.rms(job.src[pos:pos + self.BLOCK]) / 6000.0)
                data = chunk.tobytes()
                pos += self.BLOCK
                with self.tl_lock:
                    self.timeline.append((now_play, level))
                    if pos >= len(job.pcm):
                        self.timeline.append((now_play + len(chunk) / self.RATE, None, job))
                if pos >= len(job.pcm):
                    job = None
                n = len(chunk)
            else:
                data, n = silence, self.BLOCK
            try:
                self.proc.stdin.write(data)
            except (BrokenPipeError, OSError):
                log.warning("Speaker stream closed, reopening")
                self.proc = None
                continue
            written += n
            ahead = start + written / self.RATE - time.monotonic()
            if ahead > self.LEAD:
                time.sleep(ahead - self.LEAD)
            elif ahead < -0.3:  # fell behind (Pi was busy): re-anchor the clock
                start = time.monotonic() - written / self.RATE + self.LEAD

    def _mouth_clock(self):
        while True:
            now = time.monotonic()
            level = None
            with self.tl_lock:
                while self.timeline and self.timeline[0][0] <= now:
                    item = self.timeline.popleft()
                    if item[1] is None:
                        item[2].done.set()
                    else:
                        level = item[1]
                idle = not self.timeline
            with self.state.lock:
                if level is not None:
                    self.state.mouth_level = 0.6 * self.state.mouth_level + 0.4 * level
                elif idle:
                    self.state.mouth_level = 0.0
            time.sleep(0.015)
