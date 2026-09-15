"""Transcription vocale : MLX (Metal) sur Apple Silicon, faster-whisper ailleurs."""
from __future__ import annotations

from typing import Protocol

import numpy as np

from ..config import SttConfig

# Phrases que Whisper « entend » dans le silence ou le bruit (héritage des sous-titres
# de son corpus). Les répéter à voix haute serait absurde : on les jette.
_HALLUCINATIONS = (
    "sous-titres réalisés par", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "abonnez-vous", "sous-titres par", "sous titres réalisés",
)


class SpeechToText(Protocol):
    name: str

    def warmup(self) -> None: ...

    def transcribe(self, audio: np.ndarray) -> str: ...


def clean_transcript(text: str) -> str:
    text = text.strip().strip("…").strip()
    low = text.lower()
    if any(h in low for h in _HALLUCINATIONS):
        return ""
    return text


def load_stt(cfg: SttConfig) -> SpeechToText:
    if cfg.backend == "mlx":
        from .mlx_backend import MlxWhisper
        return MlxWhisper(cfg.model, cfg.language)
    if cfg.backend == "faster-whisper":
        from .faster_backend import load
        return load(cfg)
    raise ValueError(f"Backend de transcription inconnu : {cfg.backend} (mlx, faster-whisper)")
