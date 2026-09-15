"""Dossiers personnels : noms français → chemins, ouverture dans le Finder ou l'Explorateur."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..fastpath import normalize
from . import IS_MAC, IS_WINDOWS, unsupported

_FOLDERS = {
    "bureau": "Desktop", "documents": "Documents", "telechargements": "Downloads", "telechargement": "Downloads",
    "images": "Pictures", "musique": "Music", "videos": "Movies" if IS_MAC else "Videos",
}
LABELS = {"bureau": "le bureau", "documents": "les documents", "telechargements": "les téléchargements",
          "telechargement": "les téléchargements", "images": "les images", "musique": "le dossier musique",
          "videos": "les vidéos", "dossier personnel": "ton dossier personnel"}


def folder_path(name: str) -> Path | None:
    key = normalize(name)
    if key in ("dossier personnel", "maison", "home"):
        return Path.home()
    return Path.home() / _FOLDERS[key] if key in _FOLDERS else None


def open_path(path: Path) -> None:
    if IS_MAC:
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_WINDOWS:
        os.startfile(str(path))
    else:
        raise unsupported("L'ouverture de dossiers")
