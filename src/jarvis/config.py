"""Configuration : valeurs par défaut adaptées au matériel, surchargées par config.yaml."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import hardware, paths


@dataclass
class WakeWordConfig:
    model: str = "hey_jarvis"
    threshold: float = 0.5


@dataclass
class VadConfig:
    threshold: float = 0.5
    end_silence_ms: int = 550      # silence qui clôt ta phrase : le premier levier de latence
    preroll_ms: int = 250          # audio gardé avant le début détecté (première syllabe)
    start_timeout_s: float = 5.0   # après « Hey Jarvis », délai pour commencer à parler
    max_utterance_s: float = 15.0


@dataclass
class SttConfig:
    backend: str = "auto"          # auto | mlx | faster-whisper
    model: str = "auto"
    device: str = "auto"           # auto | metal | cuda | cpu
    compute_type: str = "auto"
    language: str = "fr"


@dataclass
class LlmConfig:
    backend: str = "ollama"        # moteur au démarrage : ollama (local) | claude
    model: str = "auto"            # modèle Ollama
    host: str = "http://127.0.0.1:11434"
    num_ctx: int = 4096            # contexte court = pré-remplissage rapide et moins de mémoire
    max_tokens: int = 320
    temperature: float = 0.6
    keep_alive: str = "30m"        # le modèle reste chargé entre deux questions
    history_turns: int = 6


@dataclass
class ClaudeConfig:
    model: str = "haiku"           # haiku : le plus rapide à répondre ; sonnet pour les questions difficiles
    auth: str = "subscription"     # subscription (ton abonnement, `claude auth login`) | api_key (ANTHROPIC_API_KEY)
    executable: str = "claude"
    effort: str = ""               # vide = défaut du modèle ; low réduit la réflexion des modèles qui en font
    fallback_to_local: bool = True
    first_token_timeout_s: float = 30.0


@dataclass
class TtsConfig:
    backend: str = "piper"
    voice: str = "fr_FR-siwis-medium"
    length_scale: float = 1.0      # < 1 : parle plus vite


@dataclass
class AudioConfig:
    input_device: int | str | None = None
    output_device: int | str | None = None
    follow_up_s: float = 4.0       # écoute une relance sans « Hey Jarvis » (0 = désactivé)


@dataclass
class Config:
    user_name: str = ""
    wakeword: WakeWordConfig = field(default_factory=WakeWordConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)

    def resolve(self, hw: hardware.Hardware | None = None) -> Config:
        """Remplace les « auto » par le plan recommandé pour ce matériel."""
        hw = hw or hardware.detect()
        plan = hardware.recommend(hw)
        stt = self.stt
        if stt.backend == "auto":
            stt.backend = plan.stt_backend
        same_backend = stt.backend == plan.stt_backend
        if stt.model == "auto":
            stt.model = plan.stt_model if same_backend else (
                hardware.MLX_WHISPER if stt.backend == "mlx" else "small")
        if stt.device == "auto":
            stt.device = plan.stt_device if same_backend else ("cuda" if hw.cuda_vram_gb else "cpu")
        if stt.compute_type == "auto":
            stt.compute_type = plan.stt_compute if same_backend else (
                "float16" if stt.device in ("cuda", "metal") else "int8")
        if self.llm.model == "auto":
            self.llm.model = plan.llm_model
        return self


def _apply(target: Any, data: dict[str, Any], where: str = "") -> None:
    fields = {f.name for f in dataclasses.fields(target)}
    for key, value in data.items():
        if key not in fields:
            raise ValueError(f"Clé inconnue dans la configuration : {where}{key}")
        current = getattr(target, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{where}{key} doit être une section (clé: valeur)")
            _apply(current, value, f"{where}{key}.")
        else:
            setattr(target, key, value)


def load(path: Path | None = None) -> Config:
    config = Config()
    path = path or paths.config_path()
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} : la configuration doit être un dictionnaire YAML")
        _apply(config, data)
    return config
