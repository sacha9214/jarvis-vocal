"""Automatisations : à une heure ou sur un événement, à la voix ou dans config.yaml.

L'horloge est simulée : rien n'attend réellement 8 heures du matin.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import jarvis.tools.builtin as builtin
from jarvis import commands
from jarvis.automations import (
    Automation,
    Automations,
    from_config,
    from_voice,
    is_automation_request,
    parse_days,
    parse_time,
)
from jarvis.system.foreground import Foreground, ForegroundTracker

WEDNESDAY_2PM = datetime(2026, 9, 16, 14, 0)          # un mercredi


@pytest.mark.parametrize(("spoken", "expected"), [
    ("à 8 heures", "08:00"), ("à 8h30", "08:30"), ("à 18 heures 30", "18:30"), ("à midi", "12:00"),
    ("à minuit", "00:00"), ("à 7 heures et demie du soir", "19:30"), ("à huit heures", "08:00"),
    ("à 9 heures moins le quart", "08:45"), ("à 22:15", "22:15"), ("ce soir à 8 heures", "20:00"),
    ("cet après-midi à 3 heures", "15:00"), ("à dix-huit heures trente", "18:30"),
    ("chaque lundi à 9 heures", "09:00"),
])
def test_spoken_times(spoken, expected):
    assert parse_time(spoken) == expected


def test_words_containing_an_h_are_not_hours():
    """Une version précédente lisait le « h » de « chaque » ou de « heures » comme l'unité."""
    assert parse_time("chaque jour") is None
    assert parse_time("dans la matinée") is None
    assert parse_time("à quelle heure") is None


def test_spoken_days():
    assert parse_days("tous les jours") == (list(range(7)), False)
    assert parse_days("tous les soirs") == (list(range(7)), False)
    assert parse_days("en semaine") == ([0, 1, 2, 3, 4], False)
    assert parse_days("le week-end") == ([5, 6], False)
    assert parse_days("chaque lundi") == ([0], False)
    assert parse_days("demain") == ([], True)
    assert parse_days("à 8 heures") is None


@pytest.mark.parametrize(("phrase", "description"), [
    ("tous les jours à 8 heures, rappelle-moi de prendre mes médicaments",
     "tous les jours à 8 heures, dire « Rappel : prendre mes médicaments. »"),
    ("Tous les jours à 8 heures rappelle-moi de prendre mes médicaments",         # sans virgule
     "tous les jours à 8 heures, dire « Rappel : prendre mes médicaments. »"),
    ("chaque lundi à 9 heures dis-moi de faire le point", "chaque lundi à 9 heures, dire « Rappel : faire le point. »"),
    ("en semaine à 18 heures 30 ferme Discord", "en semaine à 18 heures 30, « ferme Discord »"),
    ("demain à 7 heures, rappelle-moi d'appeler le garage",
     "le jeudi 17 à 7 heures, dire « Rappel : appeler le garage. »"),
    ("ce soir à 8 heures, rappelle-moi d'appeler maman",
     "le mercredi 16 à 20 heures, dire « Rappel : appeler maman. »"),
    ("à 10 heures, rappelle-moi de sortir le linge",                               # 10 h est passé : demain
     "le jeudi 17 à 10 heures, dire « Rappel : sortir le linge. »"),
    ("quand j'ouvre Visual Studio Code mets le volume à 20",
     "quand tu ouvres Visual Studio Code, « mets le volume à 20 »"),
    ("à 23 heures 45, rappelle-moi de fermer les volets",                         # deux chiffres, heure à venir
     "le mercredi 16 à 23 heures 45, dire « Rappel : fermer les volets. »"),
])
def test_creating_an_automation_by_voice(phrase, description):
    automation, error = from_voice(phrase, WEDNESDAY_2PM)
    assert error == "" and automation.describe() == description


def test_what_jarvis_cannot_do_alone_is_refused_at_creation():
    automation, error = from_voice("tous les jours à 8 heures, fais le café", WEDNESDAY_2PM)
    assert automation is None and "Je ne sais pas faire « fais le café »" in error


