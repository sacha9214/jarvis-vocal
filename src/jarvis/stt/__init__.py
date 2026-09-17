"""Transcription vocale : MLX (Metal) sur Apple Silicon, faster-whisper ailleurs."""
from __future__ import annotations

import zlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from ..config import SttConfig
from ..fastpath import normalize

# Phrases que Whisper « entend » dans le silence ou le bruit (héritage des sous-titres
# de son corpus). Les répéter à voix haute serait absurde : on les jette.
_HALLUCINATIONS = (
    "sous-titres réalisés par", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "abonnez-vous", "sous-titres par", "sous titres réalisés",
)
# Amorce : Whisper transcrit bien mieux les mots qu'il vient de « lire » (noms d'applications,
# commandes courantes) et reste ancré en français.
_BASE_NAMES = ("Spotify", "YouTube", "Netflix", "Discord")
_COMMANDS = "Monte le volume, mets pause, verrouille l'écran, lance un minuteur, passe sur Claude."


class SpeechToText(Protocol):
    name: str

    def warmup(self) -> None: ...

    def set_vocabulary(self, words: Sequence[str]) -> None: ...

    def transcribe(self, audio: np.ndarray) -> str: ...

    def transcribe_hint(self, audio: np.ndarray, hint: str) -> str: ...


def clean_transcript(text: str) -> str:
    text = text.strip().strip("…").strip()
    low = text.lower()
    if any(h in low for h in _HALLUCINATIONS):
        return ""
    return text


def vocabulary_prompt(words: Sequence[str], limit: int = 30) -> str:
    names = list(dict.fromkeys([*_BASE_NAMES, *words]))[:limit]
    return f"Jarvis, ouvre {', '.join(names)}. {_COMMANDS}"


def echoes_prompt(text: str, prompt: str | None) -> bool:
    """Sur du bruit, Whisper recopie parfois un long morceau de son amorce : on le jette.
    Une commande courte présente dans l'amorce (« passe sur Claude ») reste valable."""
    if not prompt:
        return False
    heard = normalize(text)
    return len(heard.split()) >= 6 and heard in normalize(prompt)


def is_repetitive(text: str) -> bool:
    """Boucle de décodage (« de la voie de la voie… ») : un texte anormalement compressible."""
    raw = text.strip().encode("utf-8")
    return len(raw) > 40 and len(raw) / len(zlib.compress(raw)) > 2.4


def suspicious(text: str, prompt: str | None) -> bool:
    """L'amorce a pu faire dérailler Whisper : boucle, recopie ou phrase fantôme."""
    return is_repetitive(text) or echoes_prompt(text, prompt) or not clean_transcript(text)


def prepare_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if 0.0 < peak < 0.3:   # micro faible ou lointain : Whisper préfère un signal franc
        audio = audio * min(0.9 / peak, 8.0)
    return audio


def load_stt(cfg: SttConfig) -> SpeechToText:
    if cfg.backend == "mlx":
        from .mlx_backend import MlxWhisper
        return MlxWhisper(cfg.model, cfg.language)
    if cfg.backend == "faster-whisper":
        from .faster_backend import load
        return load(cfg)
    raise ValueError(f"Backend de transcription inconnu : {cfg.backend} (mlx, faster-whisper)")
