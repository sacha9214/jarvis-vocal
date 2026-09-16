"""Journal sur disque : sans lui, un plantage ne laisse aucune trace.

`logs/jarvis.log` (tournant, 3 × 5 Mo) reçoit tout ce qui s'affiche dans le terminal ; `logs/crash.log`
reçoit la pile Python si le processus meurt d'un plantage natif (MLX, torch, ONNX, PortAudio) ; les
exceptions non rattrapées des fils secondaires, qui tuaient un fil en silence, sont journalisées.
"""
from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import data_dir

LOG = logging.getLogger("jarvis")
_crash_file = None   # gardé ouvert : faulthandler écrit dedans au moment du plantage


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup(verbose: bool = False) -> Path:
    """Journal terminal + fichier ; renvoie le chemin du fichier."""
    global _crash_file
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)
    folder = logs_dir()
    path = folder / "jarvis.log"
    try:
        file = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s"))
        root.addHandler(file)
        _crash_file = open(folder / "crash.log", "a", encoding="utf-8")  # noqa: SIM115 - vit autant que le processus
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError as exc:
        LOG.warning("Journal sur disque impossible (%s) : terminal seulement.", exc)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "mcp", "uvicorn"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        LOG.error("Fil « %s » mort sur une exception non rattrapée", args.thread.name if args.thread else "?",
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def main_hook(exc_type, exc_value, exc_traceback) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            LOG.critical("Jarvis s'arrête sur une exception", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    threading.excepthook = thread_hook
    sys.excepthook = main_hook
    return path
