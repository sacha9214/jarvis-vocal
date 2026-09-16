"""Agenda : dates dites à voix haute, agendas iCal (Google, Outlook, iCloud), rendez-vous de Jarvis, rappels."""
from datetime import date, datetime

import pytest

import jarvis.tools.builtin as builtin
from jarvis import commands
from jarvis.agenda import Agenda, masked, parse_day, parse_new_event

WEDNESDAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 16, 14, 0)

# Un agenda comme Google l'exporte : fuseau horaire, récurrence avec exception, journée entière, heure UTC.
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
X-WR-CALNAME:Perso
BEGIN:VTIMEZONE
TZID:Europe/Paris
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:dentiste@exemple
SUMMARY:Dentiste
LOCATION:Cabinet du Dr Martin
DTSTART;TZID=Europe/Paris:20260917T140000
DTEND;TZID=Europe/Paris:20260917T143000
END:VEVENT
BEGIN:VEVENT
UID:foot@exemple
SUMMARY:Entraînement de foot
DTSTART;TZID=Europe/Paris:20260901T190000
DTEND;TZID=Europe/Paris:20260901T203000
RRULE:FREQ=WEEKLY;BYDAY=TU
EXDATE;TZID=Europe/Paris:20260922T190000
END:VEVENT
BEGIN:VEVENT
UID:anniv@exemple
SUMMARY:Anniversaire de Léa
DTSTART;VALUE=DATE:20260918
DTEND;VALUE=DATE:20260919
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n").encode("utf-8")


@pytest.fixture
def agenda(tmp_path):
    (tmp_path / "perso.ics").write_bytes(ICS)
    return Agenda(tmp_path / "agenda.json", sources=[str(tmp_path / "perso.ics")], clock=lambda: NOW)


@pytest.mark.parametrize(("spoken", "expected"), [
    ("aujourd'hui", date(2026, 9, 16)), ("demain", date(2026, 9, 17)), ("après-demain", date(2026, 9, 18)),
    ("jeudi", date(2026, 9, 17)), ("mercredi", date(2026, 9, 16)), ("mercredi prochain", date(2026, 9, 23)),
    ("lundi prochain", date(2026, 9, 21)), ("le 25", date(2026, 9, 25)), ("le 10", date(2026, 10, 10)),
    ("le 25 septembre", date(2026, 9, 25)), ("le 3 janvier", date(2027, 1, 3)), ("le 25/09", date(2026, 9, 25)),
    ("le premier octobre", date(2026, 10, 1)),
])
def test_spoken_days(spoken, expected):
    assert parse_day(spoken, WEDNESDAY) == expected


@pytest.mark.parametrize(("phrase", "title", "start"), [
    ("ajoute rendez-vous chez le dentiste jeudi à 14 heures", "Rendez-vous chez le dentiste",
     datetime(2026, 9, 17, 14)),
    ("note dans mon agenda réunion avec Paul demain à 10h30", "Réunion avec Paul", datetime(2026, 9, 17, 10, 30)),
    ("ajoute dîner chez Léa le 25 septembre à 20 heures", "Dîner chez Léa", datetime(2026, 9, 25, 20)),
    ("ajoute match de foot samedi à 15 heures 30", "Match de foot", datetime(2026, 9, 19, 15, 30)),
    ("ajoute réunion le 3 octobre à 9 heures moins le quart", "Réunion", datetime(2026, 10, 3, 8, 45)),
    ("ajoute séance de sport vendredi à 7 heures du matin", "Séance de sport", datetime(2026, 9, 18, 7)),
])
def test_new_events_keep_a_clean_title(phrase, title, start):
    """Une version précédente laissait « à heures » dans le titre : le nombre de l'heure partait comme un jour."""
    assert parse_new_event(phrase, NOW) == (title, start)


def test_an_event_needs_a_title_a_day_and_an_hour():
    assert parse_new_event("ajoute rendez-vous médecin", NOW) is None
    assert parse_new_event("ajoute jeudi à 14 heures", NOW) is None


def test_reading_a_google_calendar(agenda):
    assert agenda.recite_day(date(2026, 9, 17), WEDNESDAY) == \
        "Demain, tu as 1 chose : à 14 heures, Dentiste, Cabinet du Dr Martin."
    assert agenda.recite_day(date(2026, 9, 18), WEDNESDAY) == \
        "Après-demain, tu as 1 chose : toute la journée, Anniversaire de Léa."
    assert agenda.recite_day(date(2026, 9, 29), WEDNESDAY) == \
        "Mardi 29 septembre, tu as 1 chose : à 19 heures, Entraînement de foot."          # récurrence
    assert agenda.recite_day(date(2026, 9, 22), WEDNESDAY) == "Rien de prévu le mardi 22 septembre."   # exception


def test_next_event_and_free_time(agenda):
    assert agenda.next_event().title == "Dentiste"
    assert agenda.is_free(datetime(2026, 9, 17, 14, 10), WEDNESDAY) == "Non, demain à 14 heures 10 tu as Dentiste."
    assert agenda.is_free(datetime(2026, 9, 17, 16, 0), WEDNESDAY) == "Oui, tu es libre demain à 16 heures."


