"""Presse-papiers : lire ce qui est copié, y mettre du texte.

macOS : pbpaste et pbcopy. Windows : le presse-papiers de PowerShell. Rien n'est jamais envoyé
ailleurs : le contenu ne sort de la machine que si tu demandes à Jarvis d'en faire quelque chose.
"""
from __future__ import annotations

import subprocess

from . import IS_MAC, IS_WINDOWS, NO_WINDOW, unsupported

MAX_CHARS = 4000


def read() -> str:
    if IS_MAC:
        completed = subprocess.run(["pbpaste"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=5)
    elif IS_WINDOWS:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=NO_WINDOW)
    else:
        raise unsupported("Le presse-papiers")
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "presse-papiers illisible")
    return completed.stdout.rstrip("\n")[:MAX_CHARS]


def write(text: str) -> None:
    if IS_MAC:
        completed = subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8", capture_output=True,
                                   timeout=5)
    elif IS_WINDOWS:
        # Par l'entrée standard : un texte passé en argument serait abîmé par cmd.exe.
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$input | Set-Clipboard"],
            input=text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=NO_WINDOW)
    else:
        raise unsupported("Le presse-papiers")
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "copie impossible")
