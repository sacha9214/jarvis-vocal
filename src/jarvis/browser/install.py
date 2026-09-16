"""`jarvis extension` : prépare l'extension pour chaque navigateur et explique comment l'ajouter."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from ..paths import STORE_PYTHON_HINT, data_dir, store_python
from ..system import IS_MAC, IS_WINDOWS

SOURCE = Path(__file__).parent / "extension"


TOKEN_FILE = "bridge-token"


def token() -> str:
    """Jeton du pont : créé une fois, partagé par Jarvis et les extensions installées (navigateur, VS Code)."""
    path = data_dir() / TOKEN_FILE
    legacy = data_dir() / "browser-token"
    if not path.exists() and legacy.exists():
        legacy.rename(path)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        if os.name == "posix":
            os.chmod(path, 0o600)
    return path.read_text(encoding="utf-8").strip()


def build(port: int, root: Path | None = None) -> dict[str, Path]:
    root = root or data_dir() / "extension"
    folders = {}
    for flavor, manifest in (("chromium", "manifest.json"), ("firefox", "manifest.firefox.json")):
        target = root / flavor
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        for name in ("actions.js", "background.js"):
            shutil.copy2(SOURCE / name, target / name)
        shutil.copy2(SOURCE / manifest, target / "manifest.json")
        config = json.dumps({"port": port, "token": token()})
        (target / "config.js").write_text(f"globalThis.JARVIS_CONFIG = {config};\n", encoding="utf-8")
        folders[flavor] = target
    return folders


def run(port: int) -> int:
    if store_python():
        print(f"❌ {STORE_PYTHON_HINT}")
        return 1
    folders = build(port)
    print("Extension Jarvis prête.\n")
    print("Chrome, Edge, Brave, Opera ou Arc :")
    print("  1. Ouvre chrome://extensions (edge://extensions pour Edge, brave://extensions pour Brave).")
    print("  2. Active le « Mode développeur » (en haut à droite, ou à gauche dans Edge).")
    print("  3. « Charger l'extension non empaquetée » et choisis ce dossier :")
    print(f"     {folders['chromium']}\n")
    print("Firefox :")
    print("  1. Ouvre about:debugging#/runtime/this-firefox, « Charger un module complémentaire temporaire ».")
    print(f"  2. Choisis {folders['firefox'] / 'manifest.json'}")
    print("  3. Clique sur l'icône Jarvis dans la barre d'outils et accepte l'accès à tous les sites")
    print("     (ou about:addons › Jarvis › Permissions). Tant que ce n'est pas fait, le badge affiche « ! ».")
    print("  (Firefox retire les modules temporaires à sa fermeture ; recommence après un redémarrage.)\n")
    if IS_MAC:
        print("Safari (sans extension) : Réglages › Avancés ›")
        print("  « Afficher les fonctionnalités pour les développeurs web »,")
        print("  puis menu Développement › « Autoriser JavaScript depuis les Apple Events ».\n")
    print("L'icône Jarvis de l'extension affiche « off » tant que Jarvis n'est pas lancé : c'est normal.")
    try:
        if IS_WINDOWS:
            os.startfile(folders["chromium"])  # type: ignore[attr-defined]
        elif IS_MAC:
            subprocess.Popen(["open", str(folders["chromium"])])
    except OSError:
        pass
    return 0
