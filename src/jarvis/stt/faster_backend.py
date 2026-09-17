"""Whisper via CTranslate2 (faster-whisper) : CUDA sous Windows/Linux, sinon CPU int8."""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..config import SttConfig
from . import clean_transcript, is_repetitive, prepare_audio, suspicious, vocabulary_prompt

LOG = logging.getLogger("jarvis.stt")
_MAX_TOKENS = 120


def _add_cuda_dll_dirs() -> None:
    """Sous Windows, les DLL cuBLAS/cuDNN des roues pip nvidia-* ne sont pas dans le PATH."""
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    for root in getattr(nvidia, "__path__", []):
        for bin_dir in Path(root).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


class FasterWhisper:
    name = "faster-whisper"

    def __init__(self, model: str, device: str, compute_type: str, language: str):
        from faster_whisper import WhisperModel

        self.model = model
        self.device = device
        self.prompt: str | None = None
        self._language = language
        threads = max(2, (os.cpu_count() or 4) // 2)
        self._model = WhisperModel(model, device=device, compute_type=compute_type, cpu_threads=threads)

    def set_vocabulary(self, words: Sequence[str]) -> None:
        self.prompt = vocabulary_prompt(words)

    def warmup(self) -> None:
        self._decode(np.zeros(16000, np.float32), self.prompt)

    def _decode(self, audio: np.ndarray, prompt: str | None) -> str:
        segments, _ = self._model.transcribe(
            audio, language=self._language, beam_size=1, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True, initial_prompt=prompt,
            max_new_tokens=_MAX_TOKENS,
            vad_filter=False,   # la phrase est déjà découpée par notre VAD
        )
        return "".join(segment.text for segment in segments)

    def transcribe(self, audio: np.ndarray) -> str:
        audio = prepare_audio(audio)
        text = self._decode(audio, self.prompt)
        if self.prompt and suspicious(text, self.prompt):
            text = self._decode(audio, None)    # second essai sans amorce
        return "" if is_repetitive(text) else text

    def transcribe_hint(self, audio: np.ndarray, hint: str) -> str:
        """Phrase courte amorcée par un mot attendu (mot d'activation personnalisé) : un seul essai, pas de repli."""
        return clean_transcript(self._decode(prepare_audio(audio), hint))


def load(cfg: SttConfig) -> FasterWhisper:
    if cfg.device == "cuda":
        _add_cuda_dll_dirs()
        try:
            stt = FasterWhisper(cfg.model, "cuda", cfg.compute_type, cfg.language)
            stt.warmup()
            return stt
        except Exception as exc:  # DLL CUDA absentes, pilote trop ancien, VRAM pleine…
            LOG.warning("CUDA indisponible pour Whisper (%s) : repli sur CPU (modèle small, int8).", exc)
            return FasterWhisper("small", "cpu", "int8", cfg.language)
    return FasterWhisper(cfg.model, "cpu", cfg.compute_type, cfg.language)
