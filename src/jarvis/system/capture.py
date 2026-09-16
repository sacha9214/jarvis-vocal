"""Capture d'écran enregistrée sur le bureau.

Différente de l'analyse d'écran : ici l'image est un fichier que tu gardes, là elle reste en mémoire
et sert seulement à décrire ce qui est affiché.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from . import IS_MAC, IS_WINDOWS, NO_WINDOW, unsupported

MODES = ("screen", "area", "window")


def target_folder() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def take(mode: str = "screen", folder: Path | None = None) -> Path:
    """Enregistre une capture et renvoie son chemin. `area` laisse choisir la zone à la souris."""
    if mode not in MODES:
        raise ValueError(f"Mode de capture inconnu : {mode}")
    destination = (folder or target_folder()) / f"Capture {datetime.now():%Y-%m-%d à %H.%M.%S}.png"
    if IS_MAC:
        flags = {"screen": [], "area": ["-s"], "window": ["-w"]}[mode]
        completed = subprocess.run(["screencapture", "-x", *flags, str(destination)],
                                   capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not destination.exists():
            raise RuntimeError("capture annulée" if mode == "area" else
                               "macOS n'autorise pas encore Jarvis à enregistrer l'écran : Réglages Système, "
                               "Confidentialité et sécurité, Enregistrement de l'écran")
    elif IS_WINDOWS:
        if mode == "area":
            # L'outil Capture d'écran de Windows : l'utilisateur choisit la zone, puis colle lui-même.
            subprocess.Popen(["explorer", "ms-screenclip:"], creationflags=NO_WINDOW)
            raise RuntimeError("outil-capture")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$img = New-Object System.Drawing.Bitmap($b.Width, $b.Height);"
            "$g = [System.Drawing.Graphics]::FromImage($img);"
            "$g.CopyFromScreen($b.X, $b.Y, 0, 0, $img.Size);"
            f"$img.Save('{destination}', [System.Drawing.Imaging.ImageFormat]::Png);"
            "$g.Dispose(); $img.Dispose()")
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=NO_WINDOW)
        if completed.returncode != 0 or not destination.exists():
            raise RuntimeError(completed.stderr.strip().splitlines()[-1] if completed.stderr.strip()
                               else "capture impossible")
    else:
        raise unsupported("La capture d'écran")
    return destination
