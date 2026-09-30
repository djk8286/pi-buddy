"""Speech-to-text (faster-whisper) and text-to-speech (Piper), both running on the Pi."""
import logging
import re

import numpy as np

from .config import ROOT

log = logging.getLogger("speech")


class Ears:
    def __init__(self, model_name="base.en"):
        from faster_whisper import WhisperModel
        log.info("Loading Whisper %s ...", model_name)
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True, condition_on_previous_text=False
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper sometimes hallucinates these on silence/noise
        if text.lower().strip(" .!") in {"", "you", "thank you", "thanks for watching", "bye"}:
            return ""
        return text


class Voice:
    def __init__(self, voice_path="voices/en_US-lessac-medium.onnx"):
        from piper import PiperVoice
        path = ROOT / voice_path
        log.info("Loading Piper voice %s ...", path.name)
        self.voice = PiperVoice.load(str(path))

    def synth(self, text: str):
        """Returns (int16 samples, sample_rate)."""
        chunks, rate = [], 22050
        for chunk in self.voice.synthesize(text):
            rate = chunk.sample_rate
            chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        if not chunks:
            return np.zeros(0, dtype=np.int16), rate
        return np.concatenate(chunks), rate


def clean_for_speech(text: str) -> str:
    """Strip markdown and stray symbols that TTS would read out loud."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # [label](url) -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"\[\w+\]", "", text)                      # leftover mood tags
    text = re.sub(r"[*_`#>~\[\]]", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()
