"""`jarvis doctor` (diagnostic) et `jarvis setup` (téléchargement des modèles)."""
from __future__ import annotations

import importlib.util
import platform
import sys

import httpx

from . import assets, hardware, paths
from .config import Config
from .paths import models_dir
from .system import IS_MAC, IS_WINDOWS

_SYMBOLS = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}


def doctor(cfg: Config) -> int:
    failures = 0

    def line(status: str, label: str, detail: str = "") -> None:
        nonlocal failures
        failures += status == "fail"
        print(f"{_SYMBOLS[status]} {label}" + (f" — {detail}" if detail else ""))

    from .logs import logs_dir
    line("ok", "Journal", f"{logs_dir() / 'jarvis.log'} (crash.log à côté en cas de plantage natif)")
    hw = hardware.detect()
    line("ok", "Matériel", f"{hw} → {hardware.recommend(hw).reason}")
    line("ok" if sys.version_info >= (3, 12) else "fail", "Python", platform.python_version())
    if paths.store_python():
        line("fail", "Python du Store", paths.STORE_PYTHON_HINT)

    from .llm import load_backend
    where = f"Ollama distant {cfg.llm.host}" if cfg.llm.remote else "Ollama"
    engines = (("ollama", f"LLM {'local ' if not cfg.llm.remote else ''}({where}, {cfg.llm.model})"),
               ("claude", f"Claude via Claude Code ({cfg.claude.model}, {cfg.claude.auth})"))
    for name, label in engines:
        active = name == cfg.llm.backend
        try:
            load_backend(cfg, name).check()
            line("ok", label, "moteur au démarrage" if active else "disponible à la voix")
        except RuntimeError as exc:
            line("fail" if active else "warn", label, str(exc))

    needed = [assets.SILERO_VAD, assets.MELSPEC, assets.EMBEDDING, assets.WAKEWORDS.get(cfg.wakeword.model)]
    missing = [a.path for a in needed if a is not None and not (models_dir() / a.path).exists()]
    line("ok" if not missing else "fail", "Modèles d'écoute",
         "présents" if not missing else f"manquants : {', '.join(missing)} → `jarvis setup`")

    pocket = importlib.util.find_spec("pocket_tts") is not None
    if cfg.tts.backend in ("auto", "pocket"):
        line("ok" if pocket else ("fail" if cfg.tts.backend == "pocket" else "warn"), "Voix naturelle (Pocket TTS)",
             f"voix « {cfg.tts.voice} »" if pocket else "paquet pocket-tts absent : `uv sync`")
    piper_ok = (models_dir() / "piper" / f"{cfg.tts.piper_voice}.onnx").exists()
    line("ok" if piper_ok else "warn", "Voix légère (Piper)",
         cfg.tts.piper_voice if piper_ok else "pas encore téléchargée → `jarvis setup`")

    if cfg.screen.enabled:
        from .vision.screen import screen_host
        host = screen_host(cfg)
        from .config import is_local_host
        line("ok" if is_local_host(host) else "warn", "Analyse d'écran",
             f"toutes les {cfg.screen.interval_s:g} s au plus, modèle {cfg.screen.model or cfg.llm.model} sur "
             + ("cette machine" if is_local_host(host) else f"{host} : tes captures partent sur le réseau"))
    from .system.hotkey import parse as parse_hotkey
    if cfg.ui.hotkey:
        try:
            parse_hotkey(cfg.ui.hotkey)
            line("ok", "Raccourci clavier", f"{cfg.ui.hotkey} réveille Jarvis" + (
                " (macOS demandera « Surveillance de l'entrée » au premier lancement)" if IS_MAC else ""))
        except ValueError as exc:
            line("fail", "Raccourci clavier", str(exc))
    from .automations import Automations
    try:
        checker = Automations(path=models_dir().parent / "automations.json")
        checker.load_config(cfg.automations)
        spoken = [a for a in checker.all() if a.source == "voice"]
        line("ok", "Automatisations", f"{len(cfg.automations)} dans config.yaml, {len(spoken)} créées à la voix"
             if cfg.automations or spoken else "aucune (« tous les jours à 8 heures, rappelle-moi de… »)")
    except ValueError as exc:
        line("fail", "Automatisations", str(exc))
    from .custom import load_commands
    try:
        customs = load_commands(cfg)
        line("ok", "Commandes personnalisées", f"{len(customs)} définies" if customs else
             "aucune (section `commands:` de config.yaml)")
    except ValueError as exc:
        line("fail", "Commandes personnalisées", str(exc))
    if cfg.browser.enabled:
        built = (models_dir().parent / "extension" / "chromium" / "config.js").exists()
        line("ok" if built else "warn", "Pilotage du navigateur",
             f"extension prête (port {cfg.browser.port}), à charger dans ton navigateur" if built
             else "lance `jarvis extension`, puis charge l'extension dans ton navigateur")
        from .editor.install import find_editors
        editors = [name for name, _ in find_editors()]
        vsix = any((models_dir().parent / "editor").glob("*.vsix"))
        line("ok" if vsix else "warn", "Pilotage de l'éditeur (VS Code)",
             (f"extension prête pour {', '.join(editors)}" if editors else "extension prête, aucun éditeur trouvé")
             if vsix else "lance `jarvis code` pour l'installer dans " + (", ".join(editors) or "ton éditeur"))

    if IS_MAC:
        try:
            from ApplicationServices import AXIsProcessTrusted
            trusted = bool(AXIsProcessTrusted())
        except ImportError:
            trusted = False
        line("ok" if trusted else "warn", "Lecture des applications (Accessibilité)",
             "autorisée" if trusted else "Réglages Système › Confidentialité et sécurité › Accessibilité : "
             "active ton terminal")
    elif IS_WINDOWS:
        found = importlib.util.find_spec("uiautomation") is not None
        line("ok" if found else "warn", "Lecture des applications (UI Automation)",
             "disponible" if found else "paquet uiautomation absent : `uv sync`")

    import sounddevice as sd
    for kind, device, label in (("input", cfg.audio.input_device, "Micro"),
                                ("output", cfg.audio.output_device, "Sortie audio")):
        try:
            line("ok", label, sd.query_devices(device, kind)["name"])
        except Exception as exc:  # aucun périphérique, index invalide…
            line("fail", label, str(exc))

    return 1 if failures else 0


