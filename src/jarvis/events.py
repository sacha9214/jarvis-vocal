"""Bus d'événements : le pipeline publie, l'interface écoute, sans dépendre l'un de l'autre."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger("jarvis.events")
Event = dict[str, Any]
_STICKY = ("state", "engine")   # rejoués à chaque nouvel abonné


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[Event], None]] = []
        self._last: dict[str, Event] = {}

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)
            replay = list(self._last.values())
        for event in replay:
            callback(event)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    def publish(self, kind: str, **data: Any) -> None:
        event = {"type": kind, "time": time.time(), **data}
        with self._lock:
            if kind in _STICKY:
                self._last[kind] = event
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - un abonné défaillant ne doit pas casser la voix
                LOG.exception("Abonné en échec pour l'événement %s", kind)
