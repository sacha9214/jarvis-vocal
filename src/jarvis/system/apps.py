"""Applications installées : inventaire, recherche tolérante (la transcription écorche les
noms), lancement et fermeture, sur macOS et Windows."""
from __future__ import annotations

import difflib
import functools
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..fastpath import normalize
from . import IS_MAC, IS_WINDOWS, applescript_string, osascript, run, unsupported


@dataclass(frozen=True)
class App:
    name: str       # nom affiché
    target: str     # chemin .app, raccourci .lnk, exécutable ou URI
    key: str        # nom normalisé


# Noms français courants → nom réel de l'application (souvent anglais).
ALIASES: dict[str, list[str]] = {
    "calculatrice": ["calculator", "calculatrice"],
    "calendrier": ["calendar", "calendrier"],
    "reglages": ["system settings", "settings", "parametres"],
    "parametres": ["system settings", "settings", "parametres"],
    "preferences systeme": ["system settings"],
    "musique": ["music"],
    "plans": ["maps"],
    "rappels": ["reminders"],
    "apercu": ["preview"],
    "horloge": ["clock"],
    "meteo": ["weather"],
    "livres": ["books"],
    "dictionnaire": ["dictionary"],
    "moniteur d activite": ["activity monitor"],
    "gestionnaire des taches": ["task manager"],
    "bloc notes": ["notepad", "textedit"],
    "explorateur": ["file explorer", "finder"],
    "explorateur de fichiers": ["file explorer", "finder"],
    "chrome": ["google chrome"],
    "vs code": ["visual studio code"],
    "vscode": ["visual studio code"],
    "word": ["microsoft word"],
    "excel": ["microsoft excel"],
    "powerpoint": ["microsoft powerpoint"],
    "teams": ["microsoft teams"],
    "edge": ["microsoft edge"],
    "terminal": ["terminal", "windows terminal", "command prompt"],
    "enregistreur": ["voice memos", "sound recorder"],
    "magasin": ["app store", "microsoft store"],
}
_WINDOWS_BUILTINS = {
    "calculator": "calc.exe", "notepad": "notepad.exe", "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "settings": "ms-settings:", "paint": "mspaint.exe", "command prompt": "cmd.exe",
}
_SKIP = re.compile(r"uninstall|désinstall|desinstall|readme|documentation|website|release notes", re.I)
_ARTICLES = re.compile(r"^(?:(?:l|la|le|les|mon|ma|mes|ton|ta|un|une|l application|application|le logiciel"
                       r"|logiciel|le jeu|jeu|l appli|appli) )+")


def _compact(text: str) -> str:
    return text.replace(" ", "")


def _entry(name: str, target: str) -> App:
    return App(name, target, normalize(name))


def _mac_apps() -> list[App]:
    roots = [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
             Path("/System/Applications/Utilities"), Path.home() / "Applications"]
    apps = [_entry(p.stem, str(p)) for root in roots if root.is_dir() for p in root.glob("*.app")]
    apps += [_entry(p.stem, str(p)) for p in Path("/Applications").glob("*/*.app")]
    finder = Path("/System/Library/CoreServices/Finder.app")
    if finder.exists():
        apps.append(_entry("Finder", str(finder)))
    return apps


