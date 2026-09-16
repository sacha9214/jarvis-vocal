"""Fenêtres ouvertes : les lister, passer de l'une à l'autre, les ranger.

macOS : la liste vient de Quartz, qui ne demande aucune autorisation, et l'activation de `open -a`.
Windows : l'API des fenêtres (user32), comme la barre des tâches. Réduire, agrandir ou fermer passent
par un raccourci clavier sur macOS (l'accessibilité s'en charge) et directement par l'API sous Windows.
"""
from __future__ import annotations

import ctypes
import subprocess
import unicodedata
from dataclasses import dataclass

from . import IS_MAC, IS_WINDOWS, unsupported

MAX_WINDOWS = 20
# Fenêtres techniques qu'on ne propose jamais.
_HIDE = {"Window Server", "Dock", "SystemUIServer", "Spotlight", "Notification Center", "Control Center",
         "Contrôle", "Item-0", "Program Manager", "Windows Input Experience", "Paramètres", "Search"}
ACTIONS = ("minimize", "maximize", "fullscreen", "close", "left", "right")
SPOKEN = {"minimize": "J'ai réduit la fenêtre.", "maximize": "J'ai agrandi la fenêtre.",
          "fullscreen": "Fenêtre en plein écran.", "close": "J'ai fermé la fenêtre.",
          "left": "Fenêtre rangée à gauche.", "right": "Fenêtre rangée à droite."}
# Raccourcis équivalents, pour macOS (Cmd est écrit « ctrl » : le module desktop fait la traduction).
MAC_KEYS = {"minimize": "ctrl+m", "maximize": "ctrl+alt+shift+plein", "fullscreen": "ctrl+alt+f",
            "close": "ctrl+w"}


@dataclass(frozen=True)
class Window:
    app: str
    title: str
    handle: int | None = None


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()


def list_windows() -> list[Window]:
    """Applications qui ont une fenêtre à l'écran, la plus en avant d'abord."""
    windows: list[Window] = []
    if IS_MAC:
        import Quartz

        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        for info in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
            owner = str(info.get("kCGWindowOwnerName") or "")
            if info.get("kCGWindowLayer") != 0 or not owner or owner in _HIDE:
                continue
            windows.append(Window(owner, str(info.get("kCGWindowName") or "")))
    elif IS_WINDOWS:
        user32 = ctypes.windll.user32
        collected: list[Window] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def visit(handle, _):
            if not user32.IsWindowVisible(handle):
                return True
            length = user32.GetWindowTextLengthW(handle)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            title = buffer.value.strip()
            if title and title not in _HIDE:
                collected.append(Window(title, title, int(handle)))
            return True

        user32.EnumWindows(visit, 0)
        windows = collected
    else:
        raise unsupported("La liste des fenêtres")
    seen: set[str] = set()
    unique = []
    for window in windows:
        if window.app in seen:
            continue
        seen.add(window.app)
        unique.append(window)
    return unique[:MAX_WINDOWS]


def find(name: str, windows: list[Window] | None = None) -> Window | None:
    """La fenêtre dont le nom d'application ou le titre ressemble le plus à `name`."""
    wanted = soft(name)
    if not wanted:
        return None
    candidates = windows if windows is not None else list_windows()
    best, score = None, 0.0
    for window in candidates:
        app, title = soft(window.app), soft(window.title)
        if app == wanted:
            return window
        value = (90.0 if wanted in app or app in wanted else
                 60.0 if wanted in title else 0.0)
        if value > score:
            best, score = window, value
    return best


def activate(window: Window) -> None:
    """Met la fenêtre au premier plan."""
    if IS_MAC:
        subprocess.Popen(["open", "-a", window.app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_WINDOWS:
        user32 = ctypes.windll.user32
        if window.handle is None:
            raise RuntimeError("fenêtre sans identifiant")
        if user32.IsIconic(window.handle):
            user32.ShowWindow(window.handle, 9)          # SW_RESTORE
        user32.SetForegroundWindow(window.handle)
    else:
        raise unsupported("Le changement de fenêtre")


def act(action: str, window: Window | None = None) -> None:
    """Réduit, agrandit, met en plein écran, ferme ou range la fenêtre au premier plan."""
    if action not in ACTIONS:
        raise ValueError(f"Action de fenêtre inconnue : {action}")
    if IS_WINDOWS:
        user32 = ctypes.windll.user32
        handle = window.handle if window and window.handle else user32.GetForegroundWindow()
        if not handle:
            raise RuntimeError("aucune fenêtre au premier plan")
        if action == "close":
            user32.PostMessageW(handle, 0x0010, 0, 0)    # WM_CLOSE : l'application peut demander à enregistrer
        elif action == "minimize":
            user32.ShowWindow(handle, 6)                 # SW_MINIMIZE
        elif action in ("maximize", "fullscreen"):
            user32.ShowWindow(handle, 3)                 # SW_MAXIMIZE
        else:
            _windows_snap(action)
        return
    if not IS_MAC:
        raise unsupported("La gestion des fenêtres")
    raise RuntimeError("mac-keys")      # traité par l'appelant, qui a accès au clavier (module desktop)


def _windows_snap(side: str) -> None:
    """Windows : Win+Flèche range la fenêtre sur la moitié de l'écran."""
    user32 = ctypes.windll.user32
    win, arrow = 0x5B, 0x25 if side == "left" else 0x27
    for key in (win, arrow):
        user32.keybd_event(key, 0, 0, 0)
    for key in (arrow, win):
        user32.keybd_event(key, 0, 2, 0)                 # KEYEVENTF_KEYUP


def running_apps() -> list[str]:
    """Applications ouvertes, pour dire ce qui tourne."""
    return [window.app for window in list_windows()]


def quit_frontmost() -> None:
    if IS_WINDOWS:
        act("close")
    else:
        raise RuntimeError("mac-keys")


__all__ = ["ACTIONS", "MAC_KEYS", "SPOKEN", "Window", "act", "activate", "find", "list_windows",
           "quit_frontmost", "running_apps", "soft"]
