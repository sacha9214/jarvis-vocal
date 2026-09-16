"""Minuteurs : annoncés à voix haute quand ils sonnent."""
from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from typing import Any


def spoken_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} heure" + ("s" if hours > 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes > 1 else ""))
    if secs:
        parts.append(f"{secs} seconde" + ("s" if secs > 1 else ""))
    return " ".join(parts) or "0 seconde"


class Timers:
    def __init__(self) -> None:
        self._items: dict[int, tuple[threading.Timer, str, float]] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self.announce: Callable[[str], None] = lambda text: None

    def start(self, seconds: int, label: str = "") -> str:
        seconds = int(seconds)
        if not 0 < seconds <= 24 * 3600:
            return "Un minuteur doit durer entre une seconde et vingt-quatre heures."
        timer_id = next(self._ids)
        timer = threading.Timer(seconds, self._ring, args=(timer_id,))
        timer.daemon = True
        with self._lock:
            self._items[timer_id] = (timer, label, time.time() + seconds)
        timer.start()
        return f"Minuteur de {spoken_duration(seconds)} lancé" + (f" pour {label}." if label else ".")

    def cancel_all(self) -> str:
        with self._lock:
            items, self._items = self._items, {}
        for timer, _, _ in items.values():
            timer.cancel()
        if not items:
            return "Aucun minuteur en cours."
        return "J'ai annulé le minuteur." if len(items) == 1 else f"{len(items)} minuteurs annulés."

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": i, "label": label, "ends_at": ends} for i, (_, label, ends) in self._items.items()]

    def _ring(self, timer_id: int) -> None:
        with self._lock:
            item = self._items.pop(timer_id, None)
        if item:
            label = item[1]
            self.announce("Minuteur terminé" + (f" : {label}." if label else "."))
