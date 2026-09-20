"""Fenêtre native de l'interface (pywebview : WebKit sur macOS, WebView2 sur Windows)."""
from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger("jarvis.ui")
DISPLAYS = ("fenetre", "agrandie", "plein_ecran")
# Icône de la fenêtre et du Dock : embarquée dans le paquet, donc présente aussi dans l'exécutable.
ICON = Path(__file__).parent / "static" / "logo.png"
# Écrans détectés au démarrage, sur le fil principal (AppKit l'exige) : les réglages les lisent ensuite.
SCREENS: list[tuple[str, str]] = []


def detect_screens() -> list[tuple[str, str]]:
    """[(« 1 », « Écran 1 · 1920 × 1080 · principal »), …] ; vide si aucune interface graphique."""
    try:
        import webview
        screens = list(webview.screens)
    except Exception as exc:  # noqa: BLE001 - pas d'écran (serveur, CI), pywebview absent
        LOG.debug("Écrans indisponibles : %s", exc)
        screens = []
    SCREENS[:] = [(str(i + 1), f"Écran {i + 1} · {s.width} × {s.height}" + (" · principal" if i == 0 else ""))
                  for i, s in enumerate(screens)]
    return SCREENS


def window_options(screen: str, display: str, screens: list | None = None) -> dict:
    """Arguments de create_window pour l'écran et l'affichage choisis. Un écran débranché depuis retombe sur
    le principal, plutôt que d'ouvrir une fenêtre hors de vue."""
    options: dict = {"fullscreen": display == "plein_ecran", "maximized": display == "agrandie"}
    if screens and screen.isdigit():
        index = int(screen) - 1
        if 0 <= index < len(screens):
            options["screen"] = screens[index]
        else:
            LOG.warning("Écran %s absent (%d branché(s)) : fenêtre sur l'écran principal.", screen, len(screens))
    return options


def open_window(url: str, screen: str = "auto", display: str = "fenetre") -> bool:
    """Ouvre la fenêtre et bloque jusqu'à sa fermeture. False si aucune fenêtre n'a pu s'ouvrir
    (dans ce cas, l'appelant se rabat sur le navigateur). Doit tourner sur le fil principal."""
    try:
        import webview
    except ImportError:
        return False
    try:
        options = window_options(screen, display, list(webview.screens))
        webview.create_window("Jarvis", url, width=1380, height=880, min_size=(960, 640),
                              background_color="#02060b", text_select=False, **options)
        webview.start(icon=str(ICON) if ICON.exists() else None)
        return True
    except Exception as exc:  # noqa: BLE001 - WebView2 absent, pas d'écran…
        LOG.warning("Fenêtre indisponible (%s) : ouverture dans le navigateur.", exc)
        return False
