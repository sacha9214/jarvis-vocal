"""Point d'entrée : `jarvis [run | bench | doctor | setup | devices]`."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from . import __version__
from . import config as config_module

LOG = logging.getLogger("jarvis")


def _run(cfg: config_module.Config) -> int:
    from . import prompts
    from .app import load_all
    from .audio.io import Microphone, Player
    from .pipeline import Assistant

    LOG.info("Chargement des modèles…")
    parts = load_all(cfg, prompts.system_prompt(cfg.user_name, date.today()))
    with Microphone(cfg.audio.input_device) as mic, Player(cfg.audio.output_device, parts.tts.sample_rate) as player:
        Assistant(cfg, parts, mic, player).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Assistant vocal local, rapide, Windows et macOS.")
    parser.add_argument("--config", help="chemin d'un config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="journal détaillé")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="lance l'assistant (par défaut)")
    run.add_argument("--backend", choices=["ollama", "claude"], help="moteur au démarrage")
    bench = sub.add_parser("bench", help="mesure la latence de chaque étage, sans micro")
    bench.add_argument("--backend", choices=["ollama", "claude"], help="moteur à mesurer")
    bench.add_argument("--llm-model", help="modèle Ollama, ex. qwen3.5:2b")
    bench.add_argument("--claude-model", help="modèle Claude, ex. haiku ou sonnet")
    bench.add_argument("--stt-model", help="ex. mlx-community/whisper-large-v3-turbo")
    bench.add_argument("--repeats", type=int, default=3)
    sub.add_parser("doctor", help="vérifie l'installation")
    sub.add_parser("setup", help="télécharge les modèles")
    sub.add_parser("devices", help="liste les périphériques audio")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        cfg = config_module.load(Path(args.config) if args.config else None)
        if getattr(args, "backend", None):
            cfg.llm.backend = args.backend
        if args.command == "bench":
            if args.llm_model:
                cfg.llm.model = args.llm_model
            if args.claude_model:
                cfg.claude.model = args.claude_model
            if args.stt_model:
                cfg.stt.model = args.stt_model
        cfg.resolve()

        if args.command == "bench":
            from .bench import run as run_bench
            return run_bench(cfg, args.repeats)
        if args.command == "doctor":
            from .doctor import doctor
            return doctor(cfg)
        if args.command == "setup":
            from .doctor import setup
            return setup(cfg)
        if args.command == "devices":
            import sounddevice as sd
            print(sd.query_devices())
            return 0
        return _run(cfg)
    except KeyboardInterrupt:
        print("\nÀ plus.")
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
