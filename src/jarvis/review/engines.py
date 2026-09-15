"""Moteurs de review.

Claude : le binaire `claude` explore le projet lui-même, en lecture seule (Read, Grep, Glob ; ni terminal
ni écriture), lancé depuis un dossier vide avec le projet en dossier autorisé : les réglages et serveurs MCP
du projet ne se chargent pas, et rien hors du projet n'est lisible.
Local : le contexte d'un petit modèle est court, on relit donc par passes, puis on fusionne les notes. Un
modèle de 4 milliards de paramètres invente volontiers des problèmes : la consigne n'accepte que des bugs
prouvables par une ligne précise, et « RAS » est présenté comme la réponse normale.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .collect import Material, chunks, diff_files, numbered

if TYPE_CHECKING:
    from ..config import Config

REPORT_FORMAT = (
    "Rédige le rapport en français, en markdown, dans cet ordre :\n"
    "RÉSUMÉ : deux ou trois phrases simples qui seront lues à voix haute (ni markdown, ni chemin de fichier), "
    "en commençant par le problème le plus important.\n"
    "## Problèmes importants\n- `fichier:ligne` : le problème, sa conséquence concrète, la correction proposée.\n"
    "## À améliorer\n- même format, pour les points moins graves.\n"
    "## Points positifs\n- une à trois lignes.\n"
    "Uniquement des problèmes réels, vérifiés dans le code : bugs, failles de sécurité, erreurs de logique, cas "
    "non gérés, fuites de ressources, lenteurs. Pas de remarque de style sans conséquence. Si tout va bien, dis-le.")
CLAUDE_SYSTEM = ("Tu es un relecteur de code senior, exigeant et pragmatique. Tu relis sans rien modifier : tu "
                 "n'as que des outils de lecture (Read, Grep, Glob). Vérifie chaque problème dans le code avant "
                 "de le signaler.")
CLAUDE_TOOLS = "Read,Grep,Glob"
MAP_SYSTEM = (
    "Tu es un relecteur de code senior qui ne signale que ce qu'il peut prouver. On te montre un extrait d'un "
    "projet : des fichiers dont chaque ligne est précédée de son numéro, ou un diff git (+ ajouté, - supprimé).\n"
    "Signale uniquement les bugs certains, visibles sur une ligne précise : erreur de logique, plantage sur un cas "
    "courant (liste vide, None, division par zéro), faille de sécurité (injection, secret écrit en dur), ressource "
    "jamais libérée, valeur par défaut mutable partagée, condition inversée, borne décalée.\n"
    "Interdit : conseils génériques (ajouter des try, des types, des commentaires, découper), style, suppositions "
    "sur du code absent de l'extrait, affirmations sur la bibliothèque standard dont tu n'es pas certain.\n"
    "Format : « - chemin:ligne : problème → correction », cinq lignes au plus. La plupart des extraits n'ont "
    "aucun bug de ce genre : réponds alors seulement RAS.")
REDUCE_SYSTEM = ("Tu rédiges le rapport final d'une review de code à partir des notes de relecteurs. Fusionne les "
                 "doublons, écarte les remarques vagues ou génériques, garde au plus huit problèmes, en une ou deux "
                 "phrases chacun, et n'ajoute rien qui ne soit pas dans les notes.\n" + REPORT_FORMAT)
NOTHING_FOUND = "RÉSUMÉ : Je n'ai relevé aucun problème notable dans le code relu.\n\n## Problèmes importants\n- Aucun."
MAP_TOKENS, REDUCE_TOKENS = 600, 1500
_MAX_DIFF_CHARS = 60_000
_MAX_LISTED_FILES = 200

Chat = Callable[[str, str, int], str]                  # (consigne, contenu, tokens de réponse) → réponse locale
ClaudeTask = Callable[[str, str, list[str]], str]      # (demande, consigne, arguments de `claude`) → rapport


class Cancelled(Exception):
    """Review arrêtée à la demande."""


def claude_request(material: Material) -> tuple[str, list[str]]:
    root = material.root
    args = ["--tools", CLAUDE_TOOLS, "--allowedTools", CLAUDE_TOOLS, "--add-dir", str(root)]
    if material.scope == "file" and material.files:
        request = (f"Fais la review du fichier {material.files[0]} (projet {root}). Lis-le en entier, et le code "
                   "dont il dépend quand c'est utile pour juger.")
    elif material.scope == "changes":
        listed = "\n".join(f"- {path}" for path in material.files[:_MAX_LISTED_FILES]) or "aucun"
        cut = ("\n(diff tronqué : lis les fichiers concernés pour la suite)"
               if len(material.diff) > _MAX_DIFF_CHARS else "")
        diff = f"Diff par rapport au dernier commit :\n```diff\n{material.diff[:_MAX_DIFF_CHARS]}\n```{cut}\n" \
            if material.diff else ""
        request = (f"Fais la review des changements non commités du projet {root}.\n{diff}"
                   f"Nouveaux fichiers, à lire en entier :\n{listed}\n"
                   "Lis le code autour des changements quand c'est utile pour juger.")
    else:
        request = (f"Fais la review du projet {root}. Commence par sa structure (Glob), lis les fichiers principaux "
                   "(Read) et cherche les motifs à risque (Grep) : secrets en dur, entrées non validées, erreurs "
                   "ignorées, accès concurrents.")
    return f"{request}\n\n{REPORT_FORMAT}", args


def ollama_chat(cfg: Config) -> Chat:
    import httpx
    import ollama

    client = ollama.Client(host=cfg.llm.host, timeout=httpx.Timeout(600.0, connect=3.0))

    def chat(system: str, content: str, tokens: int) -> str:
        # Même contexte que la conversation : un autre num_ctx ferait recharger le modèle par Ollama.
        response = client.chat(model=cfg.llm.model, messages=[{"role": "system", "content": system},
                                                               {"role": "user", "content": content}],
                               think=False, keep_alive=cfg.llm.keep_alive,
                               options={"num_ctx": cfg.llm.num_ctx, "num_predict": tokens, "temperature": 0.1})
        return (response.message.content or "").strip()
    return chat


def budgets(num_ctx: int) -> tuple[int, int]:
    """Caractères d'une passe de relecture et des notes à fusionner, pour que consigne + contenu + réponse tiennent
    dans le contexte (~2,9 caractères par token de code)."""
    return max(3000, int((num_ctx - 250 - MAP_TOKENS - 150) * 2.9)), \
        max(2000, int((num_ctx - 450 - REDUCE_TOKENS - 150) * 2.9))


def local_review(material: Material, chat: Chat, max_chars: int, size: int, notes_size: int | None = None,
                 cancel: threading.Event | None = None, wait: Callable[[], None] = lambda: None,
                 progress: Callable[[int, int], None] = lambda done, total: None) -> tuple[str, str]:
    """→ (rapport, note sur ce qui n'a pas été relu)."""
    texts = diff_files(material.diff) if material.diff else []
    texts += [text for text in (numbered(path, material.root) for path in material.files) if text]
    kept: list[str] = []
    used = 0
    for text in texts:
        if kept and used + len(text) > max_chars:
            break
        kept.append(text)
        used += len(text)
    note = (f"Modèle local : relu {len(kept)} sur {len(texts)} fichiers (limite de {max_chars // 1000} ko de code)."
            if len(kept) < len(texts) else "")
    parts = chunks(kept, size)
    notes: list[str] = []
    for index, part in enumerate(parts):
        wait()
        if cancel is not None and cancel.is_set():
            raise Cancelled
        answer = chat(MAP_SYSTEM, part, MAP_TOKENS).strip()
        progress(index + 1, len(parts))
        lines = [line for line in answer.splitlines() if line.strip().startswith(("-", "*", "•"))]
        if lines and not re.fullmatch(r"\W*RAS\W*", answer, re.I):
            notes.append("\n".join(lines[:5]))
    if not notes:
        return NOTHING_FOUND, note
    wait()
    if cancel is not None and cancel.is_set():
        raise Cancelled
    joined = "\n".join(notes)[:notes_size or size * 2 // 3]
    return chat(REDUCE_SYSTEM, "Notes des relecteurs :\n" + joined, REDUCE_TOKENS), note


# « RÉSUMÉ : … », « **Résumé** : … » ou un titre « # RÉSUMÉ » suivi du texte à la ligne.
_SUMMARY = re.compile(r"^[#>*_\s]*R[ÉE]SUM[ÉE][*_ \t]*(?::|\n)[*_\s]*(?P<text>.+?)(?=\n\s*\n|\n\s*#|\Z)",
                      re.I | re.M | re.S)


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", re.sub(r"[`*_#>]", "", text)).strip()


def split_report(report: str) -> tuple[str, str]:
    """→ (résumé à dire, rapport sans le résumé)."""
    text = report.strip()
    if match := _SUMMARY.search(text):
        return plain(match["text"])[:600], (text[:match.start()] + text[match.end():]).strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", plain(text))[:2])[:600], text
