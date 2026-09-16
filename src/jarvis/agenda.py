"""Agenda : tes rendez-vous, et un rappel à voix haute avant chacun.

Deux sources, fusionnées :
- l'agenda de Jarvis, que tu remplis à la voix (« ajoute rendez-vous chez le dentiste jeudi à 14 heures ») ;
- tes agendas Google, Outlook ou iCloud, **en lecture**, par leur adresse iCal privée, à coller dans
  config.yaml (`agenda.sources`). Aucune connexion à ton compte, aucun mot de passe : Google, Outlook et
  iCloud fournissent chacun cette adresse dans leurs réglages de partage.

« Qu'est-ce que j'ai demain ? », « c'est quoi mon prochain rendez-vous ? », « est-ce que je suis libre
jeudi à 15 heures ? ». Les récurrences (tous les lundis, sauf tel jour…) sont gérées.

L'adresse iCal donne accès à ton agenda : elle n'est jamais écrite dans le journal ni envoyée au modèle.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .automations import DAYS, parse_time, spoken_time
from .paths import data_dir

LOG = logging.getLogger("jarvis.agenda")
MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre",
          "novembre", "decembre"]
SPOKEN_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                 "novembre", "décembre"]
CACHE_S = 10 * 60
MAX_ICS_BYTES = 5_000_000
DEFAULT_DURATION_MIN = 60
MAX_SPOKEN_EVENTS = 6


@dataclass
class Event:
    title: str
    start: float                     # horodatage
    end: float
    all_day: bool = False
    location: str = ""
    source: str = "jarvis"           # « jarvis » ou le nom de l'agenda externe
    uid: str = ""

    @property
    def starts(self) -> datetime:
        return datetime.fromtimestamp(self.start)


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9:/]+", " ", text).strip()


def masked(url: str) -> str:
    """Une adresse iCal donne accès à l'agenda : dans le journal, seulement l'hôte."""
    match = re.match(r"^(?:https?|webcal)://([^/]+)", url)
    return f"{match.group(1)}/…" if match else "fichier local"


# -- dates dites à voix haute

def parse_day(text: str, today: date) -> date | None:
    """« aujourd'hui », « demain », « après-demain », « jeudi », « lundi prochain », « le 25 », « le 25 septembre »,
    « le 25/09 » → une date. Un jour de semaine désigne le prochain (aujourd'hui compris)."""
    plain = soft(text)
    if re.search(r"\bapres demain\b", plain):
        return today + timedelta(days=2)
    if re.search(r"\bdemain\b", plain):
        return today + timedelta(days=1)
    if re.search(r"\b(?:aujourd hui|ce soir|ce matin|cet apres midi|ce midi)\b", plain):
        return today
    if match := re.search(r"\b(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{2,4}))?\b", plain):
        return _build(today, int(match["d"]), int(match["m"]), match["y"])
    if match := re.search(r"\b(?:le )?(?P<d>\d{1,2}|premier|1er)(?: (?P<m>" + "|".join(MONTHS) + r"))?"
                          r"(?: (?P<y>\d{4}))?\b", plain):
        if match["m"] or re.search(r"\ble (?:\d{1,2}|premier|1er)\b", plain):
            day = 1 if match["d"] in ("premier", "1er") else int(match["d"])
            month = MONTHS.index(match["m"]) + 1 if match["m"] else None
            return _build(today, day, month, match["y"])
    for index, name in enumerate(DAYS):
        if re.search(rf"\b{name}\b", plain):
            ahead = (index - today.weekday()) % 7
            if re.search(rf"\b{name} prochain\b", plain) and ahead == 0:
                ahead = 7
            return today + timedelta(days=ahead)
    return None


def _build(today: date, day: int, month: int | None, year: str | None) -> date | None:
    try:
        if month is None:                          # « le 25 » : ce mois-ci, ou le mois suivant s'il est passé
            target = date(today.year, today.month, day)
            if target < today:
                target = date(today.year + (today.month == 12), today.month % 12 + 1, day)
            return target
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        target = date(today.year, month, day)
        return target if target >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def spoken_day(day: date, today: date) -> str:
    if day == today:
        return "aujourd'hui"
    if day == today + timedelta(days=1):
        return "demain"
    if day == today + timedelta(days=2):
        return "après-demain"
    return f"{DAYS[day.weekday()]} {day.day} {SPOKEN_MONTHS[day.month - 1]}"


def spoken_event(event: Event, today: date, with_day: bool = False) -> str:
    when = "toute la journée" if event.all_day else "à " + spoken_time(event.starts.strftime("%H:%M"))
    day = f"{spoken_day(event.starts.date(), today)} " if with_day else ""
    where = f", {event.location}" if event.location else ""
    return f"{day}{when}, {event.title}{where}"


# -- l'agenda

class Agenda:
    def __init__(self, path: Path | None = None, sources: list[str] | None = None, remind_minutes: int = 10,
                 fetch: Callable[[str], bytes] | None = None, clock: Callable[[], datetime] = datetime.now):
        self.path = path or data_dir() / "agenda.json"
        self.sources = list(sources or [])
        self.remind_minutes = remind_minutes
        self.clock = clock
        self._fetch = fetch or _download
        self._lock = threading.Lock()
        self._events: list[Event] = self._load()
        self._cache: dict[str, tuple[float, object]] = {}
        self._reminded: set[str] = set()
        self.announce: Callable[[str], None] = lambda text: None

    def _load(self) -> list[Event]:
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
            LOG.warning("Agenda illisible (%s) : mis de côté dans %s.", exc, backup.name)
            return []
        events = []
        for item in raw.get("events", []) if isinstance(raw, dict) else []:
            try:
                events.append(Event(**{k: v for k, v in item.items() if k in Event.__dataclass_fields__}))
            except (TypeError, ValueError):
                continue
        return events

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"events": [asdict(e) for e in self._events]}, ensure_ascii=False,
                                        indent=2), encoding="utf-8")
        temporary.replace(self.path)

    # lecture

    def _external(self, start: datetime, end: datetime) -> list[Event]:
        import icalendar
        import recurring_ical_events

        events: list[Event] = []
        for index, source in enumerate(self.sources):
            name = f"agenda {index + 1}"
            cached = self._cache.get(source)
            if cached is None or time.monotonic() - cached[0] > CACHE_S:
                try:
                    data = self._fetch(source)
                    parsed = icalendar.Calendar.from_ical(data)
                except Exception as exc:  # noqa: BLE001 - un agenda indisponible n'empêche pas les autres
                    LOG.warning("Agenda %s illisible (%s) : %s", name, masked(source), exc)
                    if cached is None:
                        continue
                    parsed = cached[1]                 # hors ligne : la dernière version connue
                else:
                    self._cache[source] = (time.monotonic(), parsed)
            else:
                parsed = cached[1]
            label = str(parsed.get("X-WR-CALNAME") or name)
            for component in recurring_ical_events.of(parsed).between(start, end):
                event = _from_component(component, label)
                if event is not None:
                    events.append(event)
        return events

    def between(self, start: datetime, end: datetime) -> list[Event]:
        with self._lock:
            local = [e for e in self._events if e.end > start.timestamp() and e.start < end.timestamp()]
        events = local + (self._external(start, end) if self.sources else [])
        return sorted(events, key=lambda e: (e.start, e.title))

    def day(self, day: date) -> list[Event]:
        start = datetime.combine(day, datetime.min.time())
        return self.between(start, start + timedelta(days=1))

    def next_event(self, now: datetime | None = None) -> Event | None:
        now = now or self.clock()
        upcoming = [e for e in self.between(now, now + timedelta(days=60)) if e.start >= now.timestamp()
                    and not e.all_day]
        return upcoming[0] if upcoming else None

    # écriture (agenda de Jarvis seulement)

    def add(self, title: str, start: datetime, minutes: int = DEFAULT_DURATION_MIN) -> Event:
        event = Event(title.strip()[:120], start.timestamp(), (start + timedelta(minutes=minutes)).timestamp(),
                      uid=uuid.uuid4().hex)
        with self._lock:
            self._events.append(event)
            self._events = [e for e in self._events if e.end > time.time() - 90 * 86400]   # oublie le vieux
            self._save()
        return event

    def remove(self, query: str) -> Event | None:
        words = [w for w in soft(query).split() if len(w) > 2 and w not in ("rendez", "vous", "avec", "chez")]
        now = self.clock().timestamp()
        with self._lock:
            best, score = None, 0
            for event in self._events:
                value = sum(1 for w in words if w in soft(event.title))
                if value > score or (value == score and value and best and event.start >= now > best.start):
                    best, score = event, value
            if best is None or not score:
                return None
            self._events.remove(best)
            self._save()
        return best

    # rappels

    def reminders(self, now: datetime | None = None) -> list[Event]:
        """Événements qui commencent dans les `remind_minutes`, pas encore rappelés."""
        now = now or self.clock()
        due = []
        for event in self.between(now, now + timedelta(minutes=self.remind_minutes)):
            key = f"{event.uid or event.title}@{int(event.start)}"
            if event.all_day or event.start < now.timestamp() or key in self._reminded:
                continue
            self._reminded.add(key)
            due.append(event)
        return due

    def tick(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        for event in self.reminders(now):
            minutes = max(1, round((event.start - now.timestamp()) / 60))
            self.announce(f"Rappel : {event.title} dans {minutes} minute{'s' if minutes > 1 else ''}"
                          + (f", {event.location}." if event.location else "."))

    def start(self, every_s: float = 30.0) -> Agenda:
        if self.remind_minutes <= 0:
            return self

        def loop() -> None:
            while True:
                time.sleep(every_s)
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - un agenda injoignable n'arrête pas les rappels suivants
                    LOG.exception("Rappels d'agenda en échec")
        threading.Thread(target=loop, name="agenda", daemon=True).start()
        return self

    # phrases

    def recite_day(self, day: date, today: date | None = None) -> str:
        today = today or self.clock().date()
        events = self.day(day)
        when = spoken_day(day, today)
        if not events:
            return f"Rien de prévu {when}." if when in ("aujourd'hui", "demain", "après-demain") else \
                f"Rien de prévu le {when}."
        head = f"{when.capitalize()}, tu as {len(events)} chose" + ("s" if len(events) > 1 else "") + " : "
        listed = " ; ".join(spoken_event(e, today) for e in events[:MAX_SPOKEN_EVENTS])
        more = f" ; et {len(events) - MAX_SPOKEN_EVENTS} autres" if len(events) > MAX_SPOKEN_EVENTS else ""
        return head + listed + more + "."

    def recite_week(self, today: date | None = None) -> str:
        today = today or self.clock().date()
        start = datetime.combine(today, datetime.min.time())
        events = self.between(start, start + timedelta(days=7))
        if not events:
            return "Rien de prévu dans les sept prochains jours."
        listed = " ; ".join(spoken_event(e, today, with_day=True) for e in events[:MAX_SPOKEN_EVENTS])
        more = f" ; et {len(events) - MAX_SPOKEN_EVENTS} autres" if len(events) > MAX_SPOKEN_EVENTS else ""
        return f"Cette semaine : {listed}{more}."

    def is_free(self, moment: datetime, today: date | None = None) -> str:
        today = today or self.clock().date()
        busy = [e for e in self.between(moment, moment + timedelta(minutes=1)) if not e.all_day]
        when = f"{spoken_day(moment.date(), today)} à {spoken_time(moment.strftime('%H:%M'))}"
        if not busy:
            return f"Oui, tu es libre {when}."
        return f"Non, {when} tu as {busy[0].title}."


def _from_component(component, label: str) -> Event | None:
    start = component.get("DTSTART")
    if start is None:
        return None
    begin = start.dt
    finish = component.get("DTEND").dt if component.get("DTEND") is not None else None
    all_day = not isinstance(begin, datetime)
    if all_day:
        begin_dt = datetime.combine(begin, datetime.min.time())
        end_dt = datetime.combine(finish, datetime.min.time()) if finish else begin_dt + timedelta(days=1)
    else:
        begin_dt = begin.astimezone() if begin.tzinfo else begin    # heure locale de la machine
        end_dt = (finish.astimezone() if getattr(finish, "tzinfo", None) else finish) if finish else \
            begin_dt + timedelta(minutes=DEFAULT_DURATION_MIN)
    return Event(str(component.get("SUMMARY") or "Événement sans titre"), begin_dt.timestamp(), end_dt.timestamp(),
                 all_day, str(component.get("LOCATION") or ""), label, str(component.get("UID") or ""))


def _download(source: str) -> bytes:
    if not re.match(r"^(?:https?|webcal)://", source):
        path = Path(source).expanduser()
        return path.read_bytes()[:MAX_ICS_BYTES]
    import httpx

    url = re.sub(r"^webcal://", "https://", source)
    with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(15.0, connect=5.0)) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_ICS_BYTES:
                raise RuntimeError("agenda trop volumineux")
            chunks.append(chunk)
    return b"".join(chunks)


