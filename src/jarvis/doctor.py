"""`jarvis doctor` (diagnostic) et `jarvis setup` (téléchargement des modèles)."""
from __future__ import annotations

import importlib.util
import platform
import sys

import httpx

from . import assets, hardware
from .config import Config
from .paths import models_dir

_SYMBOLS = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}


def doctor(cfg: Config) -> int:
    failures = 0

    def line(status: str, label: str, detail: str = "") -> None:
        nonlocal failures
        failures += status == "fail"
        print(f"{_SYMBOLS[status]} {label}" + (f" — {detail}" if detail else ""))

    hw = hardware.detect()
    line("ok", "Matériel", f"{hw} → {hardware.recommend(hw).reason}")
    line("ok" if sys.version_info >= (3, 12) else "fail", "Python", platform.python_version())

    from .llm import load_backend
    engines = (("ollama", f"LLM local (Ollama {cfg.llm.model})"),
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
        line("ok", "Analyse d'écran", f"toutes les {cfg.screen.interval_s:g} s au plus, modèle local "
             f"{cfg.screen.model or cfg.llm.model}")

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
