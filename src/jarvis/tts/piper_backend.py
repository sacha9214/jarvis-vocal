"""Voix locale Piper."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np


class PiperTTS:
    name = "piper"

    def __init__(self, model_path: Path, length_scale: float = 1.0):
        from piper import PiperVoice, SynthesisConfig

        self._voice = PiperVoice.load(str(model_path))
        self._config = SynthesisConfig(length_scale=length_scale)
        self.sample_rate = int(self._voice.config.sample_rate)

    def set_length_scale(self, length_scale: float) -> None:
        from piper import SynthesisConfig
        self._config = SynthesisConfig(length_scale=length_scale)

    def warmup(self) -> None:
        for _ in self.synthesize("Bonjour."):
            pass

    def synthesize(self, text: str) -> Iterator[np.ndarray]:
        for chunk in self._voice.synthesize(text, self._config):
            yield chunk.audio_float_array.astype(np.float32, copy=False)
