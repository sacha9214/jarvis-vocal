"""Whisper sur le GPU Apple (MLX)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from . import clean_transcript, is_repetitive, prepare_audio, suspicious, vocabulary_prompt

_MAX_TOKENS = 120   # une phrase parlée tient largement dedans ; une boucle de décodage s'arrête vite


class MlxWhisper:
    name = "mlx-whisper"

    def __init__(self, model: str, language: str):
        import mlx_whisper

        self._mlx_whisper = mlx_whisper
        self.model = model
        self.prompt: str | None = None
        self._options = dict(
            path_or_hf_repo=model,
            language=language,              # pas de détection de langue : un passage de moins
            temperature=0.0,                # pas de relances à température plus haute (pics de latence)
            condition_on_previous_text=False,
            without_timestamps=True,
            sample_len=_MAX_TOKENS,
            verbose=None,
        )

    def set_vocabulary(self, words: Sequence[str]) -> None:
        self.prompt = vocabulary_prompt(words)

    def warmup(self) -> None:
        # Télécharge le modèle au besoin et compile les noyaux Metal : la 1re vraie phrase est rapide.
        self._decode(np.zeros(16000, np.float32), self.prompt)
        self._decode(np.zeros(16000, np.float32), None)

    def _decode(self, audio: np.ndarray, prompt: str | None) -> str:
        options = {**self._options, "initial_prompt": prompt} if prompt else self._options
        return self._mlx_whisper.transcribe(audio, **options)["text"]

    def transcribe(self, audio: np.ndarray) -> str:
        audio = prepare_audio(audio)
        try:
            text = self._decode(audio, self.prompt)
            if self.prompt and suspicious(text, self.prompt):
                text = self._decode(audio, None)    # second essai sans amorce
        finally:
            self._release()
        return "" if is_repetitive(text) else text

    def transcribe_hint(self, audio: np.ndarray, hint: str) -> str:
        """Phrase courte amorcée par un mot attendu (mot d'activation personnalisé) : un seul essai, pas de repli."""
        try:
            return clean_transcript(self._decode(prepare_audio(audio), hint))
        finally:
            self._release()

    @staticmethod
    def _release() -> None:
        """Rend au système les tampons Metal gardés en réserve par MLX (des centaines de Mo entre deux phrases)."""
        try:
            import mlx.core as mx
            clear = getattr(mx, "clear_cache", None) or getattr(getattr(mx, "metal", None), "clear_cache", None)
            if clear:
                clear()
        except Exception:  # noqa: BLE001 - confort mémoire, jamais bloquant
            pass
