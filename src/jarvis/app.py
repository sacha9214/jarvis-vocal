"""Assemblage des composants à partir de la config (partagé par run, bench et doctor)."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from . import assets, custom
from . import config as config_module
from .audio.vad import SileroVad
from .audio.wakeword import WakeWord
from .browser import install as browser_install
from .browser.bridge import start_bridge
from .browser.controller import BrowserController
from .config import Config
from .desktop import DesktopController
from .editor import EditorController
from .events import EventBus
from .llm import load_llm
from .llm.router import CLAUDE, Router
from .review import ReviewManager
from .server import LocalServer, start_server
from .stt import SpeechToText, load_stt
from .system.foreground import ForegroundTracker
from .tools import ToolExecutor, builtin
from .tts import TextToSpeech
from .vision.screen import ScreenWatcher, ollama_describer

LOG = logging.getLogger("jarvis")


def build_tts(cfg: Config) -> TextToSpeech:
    """Voix prête à parler (chargée et chauffée) : Pocket TTS si possible, sinon Piper."""
    backend = cfg.tts.backend
    if backend not in ("auto", "pocket", "piper"):
        raise ValueError(f"Moteur de voix inconnu : {backend} (auto, pocket, piper)")
    if backend in ("auto", "pocket"):
        try:
            from .tts.pocket_backend import PocketTTS
            tts: TextToSpeech = PocketTTS(cfg.tts.voice, temperature=cfg.tts.temperature)
            tts.warmup()
            return tts
        except Exception as exc:  # noqa: BLE001 - modèle absent, voix inconnue, machine trop lente…
            if backend == "pocket":
                raise RuntimeError(f"Voix Pocket TTS indisponible : {exc}") from exc
            LOG.warning("Voix Pocket TTS indisponible (%s) : voix Piper à la place.", exc)
    from .tts.piper_backend import PiperTTS
    tts = PiperTTS(assets.piper_voice(cfg.tts.piper_voice), cfg.tts.length_scale)
    tts.warmup()
    return tts


def build_wakeword(cfg: Config) -> WakeWord:
    return WakeWord(*assets.wakeword(cfg.wakeword.model), threshold=cfg.wakeword.threshold)


def build_vad(cfg: Config) -> SileroVad:
    return SileroVad(assets.ensure(assets.SILERO_VAD))


def build_executor(cfg: Config) -> ToolExecutor:
    def remember(name: str) -> None:
        path = config_module.update_file({"tools": {"always_allow": list(cfg.tools.always_allow)}})
        LOG.info("« %s » est désormais autorisé sans confirmation (enregistré dans %s).", name, path)
    matcher = custom.setup(cfg)          # avant tout : les commandes personnalisées sont aussi des outils
    executor = ToolExecutor(cfg.tools, on_always=remember)
    executor.custom = matcher
    return executor


def claude_task(cfg: Config, llm: Router) -> Callable[[str, str, list[str]], str]:
    def run(request: str, system: str, args: list[str]) -> str:
        backend = llm.backends.get(CLAUDE)
        if backend is None or not hasattr(backend, "run_task"):
            raise RuntimeError("Claude Code n'est pas disponible")
        return backend.run_task(request, system, args, model=cfg.review.claude_model, timeout=cfg.review.timeout_s)
    return run


@dataclass
class Components:
    stt: SpeechToText
    llm: Router
    tts: TextToSpeech
    wakeword: WakeWord
    vad: SileroVad
    executor: ToolExecutor
    server: LocalServer | None = None
    screen: ScreenWatcher | None = None
    foreground: ForegroundTracker | None = None
    browser: BrowserController | None = None
    review: ReviewManager | None = None
    desktop: DesktopController | None = None
    editor: EditorController | None = None


def load_all(cfg: Config, system_prompt: str, bus: EventBus | None = None, executor: ToolExecutor | None = None,
             server: LocalServer | None = None) -> Components:
    """Charge et chauffe tout en parallèle : le démarrage prend le temps du plus lent,
    pas la somme. Le STT est chauffé sur le fil qui transcrira ensuite (par prudence avec MLX)."""
    bus = bus or EventBus()
    bus.publish("state", state="loading")
    executor = executor or build_executor(cfg)
    llm = load_llm(cfg)
    llm.free_local_memory = cfg.llm.free_on_claude
    if cfg.tools.enabled:
        llm.tools = executor.schemas()
        server = server or start_server(executor, cfg.ui.port)
        if configure := getattr(llm.backends.get(CLAUDE), "configure_tools", None):
            configure(server.mcp_url, server.token)
    llm.check()
    # Toujours créé (activable en direct depuis l'interface) ; il ne capture que si screen.enabled.
    screen = ScreenWatcher(cfg.screen, ollama_describer(cfg))
    builtin.SCREEN = screen
    foreground = ForegroundTracker().start()
    browser = editor = None
    if cfg.browser.enabled:
        bridge = start_bridge(browser_install.token(), cfg.browser.port,
                              on_change=lambda kind, names: bus.publish(f"{kind}s", names=names))
        browser = BrowserController(bridge, foreground)
        editor = EditorController(bridge)
    builtin.BROWSER = browser
    builtin.EDITOR = editor
    review = ReviewManager(cfg, engine=lambda: llm.active, window=lambda: foreground.last_editor() or foreground.last(),
                           bus=bus, claude=claude_task(cfg, llm), hint=editor.current if editor else None)
    builtin.REVIEW = review
    desktop = DesktopController(foreground)     # UI Automation ou AX chargés au premier usage
    builtin.DESKTOP = desktop

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

    def warm_stt() -> SpeechToText:
        from .system.apps import vocabulary
        stt = load_stt(cfg.stt)
        stt.set_vocabulary(vocabulary())
        stt.warmup()
        return stt

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="load") as pool:
        llm_future = pool.submit(timed, f"Moteur {llm.active} ({llm.model})", warm_llm)
        tts_future = pool.submit(timed, "Voix", lambda: build_tts(cfg))
        ears_future = pool.submit(timed, "Mot d'activation", lambda: (build_wakeword(cfg), build_vad(cfg)))
        stt = timed("Transcription", warm_stt)
        wakeword, vad = ears_future.result()
        return Components(stt, llm_future.result(), tts_future.result(), wakeword, vad, executor, server, screen,
                          foreground, browser, review, desktop, editor)
