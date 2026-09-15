"""Synthèse vocale. Étape 1 : Piper (local, temps réel sur CPU)."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

import numpy as np


class TextToSpeech(Protocol):
    name: str
    sample_rate: int

    def warmup(self) -> None: ...

    def synthesize(self, text: str) -> Iterator[np.ndarray]: ...