@pytest.mark.parametrize("phrase", [
    "il est 8 heures", "ouvre le fichier rapport de 8 heures", "à quelle heure je me lève",
    "rappelle-moi dans 10 minutes", "mets un minuteur de 8 minutes", "à 8 heures du matin je bois un café",
    "éteins l'ordinateur dans 25 minutes",
])
def test_ordinary_sentences_are_not_automations(phrase):
    assert not is_automation_request(phrase)


def test_an_automation_fires_once_per_occurrence(tmp_path):
    fired = []
    engine = Automations(tmp_path / "automations.json")
    engine.run_action = fired.append
    engine.add(Automation("médicaments", "time", time="08:00", say="Rappel : médicaments."))

    engine.tick(datetime(2026, 9, 17, 7, 59))
    assert fired == []                                          # pas encore l'heure
    engine.tick(datetime(2026, 9, 17, 8, 0, 10))
    assert len(fired) == 1
    engine.tick(datetime(2026, 9, 17, 8, 0, 25))
    assert len(fired) == 1                                      # pas deux fois pour la même occurrence
    engine.tick(datetime(2026, 9, 18, 8, 0, 5))
    assert len(fired) == 2                                      # le lendemain, à nouveau

    restarted = Automations(tmp_path / "automations.json")      # redémarrage de Jarvis
    restarted.run_action = fired.append
    restarted.tick(datetime(2026, 9, 18, 8, 1))
    assert len(fired) == 2                                      # l'occurrence du jour est déjà faite


def test_a_sleeping_computer_catches_up_but_not_hours_later(tmp_path):
    fired = []
    engine = Automations(tmp_path / "automations.json")
    engine.run_action = fired.append
    engine.add(Automation("a", "time", time="08:00", say="a"))
    engine.tick(datetime(2026, 9, 17, 8, 7))                    # réveillé 7 minutes après : rattrapé
    assert len(fired) == 1
    engine.add(Automation("b", "time", time="09:00", say="b"))
    engine.tick(datetime(2026, 9, 17, 11, 0))                   # 2 heures après : on laisse tomber
    assert len(fired) == 1


def test_days_and_one_time_automations(tmp_path):
    fired = []
    engine = Automations(tmp_path / "automations.json")
    engine.run_action = lambda item: fired.append(item.name)
    engine.add(Automation("semaine", "time", time="18:00", days=[0, 1, 2, 3, 4], say="x"))
    engine.add(Automation("une fois", "time", time="18:00", once="2026-09-17", say="y"))
    engine.tick(datetime(2026, 9, 19, 18, 0, 5))                # samedi
    assert fired == []
    engine.tick(datetime(2026, 9, 17, 18, 0, 5))                # jeudi 17
    assert sorted(fired) == ["semaine", "une fois"]
    assert [a.name for a in engine.all()] == ["semaine"]        # l'unique a disparu après usage


def test_events_fire_with_a_cooldown(tmp_path):
    fired = []
    now = [WEDNESDAY_2PM]
    engine = Automations(tmp_path / "automations.json", clock=lambda: now[0])
    engine.run_action = lambda item: fired.append(item.name)
    engine.add(Automation("spotify", "event", event="app_opened", app="Spotify", do="mets le volume à 40"))
    engine.app_opened("Discord")
    assert fired == []
    engine.app_opened("Spotify")
    engine.app_opened("Spotify")                                # aller-retour rapide : pas de rafale
    assert fired == ["spotify"]
    now[0] += timedelta(minutes=2)
    engine.app_opened("Spotify")
    assert fired == ["spotify", "spotify"]


def test_switching_application_triggers_the_event():
    seen = []
    probes = iter([Foreground("Discord", "", 1, 1.0), Foreground("Discord", "", 1, 2.0),
                   Foreground("Spotify", "", 2, 3.0)])
    tracker = ForegroundTracker(probe_fn=lambda: next(probes))
    tracker.on_switch = seen.append
    tracker.poll()                                              # premier relevé : rien n'a « changé »
    tracker.poll()
    tracker.poll()
    assert seen == ["Spotify"]


