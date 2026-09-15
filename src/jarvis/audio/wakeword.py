"""Mot d'activation « Hey Jarvis » : les modèles ONNX d'openWakeWord exécutés directement.

Le paquet openwakeword n'est pas utilisé : sa version 0.6 impose tflite-runtime sous
Linux (aucune roue pour Python ≥ 3.12) et embarque scikit-learn. La chaîne est reproduite
à l'identique : mel-spectrogramme → embedding speech_embedding → classifieur.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

FRAME = 1280               # 80 ms à 16 kHz : un pas du modèle
_MEL_CONTEXT = 480         # 3 trames STFT de recouvrement (160 × 3)
_WINDOW = 76               # trames mel par embedding
_MEL_KEEP = 970            # ~10 s d'historique mel
_EMBEDDING = 96
_WARMUP_PREDICTIONS = 5    # openWakeWord ignore les 5 premières prédictions


def _session(path: Path) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class WakeWord:
    def __init__(self, melspec: Path, embedding: Path, model: Path, threshold: float = 0.5):
        self._mel = _session(melspec)
        self._emb = _session(embedding)
        self._clf = _session(model)
        clf_input = self._clf.get_inputs()[0]
        self._clf_input = clf_input.name
        self._n_features = int(clf_input.shape[1])
        self.threshold = threshold
        # openWakeWord démarre avec les embeddings de 10 s de silence : on fait pareil.
        self._blank = self._embed_clip(np.zeros(10 * 16000, np.int16))[-self._n_features:]
        self.reset()

    def reset(self) -> None:
        self._raw = np.zeros(_MEL_CONTEXT, np.int16)
        self._pending = np.zeros(0, np.int16)
        self._mels = np.ones((_WINDOW, 32), np.float32)
        self._features = self._blank.copy()
        self._steps = 0
        self.score = 0.0

    def process(self, samples: np.ndarray) -> float:
        """Ajoute des échantillons int16 à 16 kHz ; renvoie le dernier score (0..1)."""
        self._pending = np.concatenate([self._pending, samples])
        while len(self._pending) >= FRAME:
            frame, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            self._step(frame)
        return self.score

    def _melspec(self, samples: np.ndarray) -> np.ndarray:
        out = self._mel.run(None, {"input": samples.astype(np.float32)[None, :]})[0]
        return out.reshape(-1, 32) / 10 + 2

    def _embed_clip(self, samples: np.ndarray) -> np.ndarray:
        mels = self._melspec(samples)
        windows = np.stack([mels[i:i + _WINDOW] for i in range(0, len(mels) - _WINDOW + 1, 8)])
        return self._emb.run(None, {"input_1": windows[..., None].astype(np.float32)})[0].reshape(-1, _EMBEDDING)

    def _step(self, frame: np.ndarray) -> None:
        self._raw = np.concatenate([self._raw, frame])[-(FRAME + _MEL_CONTEXT):]
        self._mels = np.vstack([self._mels, self._melspec(self._raw)])[-_MEL_KEEP:]
        window = self._mels[-_WINDOW:][None, :, :, None].astype(np.float32)
        embedding = self._emb.run(None, {"input_1": window})[0].reshape(1, _EMBEDDING)
        self._features = np.vstack([self._features, embedding])[-self._n_features:]
        self._steps += 1
        features = self._features[None].astype(np.float32)
        score = float(self._clf.run(None, {self._clf_input: features})[0].reshape(-1)[0])
        self.score = score if self._steps > _WARMUP_PREDICTIONS else 0.0
