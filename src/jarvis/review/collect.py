"""Ce qu'il faut relire : les fichiers de code d'un projet, ou ses changements non commités (git)."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..system import NO_WINDOW
from .project import SKIP_DIRS

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".html", ".css", ".scss", ".java", ".kt",
    ".kts", ".swift", ".m", ".mm", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".lua",
    ".luau", ".gd", ".dart", ".scala", ".sh", ".ps1", ".bat", ".sql", ".r", ".jl", ".ex", ".exs", ".zig", ".toml",
    ".yaml", ".yml", ".json", ".gradle", ".cmake",
}
SPECIAL_NAMES = {"Dockerfile", "Makefile", "CMakeLists.txt", "Rakefile", "Gemfile"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock",
              "composer.lock", "Gemfile.lock", "bun.lockb", "tsconfig.tsbuildinfo"}
MAX_FILE_BYTES = 200_000


@dataclass
class Material:
    scope: str                                   # project | changes | file
    root: Path
    files: list[Path] = field(default_factory=list)
    diff: str = ""


def git(root: Path, *args: str, timeout: float = 30) -> str | None:
    try:
        completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def reviewable(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if any(part in SKIP_DIRS for part in relative.parts[:-1]) or path.name in SKIP_NAMES or ".min." in path.name:
        return False
    if path.suffix.lower() not in CODE_EXTENSIONS and path.name not in SPECIAL_NAMES:
        return False
    try:
        return path.is_file() and path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _split(listing: str | None, root: Path) -> list[Path]:
    return [root / name for name in (listing or "").split("\0") if name]


def project_files(root: Path) -> list[Path]:
    listing = git(root, "ls-files", "-co", "--exclude-standard", "-z")
    if listing is not None:
        paths = _split(listing, root)
    else:
        paths = []
        for directory, subdirs, names in os.walk(root):
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS and not d.startswith(".")]
            paths += [Path(directory) / name for name in names]
    return sorted((p for p in paths if reviewable(p, root)), key=lambda p: str(p.relative_to(root)).lower())


def changes(root: Path) -> Material | None:
    """Changements non commités (modifiés et nouveaux) ; None si ce n'est pas un dépôt git."""
    if git(root, "rev-parse", "--is-inside-work-tree") is None:
        return None
    new = [p for p in _split(git(root, "ls-files", "-o", "--exclude-standard", "-z"), root) if reviewable(p, root)]
    if git(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:     # aucun commit : tout est nouveau
        return Material("changes", root, project_files(root))
    diff = git(root, "diff", "HEAD", "--relative", "--no-color", "--no-ext-diff", "--", ".") or ""
    return Material("changes", root, new, diff)


def numbered(path: Path, root: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    relative = path.relative_to(root).as_posix()
    return f"### {relative}\n" + "\n".join(f"{index}| {line}" for index, line in enumerate(lines, 1))


def chunks(texts: list[str], size: int) -> list[str]:
    """Regroupe des textes en morceaux d'au plus `size` caractères, en coupant les longs aux lignes."""
    pieces: list[str] = []
    for text in texts:
        if len(text) <= size:
            pieces.append(text)
            continue
        header = text.split("\n", 1)[0] if text.startswith(("### ", "diff --git")) else ""
        current = ""
        for line in text.splitlines(keepends=True):
            if current and len(current) + len(line) > size:
                pieces.append(current)
                current = f"{header} (suite)\n" if header else ""
            current += line[:size]
        if current.strip():
            pieces.append(current)
    packed: list[str] = []
    for piece in pieces:
        if packed and len(packed[-1]) + len(piece) + 2 <= size:
            packed[-1] += "\n\n" + piece
        else:
            packed.append(piece)
    return packed


def diff_files(diff: str) -> list[str]:
    """Découpe un diff git par fichier."""
    parts = ("\n" + diff).split("\ndiff --git ")
    return [f"diff --git {part.strip()}" for part in parts[1:] if part.strip()]
