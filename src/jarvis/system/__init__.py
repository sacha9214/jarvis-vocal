"""Accès au système d'exploitation. Chaque fonction gère macOS et Windows ; ailleurs, elle le dit."""
from __future__ import annotations

import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Unsupported(RuntimeError):
    """Fonction indisponible sur ce système."""


def run(args: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def osascript(script: str, timeout: float = 10) -> str:
    completed = run(["osascript", "-e", script], timeout)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "AppleScript en échec")
    return completed.stdout.strip()


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unsupported(feature: str) -> Unsupported:
    return Unsupported(f"{feature} n'est pas pris en charge sur ce système.")
