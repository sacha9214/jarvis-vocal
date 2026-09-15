"""Projet ouvert dans l'éditeur : dossier, fichier affiché, dépôt git.

Deux indices, sans extension ni permission : le titre de la fenêtre (« pipeline.py - jarvis-vocal -
Visual Studio Code ») et l'historique que VS Code, Cursor, Windsurf et VSCodium tiennent sur disque
(dossiers des fenêtres ouvertes, dossiers et fichiers récents).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..system.foreground import EDITORS, Foreground

PRODUCTS = ("Code", "Code - Insiders", "Cursor", "Windsurf", "VSCodium")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out", "target", ".next",
             ".nuxt", ".idea", ".vscode", "vendor", "coverage", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             ".tox", ".gradle", "Pods", "DerivedData", "bin", "obj", ".dart_tool", ".cache"}
_SEPARATOR = re.compile(r"\s+[—–-]\s+")
_REMOTE = re.compile(r"\s*\[[^\]]*\]\s*$|\s*\((?:espace de travail|workspace)\)\s*$", re.I)


@dataclass(frozen=True)
class Project:
    root: Path
    current_file: Path | None = None

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def is_git(self) -> bool:
        return git_root(self.root) is not None


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def title_parts(title: str, app: str = "") -> list[str]:
    """« ● pipeline.py - jarvis-vocal [SSH: box] - Visual Studio Code » → [pipeline.py, jarvis-vocal]."""
    text = title.strip().lstrip("●•◉*").strip()
    parts = [_REMOTE.sub("", part).strip() for part in _SEPARATOR.split(text)]
    editors = {soft(name) for name in (app, *EDITORS) if name}
    for size in (2, 1):          # « Visual Studio Code - Insiders » contient lui-même un séparateur
        if len(parts) > size and soft(" ".join(parts[-size:])) in editors:
            del parts[-size:]
            break
    return [part for part in parts if part]


def uri_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None          # dossiers distants (SSH, WSL, conteneurs) : pas lisibles d'ici
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):                  # file:///c%3A/Users/… → c:/Users/…
        path = path[1:]
    return Path(path)


def user_dirs() -> list[Path]:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [base / product / "User" for product in PRODUCTS]


def _recently_opened(database: Path) -> list[dict]:
    if not database.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1)) as connection:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = 'history.recentlyOpenedPathsList'").fetchone()
        return list(json.loads(row[0]).get("entries") or []) if row else []
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return []


def recent(dirs: list[Path] | None = None) -> tuple[list[Path], list[Path]]:
    """Dossiers (fenêtres ouvertes d'abord) et fichiers récents des éditeurs de la famille VS Code."""
    folders: list[Path] = []
    files: list[Path] = []
    for user in dirs if dirs is not None else user_dirs():
        storage = user / "globalStorage"
        try:
            data = json.loads((storage / "storage.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        windows = data.get("windowsState") or {}
        for window in [windows.get("lastActiveWindow") or {}, *(windows.get("openedWindows") or [])]:
            if isinstance(window, dict) and (path := uri_path(str(window.get("folder") or ""))):
                folders.append(path)
        for entry in (data.get("backupWorkspaces") or {}).get("folders") or []:
            if isinstance(entry, dict) and (path := uri_path(str(entry.get("folderUri") or ""))):
                folders.append(path)
        for entry in _recently_opened(storage / "state.vscdb"):
            if not isinstance(entry, dict):
                continue
            if path := uri_path(str(entry.get("folderUri") or "")):
                folders.append(path)
            elif path := uri_path(str((entry.get("workspace") or {}).get("configPath") or "")):
                folders.append(path.parent)
            elif path := uri_path(str(entry.get("fileUri") or "")):
                files.append(path)
    if dirs is None:
        folders += jetbrains_projects()
    return _existing(folders, Path.is_dir), _existing(files, Path.is_file)


def jetbrains_projects(bases: list[Path] | None = None, home: Path | None = None) -> list[Path]:
    """Projets récents des IDE JetBrains (PyCharm, IntelliJ IDEA, WebStorm, Rider…), le plus récent IDE d'abord."""
    home = home or Path.home()
    if bases is None:
        if sys.platform == "win32":
            bases = [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "JetBrains"]
        elif sys.platform == "darwin":
            bases = [home / "Library" / "Application Support" / "JetBrains"]
        else:
            bases = [Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "JetBrains"]
    found: list[Path] = []
    for base in bases:
        try:
            files = sorted(base.glob("*/options/recentProjects.xml"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for xml in files:
            try:
                text = xml.read_text(encoding="utf-8")
            except OSError:
                continue
            for key in re.findall(r'<entry key="([^"]+)"', text):
                path = Path(key.replace("$USER_HOME$", str(home)))
                if path.is_dir():
                    found.append(path)
    return found


def search_dirs() -> list[Path]:
    """Dossiers où l'on range habituellement ses projets, pour un projet nommé à la voix."""
    home = Path.home()
    names = ("Desktop", "Bureau", "Documents", "dev", "code", "projects", "projets", "src", "repos", "git", "GitHub",
             "source/repos", "Documents/GitHub", "PycharmProjects", "IdeaProjects", "WebstormProjects",
             "OneDrive/Desktop", "OneDrive/Bureau", "OneDrive/Documents")
    return [home / name for name in names if (home / name).is_dir()]


def search_named(target: str, bases: list[Path], depth: int = 2, limit: int = 3000) -> Path | None:
    """Dossier dont le nom correspond exactement (à la casse, aux accents et aux tirets près), en largeur."""
    wanted = soft(target)
    if not wanted:
        return None
    seen: set[tuple[int, int]] = set()
    level = list(bases)
    for _ in range(depth):
        following: list[Path] = []
        for base in level:
            try:
                children = sorted(c for c in base.iterdir()
                                  if c.is_dir() and not c.name.startswith(".") and c.name not in SKIP_DIRS)
            except OSError:
                continue
            for child in children:
                try:
                    stat = child.stat()
                except OSError:
                    continue
                if (stat.st_dev, stat.st_ino) in seen:      # Desktop et Bureau, dev et Dev…
                    continue
                seen.add((stat.st_dev, stat.st_ino))
                if soft(child.name) == wanted:
                    return child
                if len(following) < limit:
                    following.append(child)
        level = following
    return None


def _existing(paths: list[Path], check) -> list[Path]:
    seen: set[str] = set()
    result = []
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen and check(path):
            seen.add(key)
            result.append(path)
    return result


def git_root(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def find_file(root: Path, name: str, limit: int = 20000) -> Path | None:
    seen = 0
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        if name in names:
            return Path(directory) / name
        seen += len(names)
        if seen > limit:
            return None
    return None


def find_project(window: Foreground | None = None, target: str = "", dirs: list[Path] | None = None,
                 bases: list[Path] | None = None) -> Project | None:
    """Le projet visé : celui qui est nommé, sinon celui de la fenêtre d'éditeur, sinon le plus récent."""
    folders, files = recent(dirs)
    parts = title_parts(window.title, window.app) if window is not None and window.title else []
    scan = bases if bases is not None else (search_dirs() if dirs is None else [])
    root = None
    if wanted := soft(target):
        root = next((f for f in folders if soft(f.name) == wanted), None) or \
            next((f for f in folders if wanted in soft(f.name) or soft(f.name) in wanted), None) or \
            search_named(target, scan)
        if root is None:
            return None
    if root is None:
        names = {soft(part) for part in parts}
        root = next((f for f in folders if soft(f.name) in names), None)
    if root is None and window is not None and window.is_editor:      # éditeur sans historique lisible
        root = next((found for part in parts if "." not in part and (found := search_named(part, scan))), None)
    if root is None and folders:
        root = folders[0]
    if root is None:
        return None
    current = None
    for part in parts:
        current = next((f for f in files if f.name == part and _inside(f, root)), None)
        if current is None and "." in part and not (root / part).is_dir():
            current = find_file(root, part)
        if current is not None:
            break
    return Project(root, current)
