"""Alimentation : verrouillage, veille, extinction et redémarrage (programmés, annulables)."""
from __future__ import annotations

import threading
from collections.abc import Callable

from . import IS_MAC, IS_WINDOWS, run, unsupported

_MAC = {
    "shutdown": ["osascript", "-e", 'tell application "System Events" to shut down'],
    "restart": ["osascript", "-e", 'tell application "System Events" to restart'],
    "sleep": ["pmset", "sleepnow"],
    "lock": ["pmset", "displaysleepnow"],   # verrouille si le Mac exige le mot de passe dès la veille
}
_WINDOWS = {
    "shutdown": ["shutdown", "/s", "/t", "0"],
    "restart": ["shutdown", "/r", "/t", "0"],
    "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
    "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
}
SPOKEN = {"shutdown": "l'extinction", "restart": "le redémarrage", "sleep": "la mise en veille"}


def execute(action: str) -> None:
    commands = _MAC if IS_MAC else _WINDOWS if IS_WINDOWS else None
    if commands is None:
        raise unsupported("La gestion de l'alimentation")
    completed = run(commands[action], timeout=15)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{action} refusé par le système")


class PowerScheduler:
    """Une seule action programmée à la fois, avec un délai pour dire « annule »."""

    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._action: str | None = None
        self._lock = threading.Lock()
        self.announce: Callable[[str], None] = lambda text: None

    def schedule(self, action: str, delay_s: int) -> str:
        if action not in SPOKEN:
            return f"Action d'alimentation inconnue : {action}."
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._action = action
            self._timer = threading.Timer(delay_s, self._fire, args=(action,))
            self._timer.daemon = True
            self._timer.start()
        return f"C'est parti pour {SPOKEN[action]} dans {delay_s} secondes. Dis « annule l'extinction » pour arrêter."

    def cancel(self) -> str:
        with self._lock:
            if not self._timer:
                return "Aucune extinction n'est programmée."
            self._timer.cancel()
            action, self._timer, self._action = self._action, None, None
        return f"J'annule {SPOKEN.get(action or '', 'l action')}."

    def _fire(self, action: str) -> None:
        with self._lock:
            self._timer, self._action = None, None
        try:
            execute(action)
        except Exception as exc:  # noqa: BLE001 - on prévient à voix haute plutôt que d'échouer en silence
            self.announce(f"Je n'ai pas pu lancer {SPOKEN[action]} : {exc}")
