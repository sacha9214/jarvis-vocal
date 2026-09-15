"""Audio temps réel : capture, lecture, mot d'activation, détection de parole."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


def to_int16(samples: np.ndarray) -> np.ndarray:
    return np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
