"""Fenêtre native de l'interface (pywebview : WebKit sur macOS, WebView2 sur Windows)."""
from __future__ import annotations

import logging

LOG = logging.getLogger("jarvis.ui")


def open_window(url: str) -> bool:
    """Ouvre la fenêtre et bloque jusqu'à sa fermeture. False si aucune fenêtre n'a pu s'ouvrir
    (dans ce cas, l'appelant se rabat sur le navigateur). Doit tourner sur le fil principal."""
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.create_window("Jarvis", url, width=1380, height=880, min_size=(960, 640),
                              background_color="#02060b", text_select=False)
        webview.start()
        return True
    except Exception as exc:  # noqa: BLE001 - WebView2 absent, pas d'écran…
        LOG.warning("Fenêtre indisponible (%s) : ouverture dans le navigateur.", exc)
        return False
