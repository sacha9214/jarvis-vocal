"""Commandes vocales directes : les demandes courantes sur le PC deviennent un appel d'outil
sans passer par le LLM. Plus rapide (aucun token à générer) et plus fiable qu'un petit modèle.
Tout ce qui n'est pas reconnu part au LLM, qui dispose des mêmes outils.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .system import apps, folders, web


@dataclass(frozen=True)
class Command:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


_NUMBERS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60, "cent": 100,
}
_UNITS = {"seconde": 1, "secondes": 1, "sec": 1, "minute": 60, "minutes": 60, "min": 60,
          "heure": 3600, "heures": 3600, "h": 3600}
_SPECIAL_DURATIONS = {"une demi heure": 1800, "un quart d heure": 900, "trois quarts d heure": 2700,
                      "une minute et demie": 90, "une heure et demie": 5400}


def parse_number(text: str) -> int | None:
    text = text.strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    total = last = 0
    seen = False
    for word in text.split():
        if word == "et":
            continue
        if word not in _NUMBERS:
            return None
        value, seen = _NUMBERS[word], True
        if value == 100:
            total, last = max(total, 1) * 100, 0
        elif word == "vingt" and last == 4:       # quatre-vingt
            total, last = total + 76, 80
        else:
            total, last = total + value, value
    return total if seen else None


def parse_duration(text: str) -> int | None:
    text = text.strip()
    if text in _SPECIAL_DURATIONS:
        return _SPECIAL_DURATIONS[text]
    total, covered = 0, ""
    for match in re.finditer(r"(?P<n>[a-z0-9 ]+?) (?P<u>secondes?|minutes?|heures?|min|sec|h)(?P<half> et demie?)?"
                             r"(?= |$)", text):
        number = parse_number(match["n"].strip().removeprefix("et "))
        if number is None:
            return None
        unit = _UNITS[match["u"]]
        total += number * unit + (unit // 2 if match["half"] else 0)
        covered += match.group(0)
    return total if total > 0 and covered.replace(" ", "") == text.replace(" ", "") else None


def _aligned(text: str) -> tuple[str, str]:
    """Deux versions de même longueur : sans accents (pour les motifs) et avec (pour les
    étiquettes et recherches qu'on redira ou tapera)."""
    plain: list[str] = []
    soft: list[str] = []
    for char in unicodedata.normalize("NFC", text.lower()):
        base = "".join(c for c in unicodedata.normalize("NFD", char) if unicodedata.category(c) != "Mn")
        if len(base) == 1 and base.isascii() and base.isalnum():
            p, s = base, char
        elif char.isalpha():
            p, s = "o", char                    # œ, æ… : rare, on garde l'alignement
        else:
            p, s = " ", " "
        if p == " " and (not plain or plain[-1] == " "):
            continue
        plain.append(p)
        soft.append(s)
    return "".join(plain).rstrip(), "".join(soft).rstrip()


_LEAD = re.compile(r"^(?:(?:hey |ok |dis )?jarvis )?(?:(?:est ce que )?(?:tu peux|tu pourrais|peux tu|pourrais tu"
                   r"|je veux que tu|j aimerais que tu|je voudrais que tu|s il te plait|stp) )*")
_TAIL = re.compile(r"(?: (?:s il te plait|s il vous plait|stp|merci|jarvis|maintenant|tout de suite|vite))+$")
_OPEN_VERBS = (r"(?:ouvre|ouvrir|lance|lancer|demarre|demarrer|allume|allumer|mets|mettre|va sur|aller sur"
               r"|affiche|afficher)")
_COMPUTER = r"(?:l ordinateur|le pc|le mac|l ordi|la machine)"
_VOLUME = r"(?:le son|le volume|son|volume)"


def _span(soft: str, match: re.Match[str], group: str) -> str:
    return soft[match.start(group):match.end(group)].strip()


def _power(plain: str, soft: str) -> Command | None:
    if re.fullmatch(r"annule (?:l extinction|le redemarrage|l arret|la mise en veille)", plain):
        return Command("cancel_power")
    if re.fullmatch(r"(?:annule|arrete|stoppe|supprime) (?:le|les|mon|mes) minuteurs?", plain):
        return Command("cancel_timer")
    if re.fullmatch(rf"(?:eteins|eteint|eteindre|arrete) {_COMPUTER}", plain):
        return Command("power", {"action": "shutdown"})
    if re.fullmatch(rf"(?:redemarre|redemarrer) {_COMPUTER}", plain):
        return Command("power", {"action": "restart"})
    if re.fullmatch(rf"(?:mets|mettre|passe) (?:{_COMPUTER} )?en veille", plain):
        return Command("power", {"action": "sleep"})
    if re.fullmatch(rf"verrouille (?:{_COMPUTER}|l ecran|la session)", plain):
        return Command("lock_screen")
    return None


def _volume(plain: str, soft: str) -> Command | None:
    if re.fullmatch(rf"(?:coupe|couper|desactive) {_VOLUME}|mute|mode muet", plain):
        return Command("set_volume", {"mute": True})
    if re.fullmatch(rf"(?:remets|reactive|retablis|rallume) {_VOLUME}", plain):
        return Command("set_volume", {"mute": False})
    if match := re.fullmatch(rf"(?:mets|mettre|regle|passe|monte|baisse)? ?{_VOLUME} (?:a|au) (?P<n>.+?)"
                             r"(?: pour cent| pourcent)?", plain):
        if match["n"] in ("fond", "maximum", "max"):
            return Command("set_volume", {"level": 100})
        if (level := parse_number(match["n"])) is not None:
            return Command("set_volume", {"level": level})
    if match := re.fullmatch(rf"(?P<verb>monte|augmente|baisse|diminue) {_VOLUME} de (?P<n>.+?)(?: pour cent)?", plain):
        if (step := parse_number(match["n"])) is not None:
            return Command("set_volume", {"change": step if match["verb"] in ("monte", "augmente") else -step})
    if match := re.fullmatch(rf"(?P<verb>monte|augmente|hausse|baisse|diminue) (?:un peu )?{_VOLUME}"
                             r"(?P<max> a fond)?", plain):
        if match["max"]:
            return Command("set_volume", {"level": 100})
        return Command("set_volume", {"change": 10 if match["verb"] in ("monte", "augmente", "hausse") else -10})
    return None


def _media(plain: str, soft: str) -> Command | None:
    track = r"(?:la chanson|la musique|le morceau|le titre|la piste|chanson|musique|morceau|titre|piste)"
    if re.fullmatch(rf"(?:mets |fais )?(?:pause|en pause)|(?:mets|met) (?:{track} )?en pause|pause {track}"
                    rf"|stoppe {track}|arrete {track}", plain):
        return Command("media", {"action": "pause"})
    if re.fullmatch(rf"(?:reprends|relance|remets) {track}|play|lecture|reprends la lecture", plain):
        return Command("media", {"action": "play"})
    if re.fullmatch(rf"{track} suivante?|{track} d apres|(?:passe a|mets) {track} suivante?|suivant|suivante"
                    rf"|passe {track}", plain):
        return Command("media", {"action": "next"})
    if re.fullmatch(rf"{track} precedente?|(?:reviens a|mets) {track} precedente?|precedent|precedente", plain):
        return Command("media", {"action": "previous"})
    return None


def _timer(plain: str, soft: str) -> Command | None:
    match = (re.fullmatch(r"(?:(?:mets|mettre|lance|lancer|demarre|programme|regle) )?(?:moi )?(?:un |le )?minuteur"
                          r" (?:de |d |pour |sur )?(?P<d>.+?)(?: pour (?P<label>.+))?", plain)
             or re.fullmatch(r"(?:reveille moi|previens moi|rappelle moi) dans (?P<d>.+?)(?: pour (?P<label>.+))?",
                             plain))
    if match and (seconds := parse_duration(match["d"])):
        arguments: dict[str, Any] = {"seconds": seconds}
        if match["label"]:
            arguments["label"] = _span(soft, match, "label")
        return Command("set_timer", arguments)
    return None


def _search(plain: str, soft: str) -> Command | None:
    if match := re.fullmatch(r"(?:cherche|recherche|google)(?: moi)? (?:sur (?:internet|google|le web) )?(?P<q>.+)",
                             plain):
        return Command("web_search", {"query": _span(soft, match, "q")})
    return None


def _close(plain: str, soft: str) -> Command | None:
    if match := re.fullmatch(r"(?:ferme|fermer|quitte|quitter) (?:l application |l appli )?(?P<t>.+)", plain):
        return Command("close_app", {"name": _span(soft, match, "t")})
    return None


def _folder(plain: str, soft: str) -> Command | None:
    names = r"bureau|documents|telechargements?|images|videos|dossier personnel"
    match = (re.fullmatch(rf"(?:ouvre|affiche|montre moi|va dans) (?:mes |les |le |mon )?(?P<f>{names})", plain)
             or re.fullmatch(rf"(?:ouvre|affiche|montre moi|va dans) (?:le |mon )?dossier (?:des |de |du |)?"
                             rf"(?P<f>{names}|musique)", plain))
    if match:
        return Command("open_folder", {"name": match["f"]})
    return None


def _open(plain: str, soft: str) -> Command | None:
    match = re.fullmatch(rf"{_OPEN_VERBS}(?: moi)? (?P<site>le site (?:web )?(?:de |d )?)?(?P<t>.+)", plain)
    if not match:
        return None
    target = _span(soft, match, "t")
    if not match["site"] and (app := apps.find_app(match["t"])):
        return Command("open_app", {"name": app.name})
    if web.resolve_site(match["t"]):
        return Command("open_website", {"site": target})
    return None


_SCREEN_ASK = re.compile(r"(?:regarde|qu est ce|c est quoi|explique|aide|lis|traduis|resume|tu vois|decris|dis moi"
                         r"|tu peux voir|corrige)")
_SCREEN_TOPIC = re.compile(r"\b(?:ecran|cette erreur|ce message|cette page|cette fenetre|ce code|ce texte"
                           r"|ce document|ce que je fais)\b")


def _screen(plain: str, soft: str) -> Command | None:
    if re.fullmatch(r"qu est ce que tu vois|tu vois quoi|regarde (?:mon |l )?ecran|decris (?:mon |l )?ecran", plain):
        return Command("describe_screen")
    if _SCREEN_ASK.match(plain) and _SCREEN_TOPIC.search(plain):
        return Command("describe_screen", {"question": soft})
    return None


_RULES = (_power, _volume, _media, _timer, _search, _screen, _close, _folder, _open)


def parse(text: str) -> Command | None:
    plain, soft = _aligned(text)
    if lead := _LEAD.match(plain):
        plain, soft = plain[lead.end():], soft[lead.end():]
    if tail := _TAIL.search(plain):
        plain, soft = plain[:tail.start()], soft[:tail.start()]
    plain, soft = plain.strip(), soft.strip()
    if not plain:
        return None
    for rule in _RULES:
        if command := rule(plain, soft):
            return command
    return None


__all__ = ["Command", "folders", "parse", "parse_duration", "parse_number"]
