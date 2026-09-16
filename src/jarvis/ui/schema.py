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


VOICE_OPTIONS = (
    ("fantine", "Fantine · féminine, la plus fiable (recommandée)"),
    ("cosette", "Cosette · féminine, parfois imprécise"),
    ("marius", "Marius · masculine, instable sur les phrases courtes"),
    ("jean", "Jean · masculine grave, instable sur les phrases courtes"),
    ("javert", "Javert · masculine, instable sur les phrases courtes"),
    ("alba", "Alba · médium, expérimentale"),
)

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
        Field("wakeword.threshold", "Sensibilité de « Hey Jarvis »", "slider", min=0.15, max=0.9, step=0.05,
              help="Plus bas : il t'entend plus facilement, même dans le bruit ou pendant qu'il parle. "
                   "0,25 par défaut, mesuré sans aucun réveil intempestif sur cinq minutes de parole et de bruit."),
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
    ("screen", "Écran", "Jarvis observe ce que tu fais pour mieux t'aider. Les captures restent en mémoire, "
     "sur ta machine, et ne sont décrites que par le modèle local.", [
        Field("screen.enabled", "Analyse de l'écran en continu", "toggle",
              help="Désactive-la avant d'afficher quelque chose de confidentiel."),
        Field("screen.interval_s", "Fréquence d'analyse", "slider", min=5, max=120, step=5, unit="s",
              help="Seulement si l'écran a changé, et jamais pendant une conversation."),
        Field("screen.max_width", "Précision de la capture", "slider", min=640, max=1600, step=64, unit="px",
              help="Plus large : texte mieux lu, analyse plus lente."),
        Field("screen.host", "Serveur Ollama pour l'écran", "text", live=False,
              help="Vide : l'Ollama de cette machine, les captures ne partent jamais sur le réseau. Mets l'adresse "
                   "du serveur distant seulement si tu acceptes de lui envoyer tes captures."),
        Field("screen.min_free_gb", "Mémoire libre minimale", "slider", min=0, max=4, step=0.5, unit="Go",
              help="En dessous, l'analyse attend : elle ferait swapper la machine. 0 pour ne jamais attendre."),
    ]),
    ("voice", "Voix", "Comment Jarvis te parle.", [
        Field("tts.backend", "Moteur de voix", "select", live=False, options=(
            ("auto", "Automatique · naturelle, Piper si la machine est lente"),
            ("pocket", "Pocket TTS · naturelle"),
            ("piper", "Piper · la plus légère"))),
        Field("tts.voice", "Voix naturelle", "select", options=VOICE_OPTIONS,
              help="Change instantanément, sans redémarrer."),
        Field("tts.piper_voice", "Voix légère (Piper)", "select", live=False, options=(
            ("fr_FR-siwis-medium", "Siwis · féminine"),
            ("fr_FR-tom-medium", "Tom · masculine"),
            ("fr_FR-upmc-medium", "UPMC · alternative"))),
        Field("tts.length_scale", "Débit de la voix Piper", "slider", min=0.7, max=1.3, step=0.05,
              help="En dessous de 1, Piper parle plus vite."),
    ]),
    ("engines", "Moteurs", "Réglages fins des modèles.", [
        Field("llm.host", "Serveur Ollama", "text",
              help="Cette machine (http://127.0.0.1:11434) ou une autre du réseau, ex. http://192.168.1.20:11434 "
                   "(Ollama doit y écouter avec OLLAMA_HOST=0.0.0.0)."),
        Field("llm.model", "Modèle Ollama", "text",
              help="Doit être installé sur le serveur : `ollama pull <modèle>`. Ex. qwen3.5:4b-mlx, qwen3.5:9b. "
                   "« auto » sur un serveur distant prend le plus gros qwen3.5 présent."),
        Field("llm.keep_alive", "Garder le modèle en mémoire", "select", live=False,
              help="Le modèle local occupe environ 3,6 Go. Plus court : la mémoire revient plus vite, mais "
                   "la première question après une pause attend son rechargement (mesuré : 1,3 s).",
              options=(("5m", "5 minutes · mémoire libérée vite"), ("30m", "30 minutes · recommandé"),
                       ("2h", "2 heures"), ("-1", "toujours · jamais déchargé"))),
        Field("llm.free_on_claude", "Libérer la mémoire en passant sur Claude", "toggle", live=True,
              help="Quand tu dis « passe sur Claude », le modèle local rend ses 3,6 Go."),
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
    ("review", "Review de code", "« Fais une review de mon code » : le projet ouvert dans ton éditeur est relu en "
     "arrière-plan, par le moteur actif ; le résumé est dit à voix haute, le rapport arrive dans le journal.", [
        Field("review.claude_model", "Modèle Claude pour la review", "select", options=(
            ("haiku", "Haiku · rapide, review superficielle"),
            ("sonnet", "Sonnet · recommandé"),
            ("opus", "Opus · le plus poussé, consomme davantage"))),
        Field("review.max_chars", "Code relu par le modèle local", "slider", min=20_000, max=400_000, step=20_000,
              unit="caractères", help="Au-delà, la review locale est partielle et le rapport le signale."),
        Field("review.timeout_s", "Durée maximale avec Claude", "slider", min=120, max=1800, step=60, unit="s"),
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