def _windows_apps() -> list[App]:
    roots = [Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs"]
    apps = [_entry(p.stem, str(p)) for root in roots if root.is_dir()
            for p in root.rglob("*.lnk") if not _SKIP.search(p.stem)]
    apps += [App(name.title(), target, name) for name, target in _WINDOWS_BUILTINS.items()]
    return apps


@functools.lru_cache(maxsize=1)
def installed_apps() -> tuple[App, ...]:
    apps = _mac_apps() if IS_MAC else _windows_apps() if IS_WINDOWS else []
    unique: dict[str, App] = {}
    for app in apps:
        unique.setdefault(app.key, app)
    return tuple(unique.values())


def find_app(query: str, apps: Sequence[App] | None = None) -> App | None:
    apps = installed_apps() if apps is None else apps
    wanted = _ARTICLES.sub("", normalize(query)).strip()
    if not wanted:
        return None
    by_key = {app.key: app for app in apps}
    by_compact = {_compact(app.key): app for app in apps}
    candidates = [wanted, *ALIASES.get(wanted, [])]
    for candidate in candidates:                      # exact, espaces compris ou non
        if app := by_key.get(candidate) or by_compact.get(_compact(candidate)):
            return app
    for candidate in candidates:                      # « visual studio » → « visual studio code »
        if len(candidate) >= 3:
            prefixed = [app for app in apps if app.key.startswith(candidate)]
            if prefixed:
                return min(prefixed, key=lambda app: len(app.key))
    close = difflib.get_close_matches(wanted, list(by_key), n=1, cutoff=0.75)
    if close:
        return by_key[close[0]]
    phonetic = {_phonetic(app.key): app for app in apps if len(app.key) >= 4}
    heard = _phonetic(wanted)
    if len(heard) >= 4:
        close = difflib.get_close_matches(heard, list(phonetic), n=1, cutoff=0.8)
        if close:
            return phonetic[close[0]]
    return None


def _phonetic(text: str) -> str:
    """Clé de prononciation à la française : « Spotifaille » et « Spotify » se rejoignent."""
    key = _compact(text)
    for old, new in (("eau", "o"), ("aille", "ai"), ("ille", "i"), ("au", "o"), ("ph", "f"), ("qu", "k"),
                     ("ck", "k"), ("c", "k"), ("ou", "u"), ("ai", "e"), ("ei", "e"), ("ay", "e"), ("y", "i"),
                     ("w", "v"), ("z", "s"), ("x", "ks"), ("h", "")):
        key = key.replace(old, new)
    key = re.sub(r"(.)\1+", r"\1", key)
    return key.rstrip("es") or key


def vocabulary(budget: int = 480) -> list[str]:
    """Noms des applications installées par l'utilisateur, pour amorcer la transcription. Les plus
    courts d'abord, dans un budget de caractères : une amorce longue ralentit chaque phrase."""
    names = sorted({app.name for app in installed_apps()
                    if not app.target.startswith("/System/") and re.fullmatch(r"[A-Za-z][\w .+'-]{1,22}", app.name)
                    and not _SKIP.search(app.name) and not re.search(r"helper|handler|launcher", app.name, re.I)},
                   key=lambda name: (len(name), name.lower()))
    chosen: list[str] = []
    used = 0
    for name in names:
        if used + len(name) + 2 > budget:
            break
        chosen.append(name)
        used += len(name) + 2
    return sorted(chosen, key=str.lower)


def launch(app: App) -> None:
    if IS_MAC:
        subprocess.Popen(["open", app.target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_WINDOWS:
        os.startfile(app.target)  # raccourci, exécutable ou URI (ms-settings:)
    else:
        raise unsupported("L'ouverture d'applications")


def _lnk_target(path: str) -> str | None:
    """Exécutable visé par un raccourci Windows (« Visual Studio Code.lnk » → Code.exe)."""
    escaped = path.replace("'", "''")
    completed = run(["powershell", "-NoProfile", "-Command",
                     f"(New-Object -ComObject WScript.Shell).CreateShortcut('{escaped}').TargetPath"], timeout=10)
    return completed.stdout.strip() or None


def quit_app(query: str) -> str:
    app = find_app(query)
    label = app.name if app else query
    if IS_MAC:
        if osascript(f"application {applescript_string(label)} is running") != "true":
            return f"{label} n'est pas ouvert."
        osascript(f"quit app {applescript_string(label)}")    # fermeture propre : propose d'enregistrer
        return f"Je ferme {label}."
    if not IS_WINDOWS:
        raise unsupported("La fermeture d'applications")
    names = {_compact(normalize(query))}
    if app:
        names.add(_compact(app.key))
        if app.target.lower().endswith(".lnk") and (target := _lnk_target(app.target)):
            names.add(_compact(normalize(Path(target).stem)))
    processes = [p for p in psutil.process_iter(["name"])
                 if _compact(normalize(Path(p.info["name"] or "").stem)) in names]
    if not processes:
        return f"{label} n'est pas ouvert."
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    return f"Je ferme {label}."
