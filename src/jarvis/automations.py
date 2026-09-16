"""Automatisations : ce que Jarvis fait tout seul, à une heure ou quand quelque chose se passe.

À la voix :
    « tous les jours à 8 heures, rappelle-moi de prendre mes médicaments »
    « chaque lundi à 9 heures, dis-moi de faire le point de la semaine »
    « en semaine à 18 heures 30, ferme Discord »
    « demain à 7 heures, rappelle-moi d'appeler le garage »
    « quand j'ouvre Spotify, mets le volume à 40 »
    « quelles sont mes automatisations ? », « supprime le rappel des médicaments »

Dans config.yaml (section `automations`), les mêmes, plus les événements de la machine :
    - name: batterie
      when: {event: battery_low}
      say: "La batterie est presque vide, branche le chargeur."
    - name: soir
      when: {time: "22:30", days: [lundi, mardi, mercredi, jeudi, vendredi]}
      do: "mets l'ordinateur en veille"

Une action est soit une phrase à dire (`say`), soit une commande dite comme à Jarvis (`do`), reconnue au
moment de la création : ce qui ne serait pas compris ne peut pas être programmé. Les actions sensibles
gardent leur confirmation : programmer « éteins l'ordinateur » ne l'éteindra pas sans que tu dises oui.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .paths import data_dir

LOG = logging.getLogger("jarvis.automations")
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
EVENTS = ("startup", "app_opened", "power_plugged", "power_unplugged", "battery_low")
CHECK_EVERY_S = 15.0
LATE_TOLERANCE_S = 10 * 60      # ordinateur en veille à l'heure dite : on rattrape jusqu'à 10 minutes, pas plus
BATTERY_LOW = 15
MAX_AUTOMATIONS = 50


@dataclass
class Automation:
    name: str
    kind: str                           # "time" | "event"
    time: str = ""                      # "HH:MM"
    days: list[int] = field(default_factory=list)   # 0 = lundi ; vide = tous les jours
    once: str = ""                      # "AAAA-MM-JJ" : une seule fois, ce jour-là
    event: str = ""
    app: str = ""                       # pour app_opened
    say: str = ""
    do: str = ""
    last_run: float = 0.0
    source: str = "voice"               # "voice" (modifiable à la voix) | "config"

    def describe(self) -> str:
        what = f"dire « {self.say} »" if self.say else f"« {self.do} »"
        if self.kind == "event":
            when = {"startup": "au démarrage de Jarvis", "power_plugged": "quand tu branches le chargeur",
                    "power_unplugged": "quand tu débranches le chargeur",
                    "battery_low": "quand la batterie est faible"}.get(self.event, "")
            if self.event == "app_opened":
                when = f"quand tu ouvres {self.app}"
            return f"{when}, {what}"
        hour = spoken_time(self.time)
        if self.once:
            day = datetime.strptime(self.once, "%Y-%m-%d")
            return f"le {DAYS[day.weekday()]} {day.day} à {hour}, {what}"
        return f"{spoken_days(self.days)} à {hour}, {what}"


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9:]+", " ", text).strip()


def spoken_time(hhmm: str) -> str:
    hour, minute = (int(part) for part in hhmm.split(":"))
    if (hour, minute) == (12, 0):
        return "midi"
    if (hour, minute) == (0, 0):
        return "minuit"
    return f"{hour} heure{'s' if hour > 1 else ''}" + (f" {minute:02d}" if minute else "")


def spoken_days(days: list[int]) -> str:
    chosen = sorted(set(days))
    if not chosen or chosen == list(range(7)):
        return "tous les jours"
    if chosen == [0, 1, 2, 3, 4]:
        return "en semaine"
    if chosen == [5, 6]:
        return "le week-end"
    if len(chosen) == 1:
        return f"chaque {DAYS[chosen[0]]}"
    return "les " + ", ".join(DAYS[d] for d in chosen[:-1]) + f" et {DAYS[chosen[-1]]}"


# -- comprendre une heure et des jours dits à voix haute

_NUMBER_WORDS = {"une": 1, "un": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
                 "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
                 "quinze": 15, "seize": 16, "dix sept": 17, "dix huit": 18, "dix neuf": 19, "vingt": 20,
                 "vingt et une": 21, "vingt deux": 22, "vingt trois": 23, "trente": 30, "quarante cinq": 45}


def _number(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    return _NUMBER_WORDS.get(text)


def parse_time(text: str) -> str | None:
    """« 8 heures », « 8h30 », « 18 heures 30 », « midi », « 7 heures et demie du soir » → « HH:MM »."""
    plain = soft(text)
    if re.search(r"\bmidi\b", plain) and not re.search(r"apres midi", plain):
        return "12:00"
    if re.search(r"\bminuit\b", plain):
        return "00:00"
    # Nombre en chiffres ou en lettres connues, suivi de « heure(s) » ou « h » : jamais n'importe quel mot qui
    # contient un h (« chaque », « heures » coupé en « h » + « eures » : les deux bugs d'une version précédente).
    words = "|".join(sorted((re.escape(w) for w in _NUMBER_WORDS), key=len, reverse=True))
    match = re.search(rf"\b(?P<h>\d{{1,2}}|{words})\s*(?:heures?|h)(?![a-z])\s*"
                      rf"(?P<m>et demie?|et quart|moins le quart|\d{{1,2}}|{words})?"
                      r"(?P<ampm>\s*du matin|\s*du soir|\s*de l apres midi)?", plain)
    if match is None:
        match = re.search(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})\b(?P<ampm>)", plain)
    if match is None:
        return None
    hour = _number(match["h"])
    if hour is None:
        return None
    minutes_text = (match["m"] or "").strip()
    minute = 0
    if minutes_text in ("et demie", "et demi"):
        minute = 30
    elif minutes_text == "et quart":
        minute = 15
    elif minutes_text == "moins le quart":
        hour, minute = hour - 1, 45
    elif minutes_text:
        minute = _number(minutes_text) or 0
    evening = (match["ampm"] or "").strip() in ("du soir", "de l apres midi") or bool(
        re.search(r"\b(?:ce soir|tous les soirs|chaque soir|le soir|cet apres midi|l apres midi)\b", plain))
    if evening and 1 <= hour < 12:          # « ce soir à 8 heures » = 20 h, « cet après-midi à 3 heures » = 15 h
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def parse_days(text: str) -> tuple[list[int], bool] | None:
    """Jours de répétition, et si c'est « une seule fois ». None si rien de tel n'est dit."""
    plain = soft(text)
    if re.search(r"\b(?:tous les jours|chaque jour|quotidiennement|chaque (?:matin|soir)|tous les (?:matins|soirs))\b",
                 plain):
        return list(range(7)), False
    if re.search(r"\b(?:en semaine|les jours de semaine|du lundi au vendredi)\b", plain):
        return [0, 1, 2, 3, 4], False
    if re.search(r"\b(?:le week end|les week ends|chaque week end|le weekend)\b", plain):
        return [5, 6], False
    days = [index for index, day in enumerate(DAYS)
            if re.search(rf"\b(?:chaque|tous les|les|le) {day}s?\b", plain)]
    if days:
        return days, False
    if re.search(r"\b(?:aujourd hui|ce soir|ce matin|demain|cet apres midi)\b", plain):
        return [], True
    return None


def first_occurrence(hhmm: str, text: str, now: datetime) -> str:
    """Jour d'une automatisation unique : demain si c'est dit, sinon aujourd'hui, ou demain si l'heure est passée."""
    hour, minute = (int(p) for p in hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.search(r"\bdemain\b", soft(text)) or target <= now:
        target += timedelta(days=1)
    return target.strftime("%Y-%m-%d")


# -- le moteur

class Automations:
    def __init__(self, path: Path | None = None, clock: Callable[[], datetime] = datetime.now):
        self.path = path or data_dir() / "automations.json"
        self.clock = clock
        self._lock = threading.Lock()
        self._items: list[Automation] = self._load()
        self._config: list[Automation] = []
        self._stopped = threading.Event()
        self._plugged: bool | None = None
        self._battery_warned = False
        self.run_action: Callable[[Automation], None] = lambda automation: None

    # persistance

    def _load(self) -> list[Automation]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            backup = self.path.with_suffix(f".abime-{int(time.time())}.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            LOG.warning("Automatisations illisibles (%s) : mises de côté dans %s.", exc, backup.name)
            return []
        items = []
        for entry in raw.get("automations", []) if isinstance(raw, dict) else []:
            try:
                known = {k: v for k, v in entry.items() if k in Automation.__dataclass_fields__}
                items.append(Automation(**known))
            except (TypeError, ValueError):
                continue
        return items

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"automations": [asdict(a) for a in self._items]}, ensure_ascii=False,
                                        indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def load_config(self, entries: list[dict[str, Any]]) -> None:
        """Automatisations de config.yaml : validées au démarrage, jamais réécrites par Jarvis."""
        items = []
        for index, raw in enumerate(entries or [], 1):
            items.append(from_config(raw, index))
        with self._lock:
            self._config = items

    def all(self) -> list[Automation]:
        with self._lock:
            return [*self._config, *self._items]

    # modification

    def add(self, automation: Automation) -> str:
        with self._lock:
            if len(self._items) >= MAX_AUTOMATIONS:
                return "Tu as déjà cinquante automatisations : supprimes-en une d'abord."
            wanted = (automation.kind, automation.time, sorted(automation.days), automation.once, automation.event,
                      soft(automation.app), soft(automation.say), soft(automation.do))
            for existing in self._items:
                current = (existing.kind, existing.time, sorted(existing.days), existing.once, existing.event,
                           soft(existing.app), soft(existing.say), soft(existing.do))
                if current == wanted:
                    return "Cette automatisation existe déjà."
            self._items.append(automation)
            self._save()
        return f"C'est programmé : {automation.describe()}."

    def remove(self, query: str) -> str:
        wanted = soft(query)
        words = [w for w in wanted.split() if len(w) > 2 and w not in ("rappel", "automatisation", "supprime")]
        with self._lock:
            best, score = None, 0
            for item in self._items:
                text = soft(f"{item.name} {item.say} {item.do} {item.app} {spoken_days(item.days)}")
                value = sum(1 for w in words if w in text)
                if value > score:
                    best, score = item, value
            if best is None:
                in_config = any(soft(item.name) and soft(item.name) in wanted for item in self._config)
                if in_config:
                    return "Celle-ci vient de ton fichier de configuration : retire-la de config.yaml."
                return "Je ne trouve pas d'automatisation qui corresponde."
            self._items.remove(best)
            self._save()
        return f"J'ai supprimé : {best.describe()}."

    def clear(self) -> str:
        with self._lock:
            count = len(self._items)
            self._items = []
            self._save()
        return f"J'ai supprimé tes {count} automatisations." if count > 1 else \
            "J'ai supprimé ton automatisation." if count else "Tu n'avais aucune automatisation."

    def recite(self) -> str:
        items = self.all()
        if not items:
            return "Tu n'as aucune automatisation. Dis par exemple « tous les jours à 8 heures, rappelle-moi de… »."
        return f"Tu as {len(items)} automatisation" + ("s" if len(items) > 1 else "") + " : " + \
            " ; ".join(item.describe() for item in items) + "."

    # exécution

    def start(self) -> Automations:
        threading.Thread(target=self._run, name="automatisations", daemon=True).start()
        self.fire_event("startup")
        return self

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        while not self._stopped.wait(CHECK_EVERY_S):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - une automatisation ratée ne doit pas arrêter les suivantes
                LOG.exception("Vérification des automatisations en échec")

    def due(self, now: datetime | None = None) -> list[Automation]:
        """Automatisations horaires à lancer maintenant (au plus une fois par occurrence)."""
        now = now or self.clock()
        due = []
        for item in self.all():
            if item.kind != "time" or not item.time:
                continue
            hour, minute = (int(p) for p in item.time.split(":"))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target > now:
                target -= timedelta(days=1)           # l'occurrence la plus récente
            late = (now - target).total_seconds()
            if late > LATE_TOLERANCE_S:
                continue
            if item.once and item.once != target.strftime("%Y-%m-%d"):
                continue
            if item.days and target.weekday() not in item.days:
                continue
            if item.last_run >= target.timestamp():
                continue                              # déjà faite pour cette occurrence
            due.append(item)
        return due

    def tick(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        for item in self.due(now):
            self._fire(item, now)
        self._watch_power()

    def _fire(self, item: Automation, now: datetime) -> None:
        with self._lock:
            item.last_run = now.timestamp()
            if item.once and item in self._items:
                self._items.remove(item)              # une seule fois : elle disparaît après
            if item in self._items:
                self._save()
            elif item.once:
                self._save()
        LOG.info("⏰ Automatisation : %s", item.describe())
        try:
            self.run_action(item)
        except Exception:  # noqa: BLE001
            LOG.exception("Automatisation en échec : %s", item.describe())

    def fire_event(self, event: str, app: str = "") -> None:
        now = self.clock()
        for item in self.all():
            if item.kind != "event" or item.event != event:
                continue
            if event == "app_opened" and soft(item.app) not in soft(app):
                continue
            if now.timestamp() - item.last_run < 60:  # pas de rafale si l'événement se répète
                continue
            self._fire(item, now)

    def app_opened(self, app: str) -> None:
        self.fire_event("app_opened", app)

    def _watch_power(self) -> None:
        try:
            import psutil
            battery = psutil.sensors_battery()
        except Exception:  # noqa: BLE001 - ordinateur fixe ou capteur indisponible
            return
        if battery is None:
            return
        plugged = bool(battery.power_plugged)
        if self._plugged is not None and plugged != self._plugged:
            self.fire_event("power_plugged" if plugged else "power_unplugged")
        self._plugged = plugged
        if not plugged and battery.percent <= BATTERY_LOW and not self._battery_warned:
            self._battery_warned = True
            self.fire_event("battery_low")
        elif plugged or battery.percent > BATTERY_LOW + 5:
            self._battery_warned = False


def from_config(raw: dict[str, Any], index: int) -> Automation:
    """Une automatisation de config.yaml, avec un message clair si elle est mal écrite."""
    if not isinstance(raw, dict):
        raise ValueError(f"automations[{index}] : chaque automatisation est une liste de clés (name, when, say…).")
    name = str(raw.get("name") or f"automatisation {index}")
    when = raw.get("when")
    if not isinstance(when, dict):
        raise ValueError(f"automatisation « {name} » : il faut `when`, avec `time` ou `event`.")
    say, do = str(raw.get("say") or "").strip(), str(raw.get("do") or "").strip()
    if bool(say) == bool(do):
        raise ValueError(f"automatisation « {name} » : exactement une action, `say` ou `do`.")
    if do:
        from .commands import parse
        if parse(do) is None:
            raise ValueError(f"automatisation « {name} » : je ne comprends pas « {do} ».")
    if "event" in when:
        event = str(when["event"])
        if event not in EVENTS:
            raise ValueError(f"automatisation « {name} » : événement inconnu {event} ({', '.join(EVENTS)}).")
        app = str(when.get("app") or "")
        if event == "app_opened" and not app:
            raise ValueError(f"automatisation « {name} » : `app_opened` demande `app`.")
        return Automation(name, "event", event=event, app=app, say=say, do=do, source="config")
    at = parse_time(str(when.get("time") or ""))
    if at is None:
        raise ValueError(f"automatisation « {name} » : heure illisible « {when.get('time')} » (ex. \"08:30\").")
    days = []
    for day in when.get("days") or []:
        if soft(str(day)) not in DAYS:
            raise ValueError(f"automatisation « {name} » : jour inconnu « {day} ».")
        days.append(DAYS.index(soft(str(day))))
    return Automation(name, "time", time=at, days=days, say=say, do=do, source="config")


# -- à la voix

_REMIND = re.compile(r"^(?:rappelle(?:[\s-]moi)?|dis(?:[\s-]moi)?|previens(?:[\s-]moi)?)\s+"
                     r"(?:de\s+|d['’]\s*|que\s+|qu['’]\s*)?(?P<t>.+)$", re.IGNORECASE)


_TIME_IN_TEXT = re.compile(
    r"\b(?:\d{1,2}\s*(?:heures?|h)(?![a-zà-ÿ])(?:\s*\d{1,2})?|midi|minuit"
    r"|(?:une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|quinze|seize|vingt)"
    r"(?:[\s-](?:et[\s-])?(?:une|deux|trois|sept|huit|neuf))?\s+heures?)", re.IGNORECASE)
_CUE = re.compile(r"\b(?:tous les|chaque|en semaine|le week end|les (?:lundi|mardi|mercredi|jeudi|vendredi|samedi"
                  r"|dimanche)s?|demain|aujourd hui|ce soir|ce matin|cet apres midi|a \d+|a midi|a minuit"
                  r"|a [a-z]+ heures?)\b")


def _action_ok(action: str) -> bool:
    action = action.strip()
    if not action:
        return False
    if _REMIND.match(action):
        return True
    from .commands import parse
    return parse(action) is not None


def split_when_action(text: str) -> tuple[str, str] | None:
    """Sépare « quand » et « quoi ». Avec une virgule c'est direct ; sans (Whisper l'oublie souvent), on coupe au
    premier endroit où la fin de la phrase est une action que Jarvis comprend."""
    raw = text.strip().rstrip(".!?").strip()
    opened = re.match(r"^quand\s+j['’]?\s*ouvre\s+(?P<rest>.+)$", raw, re.IGNORECASE)
    if "," in raw:
        when, action = (part.strip() for part in raw.split(",", 1))
        if re.match(r"^quand\s+j['’]?\s*ouvre\s+\S", when, re.IGNORECASE) or (
                parse_time(when) and _CUE.search(soft(when))):
            return when, action
    if opened:
        words = opened["rest"].split()
        for cut in range(1, len(words)):
            if _action_ok(" ".join(words[cut:])):
                return "quand j'ouvre " + " ".join(words[:cut]), " ".join(words[cut:])
        return None
    found = _TIME_IN_TEXT.search(raw)
    if found is None:
        return None
    head, tail = raw[:found.end()], raw[found.end():].split()
    for cut in range(len(tail)):
        when, action = (head + " " + " ".join(tail[:cut])).strip(), " ".join(tail[cut:])
        if parse_time(when) and _CUE.search(soft(when)) and _action_ok(action):
            return when, action
    return None


def from_voice(text: str, now: datetime) -> tuple[Automation | None, str]:
    """(automatisation, message d'erreur). « tous les jours à 8 h, rappelle-moi de… », « quand j'ouvre X, … »."""
    parts = split_when_action(text)
    if parts is None:
        return None, "Dis quand, puis quoi, par exemple « tous les jours à 8 heures, rappelle-moi de… »."
    when, action = parts
    if opened := re.match(r"^quand\s+j['’]?\s*ouvre\s+(?P<app>.+)$", when, re.IGNORECASE):
        app = opened["app"].strip()
        return _with_action(Automation(f"quand j'ouvre {app}", "event", event="app_opened", app=app), action)
    at = parse_time(when)
    if at is None:
        return None, "Je n'ai pas compris l'heure : dis par exemple « à 8 heures » ou « à 18 heures 30 »."
    days_once = parse_days(when)
    days, once = days_once if days_once is not None else ([], True)
    automation = Automation(action[:60], "time", time=at, days=days,
                            once=first_occurrence(at, when, now) if once else "")
    return _with_action(automation, action)


def _with_action(automation: Automation, action: str) -> tuple[Automation | None, str]:
    action = action.strip()
    if match := _REMIND.match(action):
        reminder = match["t"].strip()
        automation.say = "Rappel : " + reminder[0].lower() + reminder[1:] + "."
        automation.name = reminder[:60]
        return automation, ""
    from .commands import parse
    if parse(action) is None:
        return None, f"Je ne sais pas faire « {action} » tout seul : dis « rappelle-moi de… » ou une commande simple."
    automation.do = action
    return automation, ""


def is_automation_request(text: str) -> bool:
    """La phrase programme-t-elle quelque chose ? (« rappelle-moi dans 10 minutes » reste un minuteur.)"""
    if "heure" not in text.lower() and not re.search(r"\d\s*h\b|midi|minuit|ouvre", text, re.IGNORECASE):
        return False                            # chemin rapide : la plupart des phrases s'arrêtent là
    return split_when_action(text) is not None
