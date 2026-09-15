"""`jarvis doctor` (diagnostic) et `jarvis setup` (téléchargement des modèles)."""
from __future__ import annotations

import platform
import sys

import httpx

from . import assets, hardware
from .config import Config
from .paths import models_dir


def _line(ok: bool, label: str, detail: str = "") -> bool:
    print(f"{'✅' if ok else '❌'} {label}" + (f" — {detail}" if detail else ""))
    return ok


def doctor(cfg: Config) -> int:
    hw = hardware.detect()
    results = [
        _line(True, "Matériel", f"{hw} → {hardware.recommend(hw).reason}"),
        _line(sys.version_info >= (3, 12), "Python", platform.python_version()),
    ]

    from .llm import load_llm
    try:
        load_llm(cfg.llm).check()
        results.append(_line(True, f"LLM ({cfg.llm.backend})", cfg.llm.model))
    except RuntimeError as exc:
        results.append(_line(False, f"LLM ({cfg.llm.backend})", str(exc)))

    needed = [assets.SILERO_VAD, assets.MELSPEC, assets.EMBEDDING, assets.WAKEWORDS.get(cfg.wakeword.model)]
    missing = [a.path for a in needed if a is not None and not (models_dir() / a.path).exists()]
    if not (models_dir() / "piper" / f"{cfg.tts.voice}.onnx").exists():
        missing.append(f"piper/{cfg.tts.voice}.onnx")
    results.append(_line(not missing, "Modèles audio",
                         "présents" if not missing else f"manquants : {', '.join(missing)} → `jarvis setup`"))

    import sounddevice as sd
    for kind, device, label in (("input", cfg.audio.input_device, "Micro"),
                                ("output", cfg.audio.output_device, "Sortie audio")):
        try:
            results.append(_line(True, label, sd.query_devices(device, kind)["name"]))
        except Exception as exc:  # aucun périphérique, index invalide…
            results.append(_line(False, label, str(exc)))

    return 0 if all(results) else 1


def setup(cfg: Config) -> int:
    print("Modèles audio (VAD, mot d'activation, voix)…")
    assets.ensure(assets.SILERO_VAD)
    assets.wakeword(cfg.wakeword.model)
    assets.piper_voice(cfg.tts.voice)

    if cfg.llm.backend == "ollama":
        import ollama
        print(f"LLM {cfg.llm.model} via Ollama…")
        try:
            for progress in ollama.Client(host=cfg.llm.host).pull(cfg.llm.model, stream=True):
                if progress.total and progress.completed:
                    print(f"\r  {progress.status} {progress.completed * 100 // progress.total} %", end="", flush=True)
                else:
                    print(f"\r  {progress.status}", end="", flush=True)
            print()
        except (httpx.HTTPError, ConnectionError) as exc:
            raise RuntimeError(f"Ollama ne répond pas ({exc}) : installe-le puis lance-le.") from exc

    print(f"Transcription {cfg.stt.model}…")
    from .stt import load_stt
    load_stt(cfg.stt).warmup()
    print("✅ Tout est prêt : `jarvis run`.")
    return 0
