"""Whisper sur le GPU Apple (MLX)."""
from __future__ import annotations

import numpy as np


class MlxWhisper:
    name = "mlx-whisper"

    def __init__(self, model: str, language: str):
        import mlx_whisper

        self._mlx_whisper = mlx_whisper
        self.model = model
        self._options = dict(
            path_or_hf_repo=model,
            language=language,              # pas de détection de langue : un passage de moins
            temperature=0.0,                # pas de relances à température plus haute (pics de latence)
            condition_on_previous_text=False,
            without_timestamps=True,
            verbose=None,
        )

    def warmup(self) -> None:
        # Télécharge le modèle au besoin et compile les noyaux Metal : la 1re vraie phrase est rapide.
        self.transcribe(np.zeros(16000, np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        return self._mlx_whisper.transcribe(audio.astype(np.float32), **self._options)["text"]
