"""Raccourci clavier global : réveiller Jarvis sans parler, depuis n'importe quelle application.

Windows : RegisterHotKey, l'API qu'utilisent les lanceurs d'applications ; aucune autorisation, et la
touche n'est prise qu'une fois (pas de répétition si on la garde enfoncée).
macOS : un « event tap » Quartz en simple écoute, qui ne retient ni ne modifie aucune touche. macOS
demande l'autorisation « Surveillance de l'entrée » ; sans elle, Jarvis le dit et continue sans raccourci.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable

from . import IS_MAC, IS_WINDOWS

LOG = logging.getLogger("jarvis.hotkey")
DEFAULT = "ctrl+alt+j"
MAC_PERMISSION = ("macOS n'autorise pas encore Jarvis à écouter le clavier : Réglages Système, Confidentialité et "
                  "sécurité, Surveillance de l'entrée, puis active ton terminal.")

# Windows : codes des modificateurs et des touches
_MOD = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012
_WIN_KEYS = {"space": 0x20, "enter": 0x0D, "escape": 0x1B, "tab": 0x09,
             **{f"f{n}": 0x6F + n for n in range(1, 13)}}
# macOS : codes de touches physiques (clavier AZERTY ou QWERTY : la position, pas la lettre gravée)
_MAC_KEYS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
             "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
             "k": 40, "n": 45, "m": 46, "space": 49, "enter": 36, "escape": 53, "tab": 48,
             **{f"f{n}": code for n, code in zip(range(1, 13), (122, 120, 99, 118, 96, 97, 98, 100, 101, 109,
                                                                  103, 111), strict=True)}}
# macOS : masques des modificateurs dans les drapeaux d'un événement Quartz
_MAC_FLAGS = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "cmd": 1 << 20}


def parse(combo: str) -> tuple[list[str], str]:
    """« ctrl+alt+j » → (["ctrl", "alt"], "j"). Lève ValueError si ce n'est pas un raccourci valide."""
    parts = [part.strip().lower() for part in combo.replace(" ", "+").split("+") if part.strip()]
    aliases = {"control": "ctrl", "option": "alt", "opt": "alt", "command": "cmd", "super": "win",
               "espace": "space", "entree": "enter", "echap": "escape"}
    parts = [aliases.get(part, part) for part in parts]
    modifiers = [part for part in parts if part in ("ctrl", "alt", "shift", "win", "cmd")]
    keys = [part for part in parts if part not in modifiers]
    if len(keys) != 1 or not modifiers:
        raise ValueError(f"raccourci « {combo} » : il faut au moins un modificateur (ctrl, alt, shift) et une touche")
    key = keys[0]
    if not (len(key) == 1 and key.isalnum()) and key not in _WIN_KEYS:
        raise ValueError(f"raccourci « {combo} » : touche « {key} » inconnue")
    return modifiers, key


class Hotkey:
    def __init__(self, combo: str, on_press: Callable[[], None]):
        self.modifiers, self.key = parse(combo)
        self.combo = combo
        self.on_press = on_press
        self.active = False
        self.error = ""
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._run_loop = None
        self._ready = threading.Event()

    def start(self, timeout: float = 5.0) -> Hotkey:
        target = self._windows if IS_WINDOWS else self._mac if IS_MAC else None
        if target is None:
            self.error = "raccourci clavier global non pris en charge sur ce système"
            return self
        self._thread = threading.Thread(target=target, name="raccourci", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self.active:
            LOG.info("Raccourci %s : réveille Jarvis sans parler.", self.combo)
        elif self.error:
            LOG.warning("Raccourci %s indisponible : %s", self.combo, self.error)
        return self

    def stop(self) -> None:
        if IS_WINDOWS and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        elif IS_MAC and self._run_loop is not None:
            import Quartz
            Quartz.CFRunLoopStop(self._run_loop)
        self.active = False

    def _fire(self) -> None:
        try:
            self.on_press()
        except Exception:  # noqa: BLE001 - un raccourci ne doit jamais faire tomber son écoute
            LOG.exception("Raccourci : réaction en échec")

    # -- Windows

    def _windows(self) -> None:
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        flags = _MOD_NOREPEAT
        for modifier in self.modifiers:
            if modifier == "cmd":
                modifier = "win"
            flags |= _MOD[modifier]
        vk = _WIN_KEYS.get(self.key) or ord(self.key.upper())
        if not user32.RegisterHotKey(None, 1, flags, vk):
            self.error = f"{self.combo} est déjà pris par une autre application ; choisis-en un autre dans les réglages"
            self._ready.set()
            return
        self.active = True
        self._ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == _WM_HOTKEY:
                    self._fire()
        finally:
            user32.UnregisterHotKey(None, 1)
            self.active = False

    # -- macOS

    def _mac(self) -> None:
        import Quartz

        wanted_key = _MAC_KEYS.get(self.key)
        if wanted_key is None:
            self.error = f"touche « {self.key} » non prise en charge sur Mac"
            self._ready.set()
            return
        wanted = 0
        for modifier in self.modifiers:
            wanted |= _MAC_FLAGS["cmd" if modifier == "win" else modifier]
        relevant = sum(_MAC_FLAGS.values())

        def callback(proxy, event_type, event, refcon):
            if event_type == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
                flags = Quartz.CGEventGetFlags(event) & relevant
                if code == wanted_key and flags == wanted and not repeat:
                    threading.Thread(target=self._fire, name="raccourci-action", daemon=True).start()
            elif event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(tap, True)          # macOS coupe un tap trop lent : on le relance
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                      Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
        if tap is None:
            self.error = MAC_PERMISSION
            self._ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.active = True
        self._ready.set()
        Quartz.CFRunLoopRun()
        self.active = False
