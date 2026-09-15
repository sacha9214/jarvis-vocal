"""Voix Pocket TTS (Kyutai) : naturelle, en français, en flux, sur le processeur.

Plus humaine que Piper, pour un premier son en ~150-250 ms sur un Mac M5. Elle tourne sur le
processeur, sans prendre de place au GPU occupé par Whisper et le LLM.

Mesuré sur 16 réponses courtes typiques (« J'ouvre Spotify. », « Il est 21 heures 7. ») :
Fantine à température 0,5 en rend 14 à 15 correctement ; les voix masculines, 2 ou 3. D'où la voix
par défaut. Si la machine est trop lente pour parler sans hacher, le chargement échoue et Jarvis
garde Piper ; si une génération s'emballe, elle est coupée.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator

import numpy as np

LOG = logging.getLogger("jarvis.tts")
logging.getLogger("pocket_tts").setLevel(logging.ERROR)   # statistiques à chaque phrase sinon
LANGUAGE = "french_24l"
RELIABLE_VOICES = ("fantine",)
MAX_REALTIME_FACTOR = 0.9   # au-delà, la génération ne suit plus la parole : la voix hacherait
# La moitié des cœurs : assez pour la voix, sans affamer la transcription et le mot d'activation.
THREADS = max(2, min(6, (os.cpu_count() or 4) // 2))


class TooSlow(RuntimeError):
    """La machine ne génère pas la voix assez vite."""


def max_samples(text: str, sample_rate: int) -> int:
    """Durée au-delà de laquelle une phrase s'est emballée (~8 caractères par seconde, plus une marge)."""
    return int(sample_rate * (1.5 + 0.12 * len(text)))


class PocketTTS:
    name = "pocket"

    def __init__(self, voice: str, language: str = LANGUAGE, temperature: float | None = 0.5):
        import torch
        from pocket_tts import TTSModel

        torch.set_num_threads(THREADS)
        self._model = TTSModel.load_model(language=language, temp=temperature)
        self.sample_rate = int(self._model.sample_rate)
        self.voice = ""
        self.set_voice(voice)

    def set_voice(self, voice: str) -> None:
        """Nom d'une voix intégrée, ou chemin d'un .wav à imiter. Instantané : changeable en direct."""
        self._state = self._model.get_state_for_audio_prompt(voice)
        self.voice = voice
        if voice not in RELIABLE_VOICES:
            LOG.info("Voix « %s » : moins fiable que Fantine sur les phrases courtes en français.", voice)

    def warmup(self) -> None:
        for _ in self.synthesize("Bonjour."):
            pass
        # Meilleur de deux mesures : au démarrage, Whisper et le LLM se chargent en même temps.
        factor = min(self._realtime_factor() for _ in range(2))
        LOG.debug("Pocket TTS : facteur temps réel %.2f", factor)
        if factor > MAX_REALTIME_FACTOR:
            raise TooSlow(f"processeur trop lent pour Pocket TTS (facteur temps réel {factor:.2f})")

    def _realtime_factor(self) -> float:
        start = time.perf_counter()
        samples = sum(len(block) for block in self.synthesize("Je suis prêt, dis-moi ce que tu veux faire."))
        return (time.perf_counter() - start) / max(samples / self.sample_rate, 1e-6)

    def synthesize(self, text: str) -> Iterator[np.ndarray]:
        limit = max_samples(text, self.sample_rate)
        produced = 0
        stream = self._model.generate_audio_stream(self._state, text)
        try:
            for chunk in stream:
                block = chunk.detach().to("cpu").numpy().reshape(-1).astype(np.float32, copy=False)
                produced += len(block)
                yield block
                if produced > limit:
                    LOG.warning("Voix emballée sur %r : phrase coupée.", text[:60])
                    return
        finally:
            stream.close()
