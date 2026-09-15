"""Commandes vocales directes : les demandes courantes sur le PC deviennent un appel d'outil
sans passer par le LLM. Plus rapide (aucun token à générer) et plus fiable qu'un petit modèle.
Tout ce qui n'est pas reconnu part au LLM, qui dispose des mêmes outils.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .desktop import parse_keys
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


_REVIEW = r"(?:review|revue|reviou|rivieu|relecture|audit)"
_CODE = r"(?:code|projet|fichier|changements?|modifs?|modifications?|diff)"


def _review(plain: str, soft: str) -> Command | None:
    if re.search(rf"\b(?:annule|arrete|stoppe) (?:la |ta )?{_REVIEW}\b", plain):
        return Command("review_control", {"action": "cancel"})
    if re.search(rf"\b(?:ou en est|elle avance|c est fini|tu as fini|t as fini)\b.*\b{_REVIEW}\b", plain):
        return Command("review_control", {"action": "status"})
    asked = (re.search(rf"\b{_REVIEW}\b", plain) and "presse" not in plain
             and (re.search(rf"\b{_CODE}\b", plain) or re.match(r"(?:fais|fait|faire|lance|demarre) ", plain))
             ) or re.match(rf"(?:relis|relire|audite|analyse|verifie|check)(?: moi)? (?:tout )?"
                           rf"(?:mon|le|ce|mes|les|tout le) {_CODE}\b", plain)
    if not asked:
        return None
    if re.search(r"\b(?:changements?|modifs?|modifications?|diff|ce que j ai (?:change|modifie|fait|ajoute)"
                 r"|(?:pas|non) commite)\b", plain):
        arguments: dict[str, Any] = {"scope": "changes"}
    elif re.search(r"\b(?:ce fichier|le fichier|du fichier|fichier ouvert|fichier actuel|ce code)\b", plain):
        arguments = {"scope": "file"}
    else:
        arguments = {"scope": "project"}
    if re.search(r"\b(?:avec|par|sur|via) claude\b", plain):
        arguments["engine"] = "claude"
    elif re.search(r"\b(?:en local|localement|modele local|avec le local)\b", plain):
        arguments["engine"] = "local"
    engine_tail = r"(?: (?:avec|par|sur|via) claude| en local| localement)?$"
    match = (re.search(rf"\bprojet (?P<t>.+?){engine_tail}", plain)
             or re.search(rf"\b{_REVIEW} (?:de |du |d )(?P<t>(?!(?:mon|ma|mes|ce|cet|cette|ces|le|la|les|l|tout|toute)"
                          rf"\b).+?){engine_tail}", plain))
    if match and match["t"] not in ("entier", "complet", "ouvert", "actuel", "en cours") \
            and not re.match(rf"{_CODE}\b", match["t"]):
        arguments["target"] = _span(soft, match, "t")
    return Command("review_code", arguments)


def _app(plain: str, soft: str) -> Command | None:
    """Hors du navigateur : l'application au premier plan (Word, Discord, Réglages…)."""
    if match := re.fullmatch(r"(?:clique|appuie) sur (?:le bouton |le menu |l onglet |le lien |la touche )?(?P<t>.+)",
                             plain):
        if keys := parse_keys(match["t"]):
            return Command("app_shortcut", {"keys": "+".join(keys)})
        return Command("app_press", {"text": _span(soft, match, "t")})
    if match := re.fullmatch(r"(?:fais|envoie|utilise)(?: le raccourci)? (?P<k>.+)", plain):
        if keys := parse_keys(match["k"]):
            return Command("app_shortcut", {"keys": "+".join(keys)})
    if re.fullmatch(r"(?:enregistre|sauvegarde|sauve)(?: le fichier| le document| mon travail| tout)?", plain):
        return Command("app_shortcut", {"keys": "ctrl+s"})
    return None


_ORDINALS = {
    "premier": 1, "premiere": 1, "1er": 1, "1re": 1, "1ere": 1, "deuxieme": 2, "second": 2, "seconde": 2, "2e": 2,
    "2eme": 2, "troisieme": 3, "3e": 3, "3eme": 3, "quatrieme": 4, "4e": 4, "4eme": 4, "cinquieme": 5, "5e": 5,
    "5eme": 5, "sixieme": 6, "septieme": 7, "huitieme": 8, "neuvieme": 9, "dixieme": 10,
}
_VIDEO = r"(?:la video|cette video|le film|youtube)"


def _media_command(action: str, value: float | None = None) -> Command:
    arguments: dict[str, Any] = {"action": action}
    if value is not None:
        arguments["value"] = value
    return Command("browser_media", arguments)


