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
