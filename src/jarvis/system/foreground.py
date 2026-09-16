"""Application et fenêtre au premier plan, suivies en continu (Windows et macOS).

Jarvis retient la dernière application qui n'est pas lui-même : quand tu cliques sur « Parler »
dans sa fenêtre, c'est l'application d'avant (navigateur, éditeur…) qui compte.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import IS_MAC, IS_WINDOWS

LOG = logging.getLogger("jarvis.foreground")

# Noms d'exécutables Windows → nom lisible (et commun avec macOS).
_WINDOWS_NAMES = {
    "chrome": "Google Chrome", "msedge": "Microsoft Edge", "brave": "Brave Browser", "firefox": "Firefox",
    "opera": "Opera", "vivaldi": "Vivaldi", "arc": "Arc", "code": "Visual Studio Code",
    "code - insiders": "Visual Studio Code - Insiders", "cursor": "Cursor", "windsurf": "Windsurf",
    "explorer": "Explorateur de fichiers", "winword": "Microsoft Word", "excel": "Microsoft Excel",
    "powerpnt": "Microsoft PowerPoint", "notepad": "Bloc-notes", "spotify": "Spotify", "discord": "Discord",
}
BROWSERS = {"Google Chrome", "Chromium", "Microsoft Edge", "Brave Browser", "Firefox", "Opera", "Vivaldi", "Arc",
            "Safari", "Norton Private Browser", "Zen Browser", "Firefox Developer Edition"}
EDITORS = {"Visual Studio Code", "Visual Studio Code - Insiders", "Code", "Cursor", "Windsurf", "VSCodium",
           "Zed", "Sublime Text", "PyCharm", "IntelliJ IDEA", "WebStorm", "Xcode"}


@dataclass(frozen=True)
class Foreground:
    app: str
    title: str
    pid: int | None
    at: float
    handle: int | None = None     # Windows : fenêtre (HWND), pour la lire et la piloter

    @property
    def is_browser(self) -> bool:
        return self.app in BROWSERS

    @property
    def is_editor(self) -> bool:
        return self.app in EDITORS


def probe() -> Foreground | None:
    """Fenêtre au premier plan, sans permission particulière (le titre peut être vide sur macOS
    sans l'autorisation d'enregistrement de l'écran)."""
    try:
        if IS_MAC:
            import Quartz

            options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
            for window in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
                if window.get("kCGWindowLayer") == 0 and window.get("kCGWindowOwnerName"):
                    return Foreground(str(window["kCGWindowOwnerName"]), str(window.get("kCGWindowName") or ""),
                                      int(window.get("kCGWindowOwnerPID") or 0) or None, time.time())
        elif IS_WINDOWS:
            import ctypes
            from ctypes import wintypes

            import psutil

            user32 = ctypes.windll.user32
            handle = user32.GetForegroundWindow()
            if not handle:
                return None
            buffer = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(handle) + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            exe = Path(psutil.Process(pid.value).name()).stem
            return Foreground(_WINDOWS_NAMES.get(exe.lower(), exe), buffer.value, pid.value, time.time(), int(handle))
    except Exception:  # noqa: BLE001 - indice de contexte, jamais bloquant
        return None
    return None


def frontmost_app() -> str:
    current = probe()
    return current.app if current else ""


class ForegroundTracker:
    def __init__(self, probe_fn: Callable[[], Foreground | None] = probe, interval: float = 1.0):
        self._probe = probe_fn
        self._interval = interval
        self._lock = threading.Lock()
        self._history: deque[Foreground] = deque(maxlen=20)   # applications distinctes, la plus récente à droite
        self._stopped = threading.Event()
        self.on_switch: Callable[[str], None] | None = None     # une autre application vient de passer devant

    def start(self) -> ForegroundTracker:
        threading.Thread(target=self._run, name="premier-plan", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            self.poll()

    def poll(self) -> None:
        current = self._probe()
        if current is None or current.pid == os.getpid():     # la fenêtre de Jarvis ne compte pas
            return
        switched = False
        with self._lock:
            if self._history and self._history[-1].app == current.app:
                self._history[-1] = current
            else:
                switched = bool(self._history)          # pas au tout premier relevé : rien n'a « changé »
                self._history = deque([f for f in self._history if f.app != current.app], maxlen=20)
                self._history.append(current)
        if switched and self.on_switch is not None:
            try:
                self.on_switch(current.app)
            except Exception:  # noqa: BLE001 - une automatisation ratée ne doit pas arrêter le suivi
                LOG.exception("Réaction au changement d'application en échec")

    def last(self) -> Foreground | None:
        with self._lock:
            return self._history[-1] if self._history else None

    def last_browser(self) -> Foreground | None:
        with self._lock:
            return next((f for f in reversed(self._history) if f.is_browser), None)

    def last_editor(self) -> Foreground | None:
        with self._lock:
            return next((f for f in reversed(self._history) if f.is_editor), None)

    def context(self) -> str:
        """« browser », « code », « app » (toute autre application) ou « » selon l'application utilisée juste avant
        de parler à Jarvis."""
        current = self.last()
        if current is None:
            return ""
        return "browser" if current.is_browser else "code" if current.is_editor else "app"
