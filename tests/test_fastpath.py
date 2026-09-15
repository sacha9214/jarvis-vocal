from datetime import datetime

import pytest

from jarvis import fastpath

NOW = datetime(2026, 9, 15, 18, 5)


@pytest.mark.parametrize("text", ["Quelle heure est-il ?", "Jarvis, il est quelle heure ?",
                                  "Dis-moi l'heure s'il te plaît."])
def test_time(text):
    assert fastpath.reply(text, NOW) == "Il est 18 heures 5."


@pytest.mark.parametrize("text", ["On est quel jour ?", "Quelle est la date aujourd'hui ?"])
def test_date(text):
    assert fastpath.reply(text, NOW) == "Nous sommes le mardi 15 septembre."


@pytest.mark.parametrize("text", ["Quel jour tombe Noël ?", "Quelle heure est-il à Tokyo ?",
                                  "Rappelle-moi l'heure du rendez-vous"])
def test_real_questions_go_to_the_llm(text):
    assert fastpath.reply(text, NOW) is None


def test_spoken_forms():
    assert fastpath.say_time(datetime(2026, 1, 1, 0, 0)) == "Il est minuit."
    assert fastpath.say_time(datetime(2026, 1, 1, 12, 30)) == "Il est midi 30."
    assert fastpath.say_time(datetime(2026, 1, 1, 1, 0)) == "Il est 1 heure."
    assert fastpath.say_date(datetime(2026, 1, 1)) == "Nous sommes le jeudi 1er janvier."


@pytest.mark.parametrize(("text", "expected"), [("Stop !", True), ("Tais-toi.", True), ("Arrête la musique", False)])
def test_stop(text, expected):
    assert fastpath.is_stop(text) is expected
