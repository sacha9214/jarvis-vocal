"""Contexte écran : ce que l'utilisateur est en train de faire, observé en continu.

Toutes les ~45 s, et seulement si l'écran a changé, une capture réduite est décrite en une
phrase par le modèle de vision LOCAL (Ollama). La capture reste en mémoire : jamais écrite
sur disque, jamais envoyée ailleurs. Seule la phrase sert de contexte, y compris pour Claude.
Pendant une conversation, l'analyse se met en pause pour ne pas ralentir les réponses.

Le nom de l'application au premier plan, lu par le système, est donné au modèle : sans cet
ancrage, un petit modèle de vision invente volontiers (« une discussion Discord » devant Claude).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..config import Config, ScreenConfig

LOG = logging.getLogger("jarvis.screen")

_ANCHOR = "L'application au premier plan est « {app} »."
_UNKNOWN_ANCHOR = "L'application au premier plan n'est pas connue."
DESCRIBE_PROMPT = ("{anchor} En une phrase courte, décris ce que l'utilisateur est en train de faire, uniquement "
                   "d'après ce qui est visible. N'invente rien : si ce n'est pas lisible, dis seulement quelle "
                   "application est ouverte. Pas de détail personnel, ne recopie pas de texte.")
ANSWER_PROMPT = ("{anchor} Voici l'écran de l'utilisateur. Réponds en français, en deux ou trois phrases courtes "
                 "qui seront lues à voix haute, sans markdown ni liste, uniquement d'après ce qui est visible ; "
                 "si tu ne peux pas le lire, dis-le. Question : {question}")
_UNCHANGED = 2.0   # écart moyen (sur 255) de l'empreinte en dessous duquel l'écran n'a pas bougé

Describer = Callable[[bytes, str], str]


def describe_prompt(app: str) -> str:
    return DESCRIBE_PROMPT.format(anchor=_ANCHOR.format(app=app) if app else _UNKNOWN_ANCHOR)


def answer_prompt(app: str, question: str) -> str:
    return ANSWER_PROMPT.format(anchor=_ANCHOR.format(app=app) if app else _UNKNOWN_ANCHOR, question=question)


@dataclass(frozen=True)
class Observation:
    text: str
    app: str
    at: float


def pick_monitor(monitors: list[dict]) -> dict:
    """L'écran principal ; sans écran utilisable (session verrouillée, Mac en veille), une erreur claire
    plutôt qu'un IndexError."""
    usable = [m for m in monitors[1:] or monitors if m.get("width", 0) > 0 and m.get("height", 0) > 0]
    if not usable:
        raise RuntimeError("aucun écran capturable pour le moment (session verrouillée ou écran en veille)")
    return usable[0]


def memory_pressure(min_free_gb: float) -> bool:
    """Vrai quand la mémoire libre passe sous le seuil : analyser l'écran ferait swapper."""
    try:
        import psutil
        return psutil.virtual_memory().available < min_free_gb * 1e9
    except Exception:  # noqa: BLE001 - psutil absent ou en échec : on ne bloque pas
        return False


