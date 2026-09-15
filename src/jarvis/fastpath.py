"""Réflexes : réponses instantanées sans LLM (heure, date, « stop »).

Les motifs sont volontairement stricts (phrase entière) : « quel jour tombe Noël » doit
partir au LLM, pas recevoir la date du jour.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
         "septembre", "octobre", "novembre", "décembre"]

_PREFIX = r"(?:(?:hey |ok |dis )?jarvis )?(?:dis moi |tu peux me dire |tu peux me donner )?"
_POLITE = r"(?: s il te plait| s il vous plait| stp)?(?: jarvis)?"
_TIME = re.compile(_PREFIX + r"(?:quelle heure (?:est il|il est)|il est quelle heure|l heure)" + _POLITE)
_DATE = re.compile(
    _PREFIX + r"(?:(?:on est|nous sommes|c est) quel jour|quel jour (?:on est|sommes nous|est on|est ce)"
    r"|quelle (?:est la )?date|on est le combien|c est quoi la date)(?: aujourd hui)?" + _POLITE)
_STOP = re.compile(r"(?:jarvis )?(?:stop|stoppe|arrete|arrete toi|tais toi|silence|chut|annule|laisse tomber"
                   r"|c est bon|rien|non merci|merci c est tout|c est tout)(?: jarvis)?")


# Bascule de moteur : Whisper déforme souvent ces phrases courtes (« Passe sur Claude » →
# « Pas sur Claude », « Passe en local » → « Passons local »). Plutôt qu'une phrase exacte,
# on reconnaît une phrase courte faite d'un verbe de bascule et d'une seule cible.
_SWITCH_VERBS = {"passe", "passes", "passez", "passons", "passer", "pas", "pass", "bascule", "basculer",
                 "repasse", "reviens", "retourne", "mode", "utilise", "utiliser", "active", "mets", "met"}
_SWITCH_FILLER = {"jarvis", "hey", "ok", "dis", "s", "il", "te", "vous", "plait", "stp", "sur", "en", "a",
                  "au", "avec", "le", "la", "moteur", "modele", "maintenant", "on"}
_CLAUDE_WORDS = {"claude", "claud", "clode", "clod"}
_LOCAL_WORDS = {"local", "locale", "horsligne", "ollama"}
_WHICH_ENGINE = re.compile(
    _PREFIX + r"(?:quel (?:modele|cerveau|mode|moteur) (?:utilises tu|tu utilises|est actif)"
    r"|tu utilises quel (?:modele|mode|moteur)|tu es en quel mode|tu tournes sur quoi)" + _POLITE)


def switch_target(text: str) -> str | None:
    """« passe sur Claude » → "claude", « passe en local » → "local", sinon None."""
    words = normalize(text).replace("hors ligne", "horsligne").split()
    content = [w for w in words if w not in _SWITCH_FILLER]
    if not content or len(content) > 3 or not any(w in _SWITCH_VERBS for w in content):
        return None
    targets = {"claude" if w in _CLAUDE_WORDS else "local" for w in content if w in _CLAUDE_WORDS | _LOCAL_WORDS}
    unknown = [w for w in content if w not in _SWITCH_VERBS | _CLAUDE_WORDS | _LOCAL_WORDS]
    return targets.pop() if len(targets) == 1 and not unknown else None


def asks_engine(text: str) -> bool:
    return bool(_WHICH_ENGINE.fullmatch(normalize(text)))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_stop(text: str) -> bool:
    return bool(_STOP.fullmatch(normalize(text)))


def say_time(now: datetime) -> str:
    hour, minute = now.hour, now.minute
    if hour == 0:
        spoken = "minuit"
    elif hour == 12:
        spoken = "midi"
    else:
        spoken = f"{hour} heure" + ("s" if hour > 1 else "")
    return f"Il est {spoken}" + (f" {minute}" if minute else "") + "."


def say_date(now: datetime) -> str:
    day = "1er" if now.day == 1 else str(now.day)
    return f"Nous sommes le {_JOURS[now.weekday()]} {day} {_MOIS[now.month - 1]}."


def reply(text: str, now: datetime | None = None) -> str | None:
    normalized = normalize(text)
    now = now or datetime.now()
    if _TIME.fullmatch(normalized):
        return say_time(now)
    if _DATE.fullmatch(normalized):
        return say_date(now)
    return None