def test_removing_listing_and_persistence(tmp_path):
    engine = Automations(tmp_path / "automations.json")
    assert engine.recite().startswith("Tu n'as aucune automatisation")
    automation, _ = from_voice("tous les jours à 8 heures, rappelle-moi de prendre mes médicaments", WEDNESDAY_2PM)
    assert engine.add(automation).startswith("C'est programmé")
    assert engine.add(automation) == "Cette automatisation existe déjà."
    assert "prendre mes médicaments" in engine.recite()
    assert engine.remove("rappel des médicaments").startswith("J'ai supprimé")
    assert engine.remove("médicaments") == "Je ne trouve pas d'automatisation qui corresponde."
    engine.add(automation)
    assert Automations(tmp_path / "automations.json").all()[0].time == "08:00"   # gardée au redémarrage
    assert engine.clear() == "J'ai supprimé ton automatisation."


def test_config_automations_are_validated_with_clear_messages():
    assert from_config({"name": "batterie", "when": {"event": "battery_low"}, "say": "Branche le chargeur."}, 1).event \
        == "battery_low"
    soir = from_config({"name": "soir", "when": {"time": "22:30", "days": ["lundi", "mardi"]},
                        "do": "mets l'ordinateur en veille"}, 1)
    assert (soir.time, soir.days) == ("22:30", [0, 1])
    for raw, message in (
        ({"name": "a", "say": "x"}, "il faut `when`"),
        ({"name": "a", "when": {"time": "8h"}}, "exactement une action"),
        ({"name": "a", "when": {"time": "n'importe"}, "say": "x"}, "heure illisible"),
        ({"name": "a", "when": {"event": "tremblement"}, "say": "x"}, "événement inconnu"),
        ({"name": "a", "when": {"event": "app_opened"}, "say": "x"}, "demande `app`"),
        ({"name": "a", "when": {"time": "08:00", "days": ["lundo"]}, "say": "x"}, "jour inconnu"),
        ({"name": "a", "when": {"time": "08:00"}, "do": "fais le café"}, "je ne comprends pas"),
    ):
        with pytest.raises(ValueError, match=message):
            from_config(raw, 1)


def test_voice_phrases_reach_the_automation_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(builtin, "AUTOMATIONS", Automations(tmp_path / "automations.json"))
    command = commands.parse("tous les jours à 8 heures, rappelle-moi de prendre mes médicaments")
    assert command.tool == "automations" and command.arguments["action"] == "create"
    assert builtin.automations_tool(**command.arguments).startswith("C'est programmé : tous les jours à 8 heures")
    assert commands.parse("quelles sont mes automatisations").arguments == {"action": "list"}
    assert commands.parse("supprime le rappel des médicaments").arguments == {"action": "remove", "text": "médicaments"}
    assert commands.parse("supprime toutes mes automatisations").arguments == {"action": "clear"}
    assert commands.parse("rappelle-moi dans 10 minutes").tool == "set_timer"       # un minuteur reste un minuteur


def test_the_voice_loop_says_reminders_and_runs_commands():
    from jarvis.pipeline import Assistant

    said, ran = [], []
    assistant = SimpleNamespace(announce=said.append, _context=lambda: "",
                                parts=SimpleNamespace(executor=SimpleNamespace(
                                    run=lambda tool, args: ran.append((tool, args)) or "Volume à 40 pour cent.",
                                    speaks=lambda tool: True)))
    Assistant._run_automation(assistant, Automation("r", "time", say="Rappel : médicaments."))
    assert said == ["Rappel : médicaments."]
    Assistant._run_automation(assistant, Automation("v", "event", do="mets le volume à 40"))
    deadline = datetime.now() + timedelta(seconds=5)
    while not ran and datetime.now() < deadline:
        pass
    assert ran == [("set_volume", {"level": 40})]
