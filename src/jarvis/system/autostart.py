"""Démarrage automatique de Jarvis à l'ouverture de session.

macOS : un LaunchAgent dans ~/Library/LaunchAgents, le mécanisme standard ; aucun droit administrateur.
Windows : la clé Run du registre de l'utilisateur (HKCU), celle qu'utilisent Discord ou Spotify ; aucun droit
administrateur, et `pythonw` plutôt que `python` pour ne pas ouvrir de fenêtre de console.

Rien n'est activé tout seul : `jarvis autostart on` ou le réglage de l'interface.
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from ..paths import data_dir
from . import IS_MAC, IS_WINDOWS, unsupported

LABEL = "com.jarvis.vocal"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Jarvis"


def project_dir() -> Path:
    """Le dossier du dépôt : config.yaml local éventuel et `uv run` s'y retrouvent."""
    return Path(__file__).resolve().parents[3]


def launch_command(executable: str | None = None) -> list[str]:
    """Commande qui démarre Jarvis sans terminal : l'interpréteur de l'environnement du projet."""
    python = Path(executable or sys.executable)
    if IS_WINDOWS:
        windowless = python.with_name("pythonw.exe")
        if windowless.exists():
            python = windowless
    return [str(python), "-m", "jarvis"]


# -- macOS

def mac_plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def mac_plist(executable: str | None = None) -> bytes:
    logs = data_dir() / "logs"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": launch_command(executable),
        "WorkingDirectory": str(project_dir()),
        "RunAtLoad": True,
        "KeepAlive": False,                       # fermé par l'utilisateur : il reste fermé
        "ProcessType": "Interactive",             # micro et fenêtre : pas une tâche de fond bridée
        "StandardOutPath": str(logs / "demarrage.log"),
        "StandardErrorPath": str(logs / "demarrage.log"),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
    })


# -- Windows

def _windows_key(write: bool, create: bool = False):
    """La clé Run n'existe pas forcément : sur un Windows où aucun programme n'a jamais été mis au
    démarrage, elle est absente et `OpenKey` lève WinError 2 (mesuré sur un runner de CI vierge).
    Pour écrire on la crée donc au besoin ; pour lire, son absence veut simplement dire « rien
    au démarrage » et l'appelant traite l'erreur.
    """
    import winreg
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if write else winreg.KEY_QUERY_VALUE
    if create:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def windows_command_line(executable: str | None = None) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in launch_command(executable))


# -- commun

def is_enabled(home: Path | None = None, value_name: str = VALUE_NAME) -> bool:
    if IS_MAC:
        return mac_plist_path(home).exists()
    if IS_WINDOWS:
        import winreg
        try:
            with _windows_key(False) as key:
                winreg.QueryValueEx(key, value_name)
            return True
        except OSError:            # clé ou valeur absente : rien au démarrage
            return False
    return False


def enable(home: Path | None = None, value_name: str = VALUE_NAME, executable: str | None = None) -> str:
    if IS_MAC:
        path = mac_plist_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        (data_dir() / "logs").mkdir(parents=True, exist_ok=True)
        path.write_bytes(mac_plist(executable))
        return f"Jarvis démarrera à l'ouverture de ta session (fichier {path})."
    if IS_WINDOWS:
        import winreg
        with _windows_key(True, create=True) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, windows_command_line(executable))
        return "Jarvis démarrera à l'ouverture de ta session Windows."
    raise unsupported("Le démarrage automatique")


def disable(home: Path | None = None, value_name: str = VALUE_NAME) -> str:
    if IS_MAC:
        path = mac_plist_path(home)
        if not path.exists():
            return "Le démarrage automatique n'était pas activé."
        path.unlink()
        return "Jarvis ne démarrera plus tout seul."
    if IS_WINDOWS:
        import winreg
        try:
            with _windows_key(True) as key:
                winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            return "Le démarrage automatique n'était pas activé."
        return "Jarvis ne démarrera plus tout seul."
    raise unsupported("Le démarrage automatique")


def run(action: str) -> int:
    """`jarvis autostart on|off|status`."""
    try:
        if action == "on":
            print(enable())
        elif action == "off":
            print(disable())
        else:
            print("Démarrage automatique : " + ("activé." if is_enabled() else "désactivé.")
                  + "\n  jarvis autostart on   pour l'activer\n  jarvis autostart off  pour le couper")
    except (OSError, RuntimeError) as exc:
        print(f"❌ {exc}")
        return 1
    return 0
