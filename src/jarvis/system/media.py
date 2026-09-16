"""Lecture en cours : play, pause, suivant, précédent.

macOS : AppleScript vers Spotify ou Musique (les touches multimédia système demandent une
permission d'accessibilité). Windows : touches multimédia virtuelles, qui pilotent le
lecteur actif quel qu'il soit.
"""
from __future__ import annotations

import ctypes
import time

from . import IS_MAC, IS_WINDOWS, applescript_string, osascript, unsupported

ACTIONS = ("play", "pause", "next", "previous")
_MAC_PLAYERS = ("Spotify", "Music")
_MAC_COMMANDS = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
_WINDOWS_KEYS = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1}
_SPOKEN = {"play": "Je relance la musique.", "pause": "Je mets en pause.", "next": "Je passe au morceau suivant.",
           "previous": "Je reviens au morceau précédent."}


def press_key(code: int, times: int = 1) -> None:
    for _ in range(times):
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)       # type: ignore[attr-defined]
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)       # type: ignore[attr-defined]
        time.sleep(0.01)


def control(action: str) -> str:
    if action not in ACTIONS:
        return f"Action de lecture inconnue : {action}."
    if IS_MAC:
        running = [p for p in _MAC_PLAYERS if osascript(f"application {applescript_string(p)} is running") == "true"]
        if not running:
            return "Aucun lecteur de musique n'est ouvert."
        osascript(f"tell application {applescript_string(running[0])} to {_MAC_COMMANDS[action]}")
        return _SPOKEN[action]
    if IS_WINDOWS:
        press_key(_WINDOWS_KEYS[action])
        return _SPOKEN[action]
    raise unsupported("Le contrôle de la lecture")