def capture(max_width: int) -> tuple[bytes, np.ndarray]:
    """Écran principal réduit à `max_width`, en PNG, et son empreinte 18×32 en niveaux de gris."""
    import mss
    import mss.tools

    with mss.mss() as grabber:
        shot = grabber.grab(pick_monitor(grabber.monitors))
    pixels = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
    step = max(1, int(np.ceil(shot.width / max_width)))
    small = np.ascontiguousarray(pixels[::step, ::step, 2::-1])          # BGRA → RGB, sous-échantillonné
    png = mss.tools.to_png(small.tobytes(), (small.shape[1], small.shape[0]))
    gray = small.mean(axis=2)
    rows, cols = max(1, gray.shape[0] // 18), max(1, gray.shape[1] // 32)
    fingerprint = gray[:rows * 18, :cols * 32].reshape(18, rows, 32, cols).mean(axis=(1, 3))
    return png, fingerprint


def frontmost_app() -> str:
    """Application (macOS) ou fenêtre (Windows) au premier plan, sans permission particulière."""
    try:
        if sys.platform == "darwin":
            import Quartz

            options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
            for window in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
                if window.get("kCGWindowLayer") == 0 and window.get("kCGWindowOwnerName"):
                    return str(window["kCGWindowOwnerName"])
        elif sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            buffer = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(handle) + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            return buffer.value
    except Exception:  # noqa: BLE001 - simple indice de contexte, jamais bloquant
        return ""
    return ""


def ollama_describer(cfg: Config) -> Describer:
    import httpx
    import ollama

    client = ollama.Client(host=cfg.llm.host, timeout=httpx.Timeout(120.0, connect=3.0))

    def describe(image: bytes, prompt: str) -> str:
        response = client.chat(
            model=cfg.screen.model or cfg.llm.model,
            messages=[{"role": "user", "content": prompt, "images": [image]}],
            think=False, keep_alive=cfg.llm.keep_alive,
            options={"num_ctx": 4096, "num_predict": 110, "temperature": 0.1},
        )
        return (response.message.content or "").strip()
    return describe


class ScreenWatcher:
    def __init__(self, cfg: ScreenConfig, describe: Describer,
                 on_observation: Callable[[Observation], None] | None = None,
                 busy: Callable[[], bool] = lambda: False,
                 grab: Callable[[int], tuple[bytes, np.ndarray]] = capture,
                 front: Callable[[], str] = frontmost_app,
                 pressure: Callable[[float], bool] = memory_pressure):
        self.cfg = cfg
        self.describe = describe
        self.on_observation = on_observation
        self.busy = busy
        self.grab = grab
        self.front = front
        self.pressure = pressure
        self._paused = False
        self.latest: Observation | None = None
        self._fingerprint: np.ndarray | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._failures = 0

    def start(self) -> ScreenWatcher:
        threading.Thread(target=self._run, name="ecran", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()

    def refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stopped.is_set():
            self._wake.wait(self.cfg.interval_s * (2 ** min(self._failures, 4)))
            self._wake.clear()
            if self._stopped.is_set():
                return
            if not self.cfg.enabled or self.busy():
                continue
            if self.pressure(self.cfg.min_free_gb):
                if not self._paused:
                    LOG.info("Analyse d'écran en pause : moins de %.1f Go de mémoire libre.", self.cfg.min_free_gb)
                self._paused = True
                continue
            self._paused = False
            try:
                self.observe()
                self._failures = 0
            except Exception as exc:  # noqa: BLE001 - permission refusée, Ollama absent… on espace les essais
                self._failures += 1
                LOG.warning("Analyse d'écran impossible : %s", exc)

    def observe(self, force: bool = False) -> Observation:
        image, fingerprint = self.grab(self.cfg.max_width)
        app = self.front()
        with self._lock:
            previous, old = self.latest, self._fingerprint
        unchanged = (previous is not None and old is not None and previous.app == app
                     and float(np.mean(np.abs(fingerprint - old))) < _UNCHANGED)
        if unchanged and not force:
            observation = Observation(previous.text, app, time.time())   # toujours d'actualité
            with self._lock:
                self.latest = observation
            return observation
        observation = Observation(self.describe(image, describe_prompt(app)), app, time.time())
        with self._lock:
            self.latest, self._fingerprint = observation, fingerprint
        LOG.debug("Écran : %s", observation.text)
        if self.on_observation:
            self.on_observation(observation)
        return observation

    def context(self) -> str | None:
        """Phrase de contexte pour le modèle, ou None si l'observation est trop ancienne."""
        observation = self.latest
        if not self.cfg.enabled or observation is None:
            return None
        age = time.time() - observation.at
        if age > max(90.0, self.cfg.interval_s * 3):
            return None
        app = f", application au premier plan : {observation.app}" if observation.app else ""
        return f"Contexte observé sur l'écran de l'utilisateur il y a {int(age)} s{app} : {observation.text}"

    def look(self, question: str = "") -> str:
        """Regard immédiat, pour une question sur l'écran (« c'est quoi cette erreur ? »)."""
        image, _ = self.grab(self.cfg.max_width)
        app = self.front()
        return self.describe(image, answer_prompt(app, question) if question else describe_prompt(app))
