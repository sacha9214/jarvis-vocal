"""Emplacements des données (modèles, config) selon l'OS."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "jarvis"


STORE_PYTHON_HINT = ("Python du Microsoft Store : ce qu'il écrit dans AppData\\Local part dans un dossier virtuel "
                     "que VS Code et les navigateurs ne voient pas. Lance `uv python install 3.12` puis "
                     "`uv sync --reinstall`.")


def store_python() -> bool:
    """Python installé depuis le Microsoft Store (exécutable sous WindowsApps ou paquet PythonSoftwareFoundation)."""
    executable = (sys.executable or "").lower() + (getattr(sys, "_base_executable", "") or "").lower()
    return sys.platform == "win32" and ("windowsapps" in executable or "pythonsoftwarefoundation" in executable)


def data_dir() -> Path:
    override = os.environ.get("JARVIS_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP
    base.mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    """JARVIS_CONFIG, sinon ./config.yaml s'il existe, sinon celui du dossier de données."""
    if env := os.environ.get("JARVIS_CONFIG"):
        return Path(env)
    local = Path.cwd() / "config.yaml"
    return local if local.exists() else data_dir() / "config.yaml"
