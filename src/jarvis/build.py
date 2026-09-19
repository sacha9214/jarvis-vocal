"""`jarvis build` : un exécutable autonome de Jarvis (Jarvis.exe sous Windows, Jarvis.app sous macOS).

PyInstaller en mode dossier, pas en fichier unique : un .exe unique décompresserait ~2 Go dans un dossier
temporaire à chaque lancement (dizaines de secondes). Le dossier `dist/Jarvis` se copie tel quel, et une
archive zip est produite à côté pour le partager.

Ce qui n'est pas dans l'exécutable : les modèles (téléchargés au premier lancement dans le dossier de données,
comme avec `uv run`), Ollama (application à part) et Claude Code (le binaire `claude` officiel, que
l'utilisateur connecte lui-même).
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from pathlib import Path

LOG = logging.getLogger("jarvis")
NAME = "Jarvis"
# Paquets qui chargent des fichiers (modèles, DLL, données) ou des modules à l'exécution : PyInstaller ne les
# voit pas en suivant les imports, on les embarque en entier.
COLLECT_ALL = ["jarvis", "pocket_tts", "piper", "onnxruntime", "sounddevice", "_sounddevice_data", "soxr",
               "webview", "mcp", "recurring_ical_events", "icalendar", "tokenizers", "sentencepiece"]
COLLECT_WINDOWS = ["faster_whisper", "ctranslate2", "uiautomation", "comtypes"]
COLLECT_MAC = ["mlx", "mlx_whisper", "Quartz", "ApplicationServices"]
EXCLUDE = ["tkinter", "matplotlib", "IPython", "pytest", "ruff"]

ENTRY = '''import multiprocessing, os, sys
# Les sous-processus (torch, multiprocessing) relancent l'exécutable lui-même : sans cela, ils arrivent
# dans la ligne de commande de Jarvis (« invalid choice: from multiprocessing.resource_tracker… »).
multiprocessing.freeze_support()
# Lancé sans console (double-clic) : pas de sortie standard. Les barres de progression des téléchargements
# écriraient dans le vide et planteraient ; le journal, lui, va dans logs/jarvis.log.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
from jarvis.__main__ import main
sys.exit(main())
'''


def arguments(root: Path, work: Path, platform: str = sys.platform) -> list[str]:
    """La ligne de commande PyInstaller ; séparée pour être testée sans lancer une construction de 10 minutes."""
    entry = work / "jarvis_entry.py"
    packages = COLLECT_ALL + (COLLECT_WINDOWS if platform == "win32" else COLLECT_MAC if platform == "darwin" else [])
    args = [str(entry), "--name", NAME, "--noconfirm", "--clean", "--windowed",
            "--distpath", str(root / "dist"), "--workpath", str(work / "build"), "--specpath", str(work)]
    for package in packages:
        if importlib.util.find_spec(package) is not None:     # absent sur cette plateforme : on passe
            args += ["--collect-all", package]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    icon = root / "packaging" / ("jarvis.ico" if platform == "win32" else "jarvis.icns")
    if icon.exists():
        args += ["--icon", str(icon)]
    return args


def run(root: Path | None = None) -> int:
    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError:
        print("❌ PyInstaller n'est pas installé. Lance :\n   uv sync --group build\n   uv run jarvis build")
        return 1
    root = root or Path.cwd()
    work = root / "build"
    work.mkdir(exist_ok=True)
    (work / "jarvis_entry.py").write_text(ENTRY, encoding="utf-8")
    print("Construction de l'exécutable (5 à 15 minutes, ~2 Go)…")
    pyinstaller.run(arguments(root, work))
    folder = root / "dist" / NAME
    app = root / "dist" / f"{NAME}.app"
    target = app if sys.platform == "darwin" and app.exists() else folder
    archive = shutil.make_archive(str(root / "dist" / f"{NAME}-{sys.platform}"), "zip", target.parent, target.name)
    exe = folder / (f"{NAME}.exe" if sys.platform == "win32" else NAME)
    print(f"✅ Exécutable : {exe}\n   Dossier à copier : {target}\n   Archive à partager : {archive}\n"
          "   Premier lancement : les modèles se téléchargent (~6 Go). Ollama s'installe à part pour le mode local.")
    return 0


def selftest(deep: bool = False) -> int:
    """`jarvis selftest` : dans l'exécutable, vérifie que tout ce qui se charge à l'exécution y est bien.
    `--deep` fait en plus parler la voix Piper et tourner le mot d'activation (télécharge ~70 Mo) : les DLL
    natives (onnxruntime, espeak, PortAudio) ne se révèlent qu'à l'usage."""
    import importlib

    from . import paths
    failures = []
    modules = ["jarvis.pipeline", "jarvis.app", "jarvis.server", "jarvis.ui.controller", "jarvis.doctor",
               "jarvis.tts.piper_backend", "jarvis.tts.pocket_backend", "onnxruntime", "sounddevice", "webview",
               "jarvis.stt.faster_backend" if sys.platform == "win32" else "jarvis.stt.mlx_backend"]
    if sys.platform == "win32":
        modules += ["faster_whisper", "ctranslate2", "uiautomation", "jarvis.desktop"]
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - on liste tout ce qui manque, pas seulement le premier
            failures.append(f"{name} : {exc!r}")
    package = Path(__file__).parent
    for data in ("ui/static/index.html", "browser/extension/manifest.json", "editor/extension/package.json"):
        if not (package / data).exists():
            failures.append(f"fichier absent : {data}")
    if deep and not failures:
        try:
            import numpy as np

            from .app import build_vad, build_wakeword
            from .assets import piper_voice
            from .config import Config
            from .tts.piper_backend import PiperTTS
            cfg = Config().resolve()
            tts = PiperTTS(piper_voice(cfg.tts.piper_voice), 1.0)
            audio = np.concatenate(list(tts.synthesize("Bonjour, je suis prêt.")))
            if audio.size < tts.sample_rate // 2:
                failures.append(f"voix Piper muette ({audio.size} échantillons)")
            wake, vad = build_wakeword(cfg), build_vad(cfg)
            wake.process(np.zeros(16000, np.int16))
            vad(np.zeros(512, np.float32))
            LOG.info("Autotest : voix Piper %.1f s, mot d'activation et détecteur de voix chargés",
                     audio.size / tts.sample_rate)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"exécution : {exc!r}")
    for failure in failures:
        LOG.error("Autotest : %s", failure)
    LOG.info("Autotest %s (%d modules, dossier de données %s)", "échoué" if failures else "réussi",
             len(modules), paths.data_dir())
    return 1 if failures else 0
