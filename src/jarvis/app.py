"""Assemblage des composants à partir de la config (partagé par run, bench et doctor)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import assets
from .audio.vad import SileroVad
from .audio.wakeword import WakeWord
from .config import Config
from .llm import LanguageModel, load_llm
from .stt import SpeechToText, load_stt
from .tts import TextToSpeech

LOG = logging.getLogger("jarvis")


def build_tts(cfg: Config) -> TextToSpeech:
    if cfg.tts.backend != "piper":
        raise ValueError(f"Backend de voix inconnu : {cfg.tts.backend} (disponible : piper)")
    from .tts.piper_backend import PiperTTS
    return PiperTTS(assets.piper_voice(cfg.tts.voice), cfg.tts.length_scale)


def build_wakeword(cfg: Config) -> WakeWord:
    return WakeWord(*assets.wakeword(cfg.wakeword.model), threshold=cfg.wakeword.threshold)


def build_vad(cfg: Config) -> SileroVad:
    return SileroVad(assets.ensure(assets.SILERO_VAD))


@dataclass
class Components:
    stt: SpeechToText
    llm: LanguageModel
    tts: TextToSpeech
    wakeword: WakeWord
    vad: SileroVad


def _timed[T](label: str, build: Callable[[], T]) -> T:
    start = time.perf_counter()
    result = build()
    LOG.info("  %-28s prêt en %5.0f ms", label, (time.perf_counter() - start) * 1000)
    return result


def load_all(cfg: Config, system_prompt: str) -> Components:
    """Charge et chauffe tout en parallèle : le démarrage prend le temps du plus lent,
    pas la somme. Le STT est chauffé sur le thread principal, celui qui transcrira
    ensuite (par prudence avec MLX et ses flux GPU)."""
    llm = load_llm(cfg.llm)
    llm.check()

    def warm_llm() -> LanguageModel:
        llm.warmup(system_prompt)
        return llm

    def warm_tts() -> TextToSpeech:
        tts = build_tts(cfg)
        tts.warmup()
        return tts

    def warm_stt() -> SpeechToText:
        stt = load_stt(cfg.stt)
        stt.warmup()
        return stt

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="load") as pool:
        llm_future = pool.submit(_timed, f"LLM {llm.model}", warm_llm)
        tts_future = pool.submit(_timed, f"Voix {cfg.tts.voice}", warm_tts)
        ears_future = pool.submit(_timed, "Mot d'activation + VAD", lambda: (build_wakeword(cfg), build_vad(cfg)))
        stt = _timed(f"Transcription {cfg.stt.backend}", warm_stt)
        wakeword, vad = ears_future.result()
        return Components(stt, llm_future.result(), tts_future.result(), wakeword, vad)
