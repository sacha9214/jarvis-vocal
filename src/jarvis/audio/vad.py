"""Détection de parole Silero VAD (ONNX) : sait quand tu as fini de parler."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from . import SAMPLE_RATE

CHUNK = 512      # 32 ms à 16 kHz, taille imposée par le modèle
_CONTEXT = 64    # échantillons du bloc précédent que le modèle v5+ attend en tête


class SileroVad:
    def __init__(self, model_path: Path):
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_path), sess_options=opts,
                                             providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        """Probabilité de parole (0..1) pour 512 échantillons float32 à 16 kHz."""
        x = np.concatenate([self._context, chunk.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -_CONTEXT:]
        return float(out[0, 0])
