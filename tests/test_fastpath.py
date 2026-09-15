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


@pytest.mark.parametrize(("text", "target"), [
    ("Passe en local.", "local"),
    ("Bascule en mode hors ligne", "local"),
    ("Jarvis, passe sur Claude.", "claude"),
    ("Utilise Claude s'il te plaît", "claude"),
    ("Passe sur Clode", "claude"),
    ("Pas sur Claude.", "claude"),                  # transcriptions réelles de Whisper
    ("Passons local.", "local"),
    ("Passe au modèle local", "local"),
    ("Explique-moi Claude Monet", None),
    ("Est-ce que Claude est meilleur que toi ?", None),
    ("Mets de la musique", None),
    ("Il ne faut pas passer par le local technique ce soir", None),
    ("Quel modèle tu utilises ?", None),
])
def test_engine_switch(text, target):
    assert fastpath.switch_target(text) == target


def test_asks_engine():
    assert fastpath.asks_engine("Quel modèle tu utilises ?")
    assert not fastpath.asks_engine("Quel modèle de voiture choisir ?")


@pytest.mark.parametrize(("text", "expected"), [("Stop !", True), ("Tais-toi.", True), ("Arrête la musique", False)])
def test_stop(text, expected):
    assert fastpath.is_stop(text) is expected
