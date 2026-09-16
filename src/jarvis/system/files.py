"""Fichiers : chercher, ouvrir, montrer dans l'explorateur, créer un dossier, place disque.

La recherche passe par l'index du système, le même que celui de la loupe : Spotlight sur macOS,
Windows Search sur Windows. Si l'index est absent (indexation coupée), on parcourt les dossiers
personnels, en s'arrêtant au bout de quelques secondes plutôt que de fouiller tout le disque.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import IS_MAC, IS_WINDOWS, NO_WINDOW, run, unsupported
from .folders import open_path

MAX_RESULTS = 12
SEARCH_TIMEOUT_S = 8.0
_WALK_SECONDS = 4.0
_SKIP = {"node_modules", ".git", "Library", "AppData", "__pycache__", ".venv", "venv", "$RECYCLE.BIN",
         "Windows", "Program Files", "Program Files (x86)", "ProgramData", ".Trash", "site-packages"}
# Dossiers fouillés quand l'index du système ne répond pas.
_HOME_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Movies", "Videos", "OneDrive")
DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".bmp", ".tiff"}
SHEET_SUFFIXES = {".xls", ".xlsx", ".csv", ".ods", ".numbers"}
KINDS = {"document": DOCUMENT_SUFFIXES, "image": IMAGE_SUFFIXES, "tableur": SHEET_SUFFIXES}


@dataclass(frozen=True)
class Found:
    path: Path
    modified: float

    @property
    def name(self) -> str:
        return self.path.name


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _powershell(script: str, timeout: float) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "PowerShell en échec")
    return completed.stdout


def _spotlight(query: str) -> list[Path]:
    """Index de macOS. -onlyin le dossier personnel : pas les fichiers système."""
    completed = run(["mdfind", "-onlyin", str(Path.home()), "-name", query], timeout=SEARCH_TIMEOUT_S)
    if completed.returncode != 0:
        return []
    return [Path(line) for line in completed.stdout.splitlines() if line.strip()]


def _windows_index(query: str) -> list[Path]:
    """Index de Windows Search, interrogé en SQL comme le fait l'explorateur."""
    safe = query.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop';"
        "$c = New-Object -ComObject ADODB.Connection;"
        "$c.Open(\"Provider=Search.CollatorDSO;Extended Properties='Application=Windows'\");"
        "$r = $c.Execute(\"SELECT TOP 60 System.ItemPathDisplay FROM SYSTEMINDEX \" + "
        f"\"WHERE System.FileName LIKE '%{safe}%' AND System.ItemPathDisplay IS NOT NULL\");"
        "while (-not $r.EOF) {{ $r.Fields.Item('System.ItemPathDisplay').Value; $r.MoveNext() }};"
        "$c.Close()")
    return [Path(line.strip()) for line in _powershell(script, SEARCH_TIMEOUT_S).splitlines() if line.strip()]


def _walk(query: str, deadline: float) -> list[Path]:
    """Repli : on parcourt les dossiers personnels jusqu'à la limite de temps."""
    wanted = soft(query)
    found: list[Path] = []
    roots = [Path.home() / name for name in _HOME_FOLDERS]
    for root in [r for r in roots if r.is_dir()]:
        for directory, subdirs, names in os.walk(root):
            if time.monotonic() > deadline:
                return found
            subdirs[:] = [d for d in subdirs if d not in _SKIP and not d.startswith(".")]
            found += [Path(directory) / name for name in names if wanted in soft(name)]
            if len(found) > MAX_RESULTS * 4:
                return found
    return found


def search(query: str, kind: str = "") -> list[Found]:
    """Fichiers dont le nom contient `query`, les plus récents d'abord."""
    query = query.strip()
    if len(query) < 2:
        return []
    paths: list[Path] = []
    try:
        paths = _spotlight(query) if IS_MAC else _windows_index(query) if IS_WINDOWS else []
    except (OSError, RuntimeError, subprocess.SubprocessError):
        paths = []
    if not paths:
        paths = _walk(query, time.monotonic() + _WALK_SECONDS)
    suffixes = KINDS.get(kind)
    results: dict[Path, Found] = {}
    for path in paths:
        if path in results or (suffixes and path.suffix.lower() not in suffixes):
            continue
        try:
            if not path.is_file():
                continue
            results[path] = Found(path, path.stat().st_mtime)
        except OSError:
            continue
    return sorted(results.values(), key=lambda f: f.modified, reverse=True)[:MAX_RESULTS]


def open_file(path: Path) -> None:
    """Ouvre avec l'application par défaut du système."""
    open_path(path)


def reveal(path: Path) -> None:
    """Montre le fichier dans le Finder ou l'Explorateur, sans l'ouvrir."""
    if IS_MAC:
        subprocess.Popen(["open", "-R", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)], creationflags=NO_WINDOW)
    else:
        raise unsupported("L'affichage d'un fichier dans l'explorateur")


def new_folder(name: str, parent: Path | None = None) -> Path:
    """Crée un dossier (par défaut sur le bureau) et l'ouvre."""
    # Les caractères interdits deviennent des espaces : « un/nom » donne « un nom », pas « unnom ».
    clean = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', " ", name)).strip() or "Nouveau dossier"
    base = parent or (Path.home() / "Desktop")
    if not base.is_dir():
        base = Path.home()
    target = base / clean
    suffix = 2
    while target.exists():
        target = base / f"{clean} {suffix}"
        suffix += 1
    target.mkdir(parents=True)
    return target


def disk_usage(path: Path | None = None) -> tuple[float, float]:
    """(libre, total) en giga-octets, sur le disque du dossier personnel."""
    usage = shutil.disk_usage(path or Path.home())
    return usage.free / 1e9, usage.total / 1e9


def trash(path: Path) -> None:
    """Met à la corbeille : récupérable, jamais une suppression définitive."""
    if IS_MAC:
        script = f'tell application "Finder" to delete POSIX file "{path}"'
        completed = run(["osascript", "-e", script], timeout=15)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "le Finder a refusé")
    elif IS_WINDOWS:
        escaped = str(path).replace("'", "''")
        _powershell("Add-Type -AssemblyName Microsoft.VisualBasic;"
                    "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                    f"'{escaped}', 'OnlyErrorDialogs', 'SendToRecycleBin')", timeout=20)
    else:
        raise unsupported("La mise à la corbeille")
