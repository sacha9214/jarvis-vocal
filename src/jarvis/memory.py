"""Mémoire de Jarvis : ce qu'il retient de toi d'une session à l'autre.

« Retiens que je travaille sur le projet jarvis-vocal », « retiens que le bureau, c'est mon dossier
Travail » : des faits courts, rangés dans un fichier JSON du dossier de données, relus à chaque
démarrage et donnés au modèle dans son prompt. « Oublie que… » les retire, « qu'est-ce que tu sais
sur moi ? » les récite.

Le prompt est pré-rempli à chaque question : la mémoire est donc bornée en nombre de faits et en
longueur, et elle ne change le prompt (donc le cache du modèle) que lorsque tu ajoutes ou retires un fait.
Rien de secret n'y entre : un fait qui ressemble à un mot de passe ou à un numéro de carte est refusé.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import data_dir

LOG = logging.getLogger("jarvis.memory")
MAX_FACTS = 40
MAX_FACT_CHARS = 200
# Ce qui ne doit jamais être retenu, ni partir vers un modèle.
_SECRET = re.compile(
    r"(?:mot de passe|password|mdp|code pin|\bpin\b|code secret|cl[eé] (?:api|priv[eé]e|secr[eè]te)|token"
    r"|jeton|iban|\bcvv\b|cryptogramme)"
    r"|\b(?:\d[ -]?){13,19}\b"                      # un numéro de carte bancaire
    r"|\b[A-Za-z0-9_\-]{32,}\b")                    # une longue clé ou un jeton


@dataclass
class Fact:
    text: str
    added: float


def soft(text: str) -> str:
    # œ et æ sont des ligatures, pas des lettres accentuées : NFD ne les décompose pas, « sœur » devenait « s ur ».
    text = text.lower().replace("œ", "oe").replace("æ", "ae")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# « je » → prénom : un petit modèle qui lit « je travaille sur jarvis » croit parler de lui-même (mesuré).
_THIRD = {"je": "{who}", "j": "{who}", "me": "se", "m": "s", "moi": "{who}", "mon": "son", "ma": "sa",
          "mes": "ses", "suis": "est", "ai": "a", "vais": "va", "peux": "peut", "veux": "veut", "dois": "doit",
          "sais": "sait", "fais": "fait", "prends": "prend", "viens": "vient", "bois": "boit", "dis": "dit"}
_SECOND = {"je": "tu", "j": "tu", "me": "te", "m": "t", "moi": "toi", "mon": "ton", "ma": "ta", "mes": "tes",
           "suis": "es", "ai": "as", "vais": "vas"}
_STOP = {"que", "qui", "les", "des", "une", "est", "pour", "dans", "avec", "sur", "pas", "plus", "mon", "ma",
         "mes", "son", "sa", "ses", "ton", "ta", "tes", "quel", "quelle", "quels", "quoi", "comment", "est-ce",
         "tu", "te", "toi", "moi", "je", "suis", "fait", "faire", "sais", "souviens", "rappelle", "aime", "aimes",
         "appelle", "appeler", "nom", "nomme", "avoir", "etre", "quand", "heure", "heures", "moment", "fois"}


def _swap(text: str, table: dict[str, str], who: str) -> str:
    def replace(match: re.Match[str]) -> str:
        word, apostrophe = match.group(1), match.group(2) or ""
        target = table.get(word.lower())
        if target is None:
            return match.group(0)
        target = target.format(who=who)
        if apostrophe and target.lower() in (who.lower(), "tu"):
            return target + " "          # « j'ai » → « Sacha a », pas « Sacha'a »
        if match.start() == 0:
            target = target[0].upper() + target[1:]
        return target + apostrophe
    swapped = re.sub(r"\b([A-Za-zÀ-ÿ]+)(['’])?", replace, text)
    return re.sub(r"\s+", " ", swapped).strip()


def third_person(fact: str, who: str) -> str:
    """« Je travaille sur jarvis-vocal. » → « Sacha travaille sur jarvis-vocal. »
    « Ma sœur s'appelle Léa. » → « À propos de Sacha : sa sœur s'appelle Léa. » (sinon « sa » de qui ?)"""
    swapped = _swap(fact, _THIRD, who)
    if who.lower() in swapped.lower():
        return swapped
    return f"À propos de {who} : {swapped[0].lower()}{swapped[1:]}"


def looks_secret(text: str) -> bool:
    return bool(_SECRET.search(text))


def clean(text: str) -> str:
    """« que je travaille sur jarvis » → « Je travaille sur jarvis. »"""
    text = re.sub(r"\s+", " ", text).strip().strip(".,;:!? ")
    text = re.sub(r"^(?:que|qu')\s*", "", text, flags=re.IGNORECASE)
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


class Memory:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "memory.json"
        self._lock = threading.Lock()
        self._facts: list[Fact] = self._load()
        self.on_change = lambda: None       # le pipeline recalcule son prompt

    def _load(self) -> list[Fact]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            # Fichier abîmé : on le met de côté plutôt que de l'écraser, pour ne rien perdre.
            backup = self.path.with_suffix(f".abime-{int(time.time())}.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            LOG.warning("Mémoire illisible (%s) : mise de côté dans %s, je repars de zéro.", exc, backup.name)
            return []
        facts = []
        for item in raw.get("facts", []) if isinstance(raw, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                facts.append(Fact(item["text"].strip()[:MAX_FACT_CHARS], float(item.get("added") or 0)))
        return facts[-MAX_FACTS:]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"facts": [asdict(f) for f in self._facts]}, ensure_ascii=False,
                                        indent=2), encoding="utf-8")
        temporary.replace(self.path)            # jamais un fichier à moitié écrit

    def facts(self) -> list[str]:
        with self._lock:
            return [fact.text for fact in self._facts]

    def remember(self, text: str) -> str:
        fact = clean(text)
        if not fact:
            return "Dis-moi ce que je dois retenir."
        if looks_secret(fact):
            return "Je ne retiens jamais de mot de passe, de code ou de numéro de carte : garde-les pour toi."
        if len(fact) > MAX_FACT_CHARS:
            return "C'est trop long pour que je le retienne : dis-le en une phrase courte."
        with self._lock:
            wanted = soft(fact)
            if any(soft(existing.text) == wanted for existing in self._facts):
                return "Je le savais déjà."
            dropped = None
            if len(self._facts) >= MAX_FACTS:
                dropped = self._facts.pop(0)        # le plus ancien laisse la place
            self._facts.append(Fact(fact, time.time()))
            self._save()
        self.on_change()
        if dropped:
            return f"C'est noté. J'ai oublié le plus ancien pour faire de la place : {dropped.text}"
        return "C'est noté, je m'en souviendrai."

    def forget(self, text: str) -> str:
        wanted = soft(clean(text) or text)
        if not wanted:
            return "Dis-moi ce que je dois oublier."
        words = [word for word in wanted.split() if len(word) > 2]
        with self._lock:
            best, score = None, 0.0
            for fact in self._facts:
                name = soft(fact.text)
                value = 100.0 if wanted in name else 60.0 * sum(w in name for w in words) / max(1, len(words))
                if value > score:
                    best, score = fact, value
            if best is None or score < 50:
                return "Je n'avais rien retenu de tel."
            self._facts.remove(best)
            self._save()
        self.on_change()
        return f"C'est oublié : {best.text}"

    def forget_all(self) -> str:
        with self._lock:
            count = len(self._facts)
            self._facts = []
            self._save()
        self.on_change()
        return f"J'ai tout oublié, {count} souvenir" + ("s" if count > 1 else "") + "." if count else \
            "Je n'avais rien retenu."

    def recite(self) -> str:
        facts = self.facts()
        if not facts:
            return "Je n'ai encore rien retenu sur toi. Dis « retiens que… » pour m'apprendre quelque chose."
        return "Voici ce que tu m'as demandé de retenir. " + " ".join(facts)

    def relevant(self, question: str, who: str = "l'utilisateur", limit: int = 5) -> str:
        """Les souvenirs qui partagent un mot avec la question, à la troisième personne ; sinon rien.

        Mesuré : avec toute la mémoire dans le prompt, un modèle de 4 milliards de paramètres la récite
        à chaque réponse (« La capitale de l'Italie est Rome. Je préfère le café sans sucre »)."""
        words = {w for w in soft(question).split() if len(w) > 2 and w not in _STOP}
        if not words:
            return ""
        scored = []
        for fact in self.facts():
            fact_words = {w for w in soft(fact).split() if len(w) > 2 and w not in _STOP}
            common = sum(1 for w in words if any(w[:5] == f[:5] for f in fact_words))
            if common:
                scored.append((common, fact))
        if not scored:
            return ""
        chosen = [fact for _, fact in sorted(scored, key=lambda item: -item[0])[:limit]]
        lines = "\n".join(f"- {third_person(fact, who)}" for fact in chosen)
        return f"Souvenirs utiles pour cette question (à utiliser seulement s'ils y répondent) :\n{lines}"

    def for_prompt(self, who: str = "l'utilisateur") -> str:
        """Bloc ajouté au prompt système, ou chaîne vide."""
        facts = self.facts()
        if not facts:
            return ""
        # Ses propres mots, entre guillemets : « je » y désigne l'utilisateur, jamais Jarvis.
        return (f"{who[0].upper() + who[1:]} t'a demandé de retenir ceci (ses propres mots, où « je » désigne "
                f"{who}) ; sers-t'en quand c'est utile, sans le réciter :\n"
                + "\n".join(f"- « {fact} »" for fact in facts) + "\n")