def _browser_media(plain: str) -> Command | None:
    volume = r"(?:le son|le volume)"
    if match := re.fullmatch(rf"(?P<verb>baisse|diminue|monte|augmente) (?:un peu )?{volume} (?:de |d )?{_VIDEO}"
                             r"(?: de (?P<n>.+?)(?: pour cent)?)?", plain):
        step = parse_number(match["n"]) if match["n"] else 10
        if step is not None:
            return _media_command("volume_up" if match["verb"] in ("monte", "augmente") else "volume_down", step)
    if match := re.fullmatch(rf"(?:mets|met|regle) {volume} (?:de |d )?{_VIDEO} a (?P<n>.+?)(?: pour cent)?", plain):
        if (level := parse_number(match["n"])) is not None:
            return _media_command("volume", level)
    if re.fullmatch(rf"coupe (?:le son (?:de |d )?)?{_VIDEO}|mets {_VIDEO} en sourdine", plain):
        return _media_command("mute")
    if re.fullmatch(rf"remets le son (?:de |d )?{_VIDEO}", plain):
        return _media_command("unmute")
    if re.fullmatch(rf"(?:mets|met) {_VIDEO} en pause|pause {_VIDEO}|(?:stoppe|arrete) {_VIDEO}", plain):
        return _media_command("pause")
    if re.fullmatch(rf"(?:relance|reprends|remets|lance|joue) {_VIDEO}", plain):
        return _media_command("play")
    if match := re.fullmatch(r"(?P<dir>avance|recule)(?: la video)? (?:de )?(?P<n>.+?) (?P<unit>secondes?|minutes?)",
                             plain):
        if amount := parse_number(match["n"]):
            seconds = amount * (60 if match["unit"].startswith("minute") else 1)
            return _media_command("forward" if match["dir"] == "avance" else "back", seconds)
    if re.fullmatch(r"video suivante|passe a la video suivante|mets la video suivante|video d apres", plain):
        return _media_command("next")
    if match := re.fullmatch(r"(?:mets|passe) (?:la video )?(?:en )?vitesse (?:x )?(?P<n>[0-9]+(?: [0-9]+)?|normale)",
                             plain):
        return _media_command("speed", 1.0 if match["n"] == "normale" else float(match["n"].replace(" ", ".")))
    return None


def _browser(plain: str, soft: str) -> Command | None:
    if command := _browser_media(plain):
        return command
    if match := re.fullmatch(r"(?:lance|mets|ouvre|joue|clique sur|choisis|regarde) (?:la |le )?(?P<o>\w+) "
                             r"(?:video|resultat)", plain):
        if rank := _ORDINALS.get(match["o"]):
            return Command("browser_open", {"video": rank})
    if match := re.fullmatch(r"(?:lance|mets|ouvre|joue) la video (?:numero )?(?P<n>\w+)", plain):
        if rank := _ORDINALS.get(match["n"]) or parse_number(match["n"]):
            return Command("browser_open", {"video": rank})
    if match := re.fullmatch(r"clique sur (?P<t>.+)", plain):
        return Command("browser_open", {"text": _span(soft, match, "t")})
    if match := (re.fullmatch(r"(?:cherche|recherche|trouve)(?: moi)? (?P<q>.+?) sur youtube", plain)
                 or re.fullmatch(r"(?:mets|lance|trouve)(?: moi)? (?:une |des )?videos? (?:de |d |sur |avec )(?P<q>.+)",
                                 plain)):
        return Command("browser_search", {"query": _span(soft, match, "q"), "site": "youtube"})
    scrolls = {
        "down": r"(?:descends|fais defiler|scrolle|defile)(?: la page)?(?: vers le bas)?",
        "up": r"(?:remonte|monte) la page|remonte|fais defiler vers le haut",
        "top": r"(?:va |remonte )?tout en haut(?: de la page)?",
        "bottom": r"(?:va |descends )?tout en bas(?: de la page)?",
    }
    for direction, pattern in scrolls.items():
        if re.fullmatch(pattern, plain):
            return Command("browser_scroll", {"direction": direction})
    if re.fullmatch(r"page precedente|reviens en arriere|retour en arriere|retourne a la page precedente", plain):
        return Command("browser_navigate", {"direction": "back"})
    if re.fullmatch(r"(?:recharge|actualise|rafraichis) la page", plain):
        return Command("browser_navigate", {"direction": "reload"})
    if re.fullmatch(r"ferme (?:l onglet|cet onglet)", plain):
        return Command("browser_close_tab")
    if re.fullmatch(r"onglet suivant|change d onglet|passe a l onglet suivant", plain):
        return Command("browser_tabs", {"action": "switch"})
    if match := re.fullmatch(r"(?:va sur|passe sur|montre) l onglet (?P<n>\w+)", plain):
        if rank := _ORDINALS.get(match["n"]) or parse_number(match["n"]):
            return Command("browser_tabs", {"action": "switch", "index": rank})
    if re.fullmatch(r"(?:ouvre un )?nouvel onglet", plain):
        return Command("browser_tabs", {"action": "new"})
    return None


_RULES = (_power, _browser, _volume, _media, _timer, _search, _review, _screen, _close, _folder, _open)
_APP_RULES = (_power, _app, _volume, _media, _timer, _search, _review, _screen, _close, _folder, _open)


def parse(text: str, context: str = "") -> Command | None:
    """`context` : « browser », « code », « app » ou « » (inconnu : les règles du navigateur s'appliquent)."""
    plain, soft = _aligned(text)
    if lead := _LEAD.match(plain):
        plain, soft = plain[lead.end():], soft[lead.end():]
    if tail := _TAIL.search(plain):
        plain, soft = plain[:tail.start()], soft[:tail.start()]
    plain, soft = plain.strip(), soft.strip()
    if not plain:
        return None
    for rule in _APP_RULES if context in ("app", "code") else _RULES:
        if command := rule(plain, soft):
            return command
    return None


__all__ = ["Command", "folders", "parse", "parse_duration", "parse_number"]
