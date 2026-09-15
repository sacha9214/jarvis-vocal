"""Réglages modifiables depuis l'interface : libellés, bornes, et application en direct ou au redémarrage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str                     # text | select | slider | toggle
    live: bool = True             # appliqué sans redémarrer
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    options: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type, "live": self.live,
                                "help": self.help}
        if self.type == "slider":
            data.update(min=self.min, max=self.max, step=self.step, unit=self.unit)
        if self.options:
            data["options"] = [list(option) for option in self.options]
        return data


SECTIONS: list[tuple[str, str, str, list[Field]]] = [
    ("general", "Général", "Qui tu es et avec quel moteur Jarvis réfléchit.", [
        Field("user_name", "Ton prénom", "text", help="Jarvis s'adresse à toi par ce prénom."),
        Field("llm.backend", "Moteur au démarrage", "select",
              help="Tu peux aussi dire « passe sur Claude » ou « passe en local ».",
              options=(("ollama", "Local (Ollama)"), ("claude", "Claude (ton abonnement)"))),
        Field("llm.history_turns", "Mémoire de la conversation", "slider", min=0, max=20, step=1, unit="échanges",
              help="Nombre d'échanges récents que Jarvis garde en tête."),
        Field("ui.window", "Affichage de l'interface", "select", live=False,
              options=(("app", "Fenêtre Jarvis"), ("browser", "Navigateur"), ("none", "Aucune"))),
    ]),
    ("listening", "Écoute", "Réactivité et sensibilité du micro.", [
        Field("wakeword.threshold", "Sensibilité de « Hey Jarvis »", "slider", min=0.2, max=0.9, step=0.05,
              help="Plus haut : moins de réveils intempestifs, mais il faut articuler."),
        Field("vad.end_silence_ms", "Silence de fin de phrase", "slider", min=250, max=1500, step=25, unit="ms",
              help="Plus bas : réponse plus rapide, mais Jarvis peut te couper si tu hésites."),
        Field("vad.threshold", "Seuil de détection de la voix", "slider", min=0.2, max=0.9, step=0.05,
              help="Monte-le dans une pièce bruyante."),
        Field("vad.start_timeout_s", "Délai pour commencer à parler", "slider", min=2, max=15, step=0.5, unit="s"),
        Field("audio.follow_up_s", "Relance sans « Hey Jarvis »", "slider", min=0, max=10, step=0.5, unit="s",
              help="Après une réponse, Jarvis écoute encore ce temps-là. 0 pour désactiver."),
        Field("tools.confirm_timeout_s", "Temps pour confirmer une action", "slider",
              min=3, max=15, step=0.5, unit="s"),
        Field("stt.model", "Modèle de transcription", "text", live=False,
              help="Ex. mlx-community/whisper-large-v3-turbo-q4 (Mac), large-v3-turbo ou small (Windows)."),
    ]),
    ("voice", "Voix", "Comment Jarvis te parle.", [
        Field("tts.voice", "Voix", "select", live=False, options=(
            ("fr_FR-siwis-medium", "Siwis · féminine, la plus rapide"),
            ("fr_FR-tom-medium", "Tom · masculine"),
            ("fr_FR-upmc-medium", "UPMC · alternative"))),
        Field("tts.length_scale", "Débit", "slider", min=0.7, max=1.3, step=0.05,
              help="En dessous de 1, Jarvis parle plus vite."),
    ]),
    ("engines", "Moteurs", "Réglages fins des modèles.", [
        Field("llm.model", "Modèle local (Ollama)", "text",
              help="Doit être installé : `ollama pull <modèle>`. Ex. qwen3.5:4b-mlx, qwen3.5:2b."),
        Field("llm.temperature", "Créativité du modèle local", "slider", min=0, max=1.2, step=0.05),
        Field("llm.max_tokens", "Longueur maximale d'une réponse", "slider",
              min=64, max=1024, step=16, unit="tokens"),
        Field("claude.model", "Modèle Claude", "select", options=(
            ("haiku", "Haiku · le plus rapide"),
            ("sonnet", "Sonnet · plus réfléchi"),
            ("opus", "Opus · le plus puissant"))),
        Field("claude.effort", "Réflexion de Claude", "select", options=(
            ("", "Par défaut"), ("low", "Faible · plus rapide"), ("medium", "Moyenne"), ("high", "Élevée"))),
        Field("claude.fallback_to_local", "Repli local si Claude échoue", "toggle"),
    ]),
]
FIELDS = {field.key: field for _, _, _, fields in SECTIONS for field in fields}
_LIVE_EXTRA = {"tools.enabled", "tools.always_allow", "tools.disabled"}   # gérés par l'onglet Permissions


def sections() -> list[dict[str, Any]]:
    return [{"id": sid, "title": title, "intro": intro, "fields": [f.to_dict() for f in fields]}
            for sid, title, intro, fields in SECTIONS]


def flatten(updates: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in updates.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def is_live(key: str) -> bool:
    field = FIELDS.get(key)
    return field.live if field else key in _LIVE_EXTRA


def validate(flat: dict[str, Any], tool_names: set[str]) -> None:
    for key, value in flat.items():
        if key in ("tools.always_allow", "tools.disabled"):
            if not isinstance(value, list) or not all(isinstance(v, str) and v in tool_names for v in value):
                raise ValueError(f"{key} : liste d'actions invalide")
            continue
        if key == "tools.enabled":
            if not isinstance(value, bool):
                raise ValueError("tools.enabled doit valoir vrai ou faux")
            continue
        field = FIELDS.get(key)
        if field is None:
            raise ValueError(f"Réglage non modifiable depuis l'interface : {key}")
        if field.type == "slider":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{field.label} : nombre attendu")
            if not field.min <= value <= field.max:  # type: ignore[operator]
                raise ValueError(f"{field.label} : entre {field.min:g} et {field.max:g}")
        elif field.type == "toggle" and not isinstance(value, bool):
            raise ValueError(f"{field.label} : vrai ou faux attendu")
        elif field.type == "select" and value not in {v for v, _ in field.options}:
            raise ValueError(f"{field.label} : choix inconnu")
        elif field.type == "text" and (not isinstance(value, str) or len(value) > 200):
            raise ValueError(f"{field.label} : texte de 200 caractères maximum")
