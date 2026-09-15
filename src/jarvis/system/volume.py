"""Volume du son. macOS : AppleScript (niveau exact). Windows : touches volume virtuelles
(pas de dépendance COM ; chaque appui vaut 2 %)."""
from __future__ import annotations

from . import IS_MAC, IS_WINDOWS, osascript, unsupported
from .media import press_key

_VK_MUTE, _VK_DOWN, _VK_UP = 0xAD, 0xAE, 0xAF


def get_volume() -> int | None:
    if IS_MAC:
        return int(osascript("output volume of (get volume settings)") or 0)
    return None


def set_volume(level: int | None = None, change: int | None = None, mute: bool | None = None) -> str:
    if IS_MAC:
        if mute is not None:
            osascript(f"set volume output muted {'true' if mute else 'false'}")
            return "Son coupé." if mute else "Son rétabli."
        current = get_volume() or 0
        target = max(0, min(100, level if level is not None else current + (change or 0)))
        osascript(f"set volume output volume {target}")
        if target > 0 and osascript("output muted of (get volume settings)") == "true":
            osascript("set volume output muted false")
        return f"Volume à {target} pour cent."
    if IS_WINDOWS:
        if mute is not None:
            press_key(_VK_MUTE)
            return "Son coupé." if mute else "Son rétabli."
        if level is not None:
            target = max(0, min(100, level))
            press_key(_VK_DOWN, 50)                 # butée à 0, puis montée au niveau voulu
            press_key(_VK_UP, round(target / 2))
            return f"Volume à {target} pour cent."
        step = change or 0
        press_key(_VK_UP if step > 0 else _VK_DOWN, max(1, round(abs(step) / 2)))
        return "J'augmente le son." if step > 0 else "Je baisse le son."
    raise unsupported("Le réglage du volume")
