"""Alimentation : verrouillage, veille, extinction et redémarrage (programmés, annulables)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from . import IS_MAC, IS_WINDOWS, run, unsupported
from .timers import spoken_duration

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
MAX_DELAY_S = 24 * 3600     # au-delà d'une journée, c'est sûrement une erreur de compréhension


def execute(action: str) -> None:
    commands = _MAC if IS_MAC else _WINDOWS if IS_WINDOWS else None
    if commands is None:
        raise unsupported("La gestion de l'alimentation")
    completed = run(commands[action], timeout=15)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{action} refusé par le système")


WARN_BEFORE_S = 60      # rappel à voix haute avant une action programmée de loin
MIN_WARNED_DELAY_S = 150   # en dessous, le rappel tomberait presque en même temps que l'action


class PowerScheduler:
    """Une seule action programmée à la fois, avec un délai pour dire « annule »."""

    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._warning: threading.Timer | None = None
        self._action: str | None = None
        self._at: float = 0.0
        self._lock = threading.Lock()
        self.announce: Callable[[str], None] = lambda text: None

    def schedule(self, action: str, delay_s: float) -> str:
        if action not in SPOKEN:
            return f"Action d'alimentation inconnue : {action}."
        delay_s = max(1.0, min(float(delay_s), MAX_DELAY_S))
        with self._lock:
            self._cancel_timers()
            self._action, self._at = action, time.time() + delay_s
            self._timer = self._start_timer(delay_s, self._fire, action)
            if delay_s >= MIN_WARNED_DELAY_S:
                self._warning = self._start_timer(delay_s - WARN_BEFORE_S, self._warn, action)
        return (f"C'est parti pour {SPOKEN[action]} dans {spoken_duration(delay_s)}. "
                f"Dis « annule l'extinction » quand tu veux pour arrêter.")

    @staticmethod
    def _start_timer(delay_s: float, target: Callable[[str], None], action: str) -> threading.Timer:
        timer = threading.Timer(delay_s, target, args=(action,))
        timer.daemon = True
        timer.start()
        return timer

    def _cancel_timers(self) -> None:
        for timer in (self._timer, self._warning):
            if timer:
                timer.cancel()
        self._timer = self._warning = None

    def pending(self) -> tuple[str, float] | None:
        """(action, secondes restantes) si quelque chose est programmé."""
        with self._lock:
            if not self._timer or not self._action:
                return None
            return self._action, max(0.0, self._at - time.time())

    def status(self) -> str:
        waiting = self.pending()
        if waiting is None:
            return "Rien n'est programmé pour le moment."
        action, remaining = waiting
        return f"{SPOKEN[action].capitalize()} de l'ordinateur dans {spoken_duration(remaining)}."

    def cancel(self) -> str:
        with self._lock:
            if not self._timer:
                return "Aucune extinction n'est programmée."
            action = self._action
            self._cancel_timers()
            self._action, self._at = None, 0.0
        return f"J'annule {SPOKEN.get(action or '', 'l action')}."

    def _warn(self, action: str) -> None:
        if self.pending() is not None:
            self.announce(f"Attention, {SPOKEN[action]} de l'ordinateur dans une minute. "
                          "Dis « annule » si tu veux continuer.")

    def _fire(self, action: str) -> None:
        with self._lock:
            self._cancel_timers()
            self._action, self._at = None, 0.0
        try:
            execute(action)
        except Exception as exc:  # noqa: BLE001 - on prévient à voix haute plutôt que d'échouer en silence
            self.announce(f"Je n'ai pas pu lancer {SPOKEN[action]} : {exc}")
