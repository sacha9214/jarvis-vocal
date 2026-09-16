"""Éditeur de code relié à Jarvis par son extension (VS Code, Cursor, Windsurf, VSCodium).

VS Code n'expose rien à l'accessibilité (mesuré : 12 éléments, 0 caractère) ; l'extension est le seul
moyen de lire le fichier ouvert, la sélection et les erreurs, et d'agir dans l'éditeur.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..browser.bridge import EDITOR, BrowserBridge, BrowserUnavailable


class EditorController:
    def __init__(self, bridge: BrowserBridge | None):
        self.bridge = bridge

    def linked(self) -> bool:
        return self.bridge is not None and bool(self.bridge.editors())

    def call(self, action: str, params: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
        if self.bridge is None:
            raise BrowserUnavailable("Le pilotage de l'éditeur est désactivé (port occupé ou réglage).")
        return self.bridge.call(action, params or {}, timeout=timeout, kind=EDITOR)

    def current(self) -> tuple[Path, Path | None] | None:
        """(dossier du projet, fichier affiché) d'après l'éditeur relié ; None s'il n'y en a pas."""
        if not self.linked():
            return None
        try:
            status = self.call("status", timeout=3.0)
        except Exception:  # noqa: BLE001 - l'éditeur ne répond pas : on retombe sur la fenêtre et l'historique
            return None
        root, file = status.get("workspace"), status.get("file")
        if not root and not file:
            return None
        current = Path(file) if file else None
        return Path(root) if root else current.parent, current
