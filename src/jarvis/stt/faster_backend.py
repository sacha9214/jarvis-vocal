"""Whisper via CTranslate2 (faster-whisper) : CUDA sous Windows/Linux, sinon CPU int8."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import numpy as np

from ..config import SttConfig

LOG = logging.getLogger("jarvis.stt")


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
        self._language = language
        threads = max(2, (os.cpu_count() or 4) // 2)
        self._model = WhisperModel(model, device=device, compute_type=compute_type, cpu_threads=threads)

    def warmup(self) -> None:
        self.transcribe(np.zeros(16000, np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio.astype(np.float32), language=self._language, beam_size=1, best_of=1,
            temperature=0.0, condition_on_previous_text=False, without_timestamps=True,
            vad_filter=False,   # la phrase est déjà découpée par notre VAD
        )
        return "".join(segment.text for segment in segments)


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