# -- ce qui se dit à la voix

_VERB = re.compile(r"^(?:ajoute|note|mets|inscris|programme|cree|crée)(?:[\s-]moi)?\s+"
                   r"(?:dans\s+(?:mon|l['’])\s*agenda\s*:?\s*)?(?:(?:un|le)\s+)?", re.IGNORECASE)
_HOUR_WORDS = re.compile(r"\b(?:à|a)\s+(?:\d{1,2}\s*(?:heures?|h)(?:\s*\d{1,2}|\s+et\s+demie?|\s+et\s+quart"
                         r"|\s+moins\s+le\s+quart)?|midi|minuit|[a-zé-]+(?:[\s-][a-zé-]+)?\s+heures?"
                         r"(?:\s+(?:trente|quinze|quarante-cinq|et\s+demie?))?)(?:\s+du\s+(?:matin|soir))?",
                         re.IGNORECASE)
_DAY_WORDS = re.compile(
    r"\b(?:aujourd['’]hui|après[\s-]demain|apres[\s-]demain|demain|ce soir|ce matin|cet après[\s-]midi"
    r"|le\s+(?:\d{1,2}|premier|1er)(?:\s+(?:janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout"
    r"|septembre|octobre|novembre|décembre|decembre))?(?:\s+\d{4})?"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|(?:" + "|".join(DAYS) + r")(?:\s+prochain)?)\b", re.IGNORECASE)


def parse_new_event(text: str, now: datetime) -> tuple[str, datetime] | None:
    """« ajoute rendez-vous chez le dentiste jeudi à 14 heures » → (« Rendez-vous chez le dentiste », jeudi 14 h)."""
    raw = text.strip().rstrip(".!?")
    body = _VERB.sub("", raw).strip()
    body = re.sub(r"\s+(?:dans|à)\s+(?:mon|l['’])\s*agenda\b", "", body, flags=re.IGNORECASE)
    at = parse_time(body)
    day = parse_day(body, now.date())
    if at is None or day is None:
        return None
    # Le titre : ce qui reste. L'heure d'abord, sinon « 14 » partirait comme un numéro de jour.
    title = _DAY_WORDS.sub(" ", _HOUR_WORDS.sub(" ", body))
    title = re.sub(r"\s+", " ", title).strip(" ,;:-")
    if len(title) < 2:
        return None
    hour, minute = (int(p) for p in at.split(":"))
    return title[0].upper() + title[1:], datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
