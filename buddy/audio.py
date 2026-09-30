"""Microphone input, wake word, end-of-speech detection, and speaker output."""
import logging
import queue
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger("audio")

RATE = 16000
BLOCK = 1280  # 80 ms at 16 kHz — the frame size openWakeWord expects


def _dev(v):
    if v in ("", None):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


class Mic:
    """Always-on microphone that pushes 80 ms int16 blocks into a queue."""

    def __init__(self, device=None):
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.noise_floor = 300.0
        self.muted = False
        self.stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
            device=_dev(device), callback=self._cb,
        )
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        if self.muted:
            return
        try:
            self.q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

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


class Speaker:
    """Plays int16 audio and reports loudness so the mouth can move."""

    def __init__(self, state, device=None):
        self.state = state
        self.device = _dev(device)

    def play(self, pcm: np.ndarray, rate: int, stop_event=None):
        block = int(rate * 0.03)  # 30 ms blocks
        with sd.OutputStream(samplerate=rate, channels=1, dtype="int16", device=self.device) as out:
            for i in range(0, len(pcm), block):
                if stop_event is not None and stop_event.is_set():
                    break
                chunk = pcm[i:i + block]
                level = min(1.0, Mic.rms(chunk) / 6000.0)
                with self.state.lock:
                    self.state.mouth_level = 0.6 * self.state.mouth_level + 0.4 * level
                out.write(chunk.reshape(-1, 1))
        with self.state.lock:
            self.state.mouth_level = 0.0

    def chime(self, kind="wake"):
        """Short beep so you know it's listening (wake) or done (done)."""
        rate = 22050
        tones = {"wake": (660, 880), "done": (880, 660), "error": (300, 200)}[kind]
        parts = []
        for f in tones:
            t = np.linspace(0, 0.08, int(rate * 0.08), False)
            env = np.minimum(1, np.minimum(t, 0.08 - t) * 60)
            parts.append((np.sin(2 * np.pi * f * t) * env * 6000).astype(np.int16))
        try:
            sd.play(np.concatenate(parts), rate, device=self.device, blocking=True)
        except Exception as e:
            log.debug("chime failed: %s", e)
