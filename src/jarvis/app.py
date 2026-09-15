"""Assemblage des composants à partir de la config (partagé par run, bench et doctor)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import assets
from . import config as config_module
from .audio.vad import SileroVad
from .audio.wakeword import WakeWord
from .config import Config
from .events import EventBus
from .llm import load_llm
from .llm.router import CLAUDE, Router
from .server import LocalServer, start_server
from .stt import SpeechToText, load_stt
from .tools import ToolExecutor
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


def build_executor(cfg: Config) -> ToolExecutor:
    def remember(name: str) -> None:
        path = config_module.update_file({"tools": {"always_allow": list(cfg.tools.always_allow)}})
        LOG.info("« %s » est désormais autorisé sans confirmation (enregistré dans %s).", name, path)
    return ToolExecutor(cfg.tools, on_always=remember)


@dataclass
class Components:
    stt: SpeechToText
    llm: Router
    tts: TextToSpeech
    wakeword: WakeWord
    vad: SileroVad
    executor: ToolExecutor
    server: LocalServer | None = None


def load_all(cfg: Config, system_prompt: str, bus: EventBus | None = None, executor: ToolExecutor | None = None,
             server: LocalServer | None = None) -> Components:
    """Charge et chauffe tout en parallèle : le démarrage prend le temps du plus lent,
    pas la somme. Le STT est chauffé sur le fil qui transcrira ensuite (par prudence avec MLX)."""
    bus = bus or EventBus()
    bus.publish("state", state="loading")
    executor = executor or build_executor(cfg)
    llm = load_llm(cfg)
    if cfg.tools.enabled:
        llm.tools = executor.schemas()
        server = server or start_server(executor, cfg.ui.port)
        if configure := getattr(llm.backends.get(CLAUDE), "configure_tools", None):
            configure(server.mcp_url, server.token)
    llm.check()

    def timed[T](label: str, build: Callable[[], T]) -> T:
        start = time.perf_counter()
        result = build()
        ms = round((time.perf_counter() - start) * 1000)
        LOG.info("  %-32s prêt en %5d ms", label, ms)
        bus.publish("loading", label=label, ms=ms)
        return result

    def warm_llm() -> Router:
        llm.warmup(system_prompt)
        return llm

    def warm_tts() -> TextToSpeech:
        tts = build_tts(cfg)
        tts.warmup()
        return tts

    def warm_stt() -> SpeechToText:
        from .system.apps import vocabulary
        stt = load_stt(cfg.stt)
        stt.set_vocabulary(vocabulary())
        stt.warmup()
        return stt

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="load") as pool:
        llm_future = pool.submit(timed, f"Moteur {llm.active} ({llm.model})", warm_llm)
        tts_future = pool.submit(timed, "Voix", warm_tts)
        ears_future = pool.submit(timed, "Mot d'activation", lambda: (build_wakeword(cfg), build_vad(cfg)))
        stt = timed("Transcription", warm_stt)
        wakeword, vad = ears_future.result()
        return Components(stt, llm_future.result(), tts_future.result(), wakeword, vad, executor, server)
