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
    num_ctx: int = 8192            # mesuré : outils + prompt = 2 900 tokens en contexte éditeur, 4 096 débordait
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
class ToolsConfig:
    enabled: bool = True
    always_allow: list[str] = field(default_factory=list)   # actions N2 autorisées sans confirmation
    disabled: list[str] = field(default_factory=list)       # actions interdites
    confirm_timeout_s: float = 6.0


@dataclass
class ScreenConfig:
    enabled: bool = True           # analyse de l'écran en continu, par le modèle de vision local
    interval_s: float = 45.0       # une analyse au plus toutes les N secondes, et seulement si l'écran a changé
    min_free_gb: float = 1.5       # en dessous de cette mémoire libre, l'analyse attend (le Mac swappe sinon)
    max_width: int = 1024          # largeur de la capture envoyée au modèle (plus petit = plus rapide)
    model: str = ""                # vide = le modèle Ollama principal (il doit gérer les images)


@dataclass
class BrowserConfig:
    enabled: bool = True           # pilotage du navigateur : extension Jarvis, ou Safari par AppleScript
    port: int = 47831              # port local fixe du pont, connu de l'extension installée


@dataclass
class ReviewConfig:
    claude_model: str = "sonnet"   # review en arrière-plan : la qualité compte plus que la vitesse
    max_chars: int = 120_000       # modèle local : code relu au plus (~14 passes avec un contexte de 4096)
    timeout_s: float = 900.0       # Claude : durée maximale d'une review


@dataclass
class TtsConfig:
    backend: str = "auto"          # auto (Pocket TTS, Piper si la machine est trop lente) | pocket | piper
    voice: str = "fantine"         # Pocket TTS : fantine (la plus fiable), cosette, marius, jean… ou un .wav
    temperature: float = 0.5       # Pocket TTS : plus bas = plus stable, plus haut = plus expressif
    piper_voice: str = "fr_FR-siwis-medium"
    length_scale: float = 1.0      # Piper : < 1 parle plus vite


@dataclass
class AudioConfig:
    input_device: int | str | None = None
    output_device: int | str | None = None
    follow_up_s: float = 4.0       # écoute une relance sans « Hey Jarvis » (0 = désactivé)


@dataclass
class UiConfig:
    window: str = "app"            # app (fenêtre Jarvis) | browser | none
    port: int = 0                  # 0 = port libre choisi au démarrage


@dataclass
class Config:
    user_name: str = ""
    wakeword: WakeWordConfig = field(default_factory=WakeWordConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    ui: UiConfig = field(default_factory=UiConfig)

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


LOADED_PATH: Path | None = None


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


def _merge(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def load(path: Path | None = None) -> Config:
    global LOADED_PATH
    config = Config()
    path = path or paths.config_path()
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} : la configuration doit être un dictionnaire YAML")
        _apply(config, data)
    LOADED_PATH = path
    return config


def update_file(updates: dict[str, Any], path: Path | None = None) -> Path:
    """Fusionne des réglages dans config.yaml ; les autres valeurs du fichier sont conservées."""
    _apply(Config(), updates)   # refuse les clés inconnues avant d'écrire quoi que ce soit
    path = path or LOADED_PATH or paths.config_path()
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _merge(data, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
