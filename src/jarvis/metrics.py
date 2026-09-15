"""Chronométrage d'un tour de parole : où part chaque milliseconde."""
from __future__ import annotations

import time


class TurnTimer:
    """Horloge démarrée à la fin de ta phrase ; chaque étape n'est notée qu'une fois."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks.setdefault(name, time.perf_counter() - self._start)

    def ms(self, name: str) -> float | None:
        value = self.marks.get(name)
        return None if value is None else value * 1000

    def summary(self) -> str:
        return " | ".join(f"{name} {seconds * 1000:.0f} ms" for name, seconds in self.marks.items())
