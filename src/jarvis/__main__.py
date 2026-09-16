"""Point d'entrée : `jarvis [run | hud | extension | code | bench | doctor | setup | devices]`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path

from . import __version__
from . import config as config_module

LOG = logging.getLogger("jarvis")


def _run(cfg: config_module.Config) -> int:
    from . import prompts
    from .app import build_executor, load_all
    from .audio.io import Microphone, Player
    from .events import EventBus
    from .pipeline import Assistant
    from .server import start_server
    from .ui.controller import Controller
    from .ui.window import open_window

    bus = EventBus()
    controller = Controller(cfg, bus)
    executor = build_executor(cfg)
    server = start_server(executor, cfg.ui.port, controller)
    failure: list[BaseException] = []

    def voice_loop() -> None:
        try:
            LOG.info("Chargement des modèles…")
            system = prompts.system_prompt(cfg.user_name, date.today(), tools=cfg.tools.enabled)
            parts = load_all(cfg, system, bus=bus, executor=executor, server=server)
            with Microphone(cfg.audio.input_device) as mic, \
                    Player(cfg.audio.output_device, parts.tts.sample_rate) as player:
                assistant = Assistant(cfg, parts, mic, player, bus)
                controller.attach(parts, assistant)
                if cfg.ui.hotkey:
                    from .system.hotkey import Hotkey
                    try:
                        shortcut = Hotkey(cfg.ui.hotkey, assistant.wake).start()
                        if shortcut.error:
                            bus.publish("error", text=f"Raccourci {cfg.ui.hotkey} : {shortcut.error}")
                    except ValueError as exc:
                        bus.publish("error", text=str(exc))
                assistant.run()
        except Exception as exc:  # noqa: BLE001 - affiché dans l'interface et le terminal
            failure.append(exc)
            LOG.error("%s", exc)
            bus.publish("error", text=str(exc))

    if cfg.ui.window == "none":
        voice_loop()
    else:
        worker = threading.Thread(target=voice_loop, name="voix", daemon=True)
        worker.start()
        LOG.info("Interface : %s", server.url)
        if cfg.ui.window == "app" and open_window(server.ui_url):
            os._exit(1 if failure else 0)       # fenêtre fermée : on coupe micro et modèles sans attendre
        webbrowser.open(server.ui_url)
        while worker.is_alive():
            worker.join(timeout=0.5)
    if failure:
        raise RuntimeError(str(failure[0]))
    return 0


def _hud_demo(cfg: config_module.Config) -> int:
    from .events import EventBus
    from .server import start_server
    from .tools import ToolExecutor
    from .ui.controller import Controller
    from .ui.window import open_window

    server = start_server(ToolExecutor(cfg.tools), 0, Controller(cfg, EventBus()))
    url = f"{server.ui_url}&demo=1"
    if open_window(url):
        return 0
    webbrowser.open(url)
    print(f"Aperçu de l'interface : {url} (Ctrl+C pour quitter)")
    while True:
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis", description="Assistant vocal local, rapide, Windows et macOS.")
    parser.add_argument("--config", help="chemin d'un config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="journal détaillé")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="lance l'assistant (par défaut)")
    run.add_argument("--backend", choices=["ollama", "claude"], help="moteur au démarrage")
    run.add_argument("--ui", choices=["app", "browser", "none"], help="affichage de l'interface")
    sub.add_parser("hud", help="aperçu de l'interface avec des données simulées")
    sub.add_parser("extension", help="prépare l'extension Jarvis pour Chrome, Edge, Brave, Opera, Arc et Firefox")
    sub.add_parser("code", help="empaquette l'extension VS Code de Jarvis et l'installe (VS Code, Cursor, Windsurf…)")
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

    from . import logs
    log_path = logs.setup(args.verbose)

    try:
        cfg = config_module.load(Path(args.config) if args.config else None)
        if getattr(args, "backend", None):
            cfg.llm.backend = args.backend
        if getattr(args, "ui", None):
            cfg.ui.window = args.ui
        if args.command == "bench":
            if args.llm_model:
                cfg.llm.model = args.llm_model
            if args.claude_model:
                cfg.claude.model = args.claude_model
            if args.stt_model:
                cfg.stt.model = args.stt_model
        cfg.resolve()

        if args.command == "hud":
            return _hud_demo(cfg)
        if args.command == "extension":
            from .browser.install import run as install_extension
            return install_extension(cfg.browser.port)
        if args.command == "code":
            from .editor.install import run as install_editor
            return install_editor()
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
        LOG.error("%s", exc)
        print(f"❌ {exc}\n   Journal : {log_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