def setup(cfg: Config) -> int:
    print("Modèles d'écoute (VAD, mot d'activation) et voix de secours…")
    assets.ensure(assets.SILERO_VAD)
    assets.wakeword(cfg.wakeword.model)
    assets.piper_voice(cfg.tts.piper_voice)

    print("Voix…")
    from .app import build_tts
    print(f"  prête : {build_tts(cfg).name}")

    import ollama
    print(f"LLM local {cfg.llm.model} via Ollama…")
    try:
        for progress in ollama.Client(host=cfg.llm.host).pull(cfg.llm.model, stream=True):
            if progress.total and progress.completed:
                print(f"\r  {progress.status} {progress.completed * 100 // progress.total} %", end="", flush=True)
            else:
                print(f"\r  {progress.status}", end="", flush=True)
        print()
    except (httpx.HTTPError, ConnectionError) as exc:
        message = f"Ollama ne répond pas ({exc}) : installe-le puis lance-le."
        if cfg.llm.backend == "ollama" or cfg.claude.fallback_to_local:
            raise RuntimeError(message) from exc
        print(f"  ⚠️  {message} (optionnel ici : tu utilises Claude sans repli local)")

    print(f"Transcription {cfg.stt.model}…")
    from .stt import load_stt
    load_stt(cfg.stt).warmup()

    from .llm import load_backend
    try:
        load_backend(cfg, "claude").check()
        print("Claude Code : connecté, « passe sur Claude » fonctionnera.")
    except RuntimeError as exc:
        print(f"Claude Code ({'requis' if cfg.llm.backend == 'claude' else 'optionnel'}) : {exc}")
    print("✅ Tout est prêt : `jarvis run`.")
    return 0
