"""Choix du navigateur à piloter : celui utilisé en dernier (extension, ou Safari sur macOS)."""
from __future__ import annotations

from typing import Any

from ..system import IS_MAC
from ..system.foreground import ForegroundTracker
from . import safari
from .bridge import BrowserBridge, BrowserUnavailable


class BrowserController:
    def __init__(self, bridge: BrowserBridge | None, foreground: ForegroundTracker | None):
        self.bridge = bridge
        self.foreground = foreground

    def in_browser(self) -> bool:
        """L'utilisateur était-il dans un navigateur juste avant de parler à Jarvis ?"""
        return self.foreground is not None and self.foreground.context() == "browser"

    def _safari_in_use(self) -> bool:
        if not IS_MAC or self.foreground is None:
            return False
        browser = self.foreground.last_browser()
        return browser is not None and browser.app == "Safari"

    def call(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if self._safari_in_use():
            return safari.run(action, params)
        if self.bridge is None:
            raise BrowserUnavailable("Le pilotage du navigateur est désactivé (port occupé ou réglage).")
        return self.bridge.call(action, params)