def test_jarvis_events_merge_with_external_ones_and_survive_a_restart(agenda, tmp_path):
    agenda.add("Rendez-vous chez le garagiste", datetime(2026, 9, 17, 9, 0))
    expected = ("Demain, tu as 2 choses : à 9 heures, Rendez-vous chez le garagiste ; "
                "à 14 heures, Dentiste, Cabinet du Dr Martin.")
    assert agenda.recite_day(date(2026, 9, 17), WEDNESDAY) == expected
    again = Agenda(tmp_path / "agenda.json", clock=lambda: NOW)
    assert [e.title for e in again.day(date(2026, 9, 17))] == ["Rendez-vous chez le garagiste"]
    assert agenda.remove("garagiste").title == "Rendez-vous chez le garagiste"
    assert agenda.remove("dentiste") is None                      # externe : se retire chez Google, pas ici


def test_a_reminder_is_said_once_before_each_event(agenda):
    said = []
    agenda.announce = said.append
    agenda.tick(datetime(2026, 9, 17, 13, 40))
    assert said == []                                             # 20 minutes avant : trop tôt
    agenda.tick(datetime(2026, 9, 17, 13, 52))
    assert said == ["Rappel : Dentiste dans 8 minutes, Cabinet du Dr Martin."]
    agenda.tick(datetime(2026, 9, 17, 13, 55))
    assert len(said) == 1                                         # pas deux fois
    agenda.tick(datetime(2026, 9, 18, 0, 0))
    assert len(said) == 1                                         # journée entière : pas de rappel à minuit


def test_an_unreachable_calendar_does_not_break_the_others(tmp_path):
    calls = []

    def flaky(source):
        calls.append(source)
        if len(calls) > 1:
            raise OSError("hors ligne")
        return ICS

    agenda = Agenda(tmp_path / "agenda.json", sources=["https://calendar.google.com/secret/basic.ics"],
                    fetch=flaky, clock=lambda: NOW)
    assert agenda.day(date(2026, 9, 17))[0].title == "Dentiste"
    agenda._cache = {k: (0.0, v[1]) for k, v in agenda._cache.items()}      # cache expiré, réseau coupé
    assert agenda.day(date(2026, 9, 17))[0].title == "Dentiste"             # dernière version connue


def test_the_private_address_never_appears_in_logs():
    assert masked("https://calendar.google.com/calendar/ical/moi%40gmail.com/private-abc123/basic.ics") == \
        "calendar.google.com/…"
    assert masked("webcal://p42-caldav.icloud.com/published/2/SECRET") == "p42-caldav.icloud.com/…"
    assert masked("/Users/moi/perso.ics") == "fichier local"


@pytest.mark.parametrize(("phrase", "arguments"), [
    ("qu'est-ce que j'ai demain", {"action": "day", "day": "demain"}),
    ("qu'est-ce que j'ai de prévu jeudi", {"action": "day", "day": "jeudi"}),
    ("qu'est-ce que j'ai cette semaine", {"action": "week"}),
    ("c'est quoi mon prochain rendez-vous", {"action": "next"}),
    ("est-ce que je suis libre demain à 15 heures", {"action": "free", "text": "demain à 15 heures"}),
    ("supprime le rendez-vous chez le dentiste", {"action": "remove", "text": "le dentiste"}),
])
def test_agenda_questions_need_no_llm(phrase, arguments):
    command = commands.parse(phrase)
    assert command.tool == "agenda" and command.arguments == arguments


def test_adding_by_voice_including_when_the_request_comes_last():
    assert commands.parse("ajoute rendez-vous chez le dentiste jeudi à 14 heures").arguments["action"] == "add"
    command = commands.parse("Je dois voir le dentiste vendredi à 11 heures, tu peux le noter ?")
    assert command.tool == "agenda" and command.arguments == {"action": "add",
                                                               "text": "ajoute voir le dentiste vendredi à 11 heures"}


def test_agenda_phrases_do_not_steal_other_commands():
    assert commands.parse("note que ma sœur s'appelle Léa").tool == "memory"
    assert commands.parse("tous les jours à 8 heures, rappelle-moi de boire").tool == "automations"
    assert commands.parse("mets un minuteur de 5 minutes").tool == "set_timer"
    assert commands.parse("demain à 14 heures, c'est l'anniversaire de Léa") is None   # ni rappel ni commande


def test_the_agenda_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin, "AGENDA", Agenda(tmp_path / "agenda.json"))
    said = builtin.agenda_tool("add", text="ajoute rendez-vous chez le coiffeur le 1er janvier 2099 à 10 heures")
    assert said.startswith("C'est ajouté : Rendez-vous chez le coiffeur")
    assert builtin.agenda_tool("add", text="ajoute truc") .startswith("Dis-moi le rendez-vous")
    assert builtin.agenda_tool("remove", text="coiffeur").startswith("J'ai supprimé Rendez-vous chez le coiffeur")
    assert builtin.agenda_tool("free") == "Dis-moi le jour et l'heure."
