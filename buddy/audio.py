"""Microphone input, wake word, end-of-speech detection, and speaker output.

Audio goes through the Pi's own `arecord` / `aplay` tools (ALSA "default" device, which PipeWire
handles). Python's PortAudio library can't see PipeWire devices on Pi OS, so we don't use it.
"""
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


class Speaker:
    """Plays int16 audio through aplay and reports loudness so the mouth can move in sync."""

    def __init__(self, state, device=None, volume=50):
        from . import settings
        self.state = state
        self.device = _dev(device)
        # saved setting (changed by voice) wins over config.toml
        with state.lock:
            state.volume = int(settings.load().get("volume", volume))

    def play(self, pcm: np.ndarray, rate: int, stop_event=None):
        if len(pcm) == 0:
            return
        proc = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with self.state.lock:
            gain = (self.state.volume / 100.0) ** 2  # squared so the scale feels even to the ear
        out_pcm = np.clip(pcm.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
        block = int(rate * 0.03)  # 30 ms
        lead = 0.12               # stay slightly ahead of playback so it never stutters
        start = time.monotonic()
        written = 0
        try:
            for i in range(0, len(pcm), block):
                if stop_event is not None and stop_event.is_set():
                    proc.kill()
                    break
                chunk = pcm[i:i + block]
                # pace writes to real time so the mouth matches what you hear
                ahead = start + written / rate - time.monotonic()
                if ahead > lead:
                    time.sleep(ahead - lead)
                level = min(1.0, Mic.rms(chunk) / 6000.0)
                with self.state.lock:
                    self.state.mouth_level = 0.6 * self.state.mouth_level + 0.4 * level
                proc.stdin.write(out_pcm[i:i + block].tobytes())
                written += len(chunk)
            if proc.poll() is None:
                proc.stdin.close()
                proc.wait(timeout=10)
            else:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
        except (BrokenPipeError, subprocess.TimeoutExpired):
            err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            if err:
                raise RuntimeError(f"aplay failed: {err}")
        finally:
            with self.state.lock:
                self.state.mouth_level = 0.0
        if proc.returncode not in (0, None, -9):
            err = proc.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(f"aplay failed: {err or proc.returncode}")

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
            self.play(np.concatenate(parts), rate)
        except Exception as e:
            log.debug("chime failed: %s", e)
