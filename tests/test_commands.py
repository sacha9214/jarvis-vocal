import pytest

from jarvis import commands
from jarvis.system import apps
from jarvis.system.apps import App

FAKE_APPS = tuple(App(name, f"/Applications/{name}.app", key) for name, key in [
    ("Spotify", "spotify"), ("Discord", "discord"), ("Calculator", "calculator"),
    ("Visual Studio Code", "visual studio code"), ("RobloxStudio", "robloxstudio"), ("Google Chrome", "google chrome"),
])


@pytest.fixture(autouse=True)
def fake_apps(monkeypatch):
    monkeypatch.setattr(apps, "installed_apps", lambda: FAKE_APPS)


@pytest.mark.parametrize(("text", "tool", "arguments"), [
    ("Ouvre Spotify", "open_app", {"name": "Spotify"}),
    ("Jarvis, tu peux lancer la calculatrice s'il te plaît ?", "open_app", {"name": "Calculator"}),
    ("Lance Visual Studio", "open_app", {"name": "Visual Studio Code"}),
    ("Ouvre Roblox Studio", "open_app", {"name": "RobloxStudio"}),
    ("Va sur YouTube", "open_website", {"site": "youtube"}),
    ("Ouvre Netflix", "open_website", {"site": "netflix"}),
    ("Ouvre mes téléchargements", "open_folder", {"name": "telechargements"}),
    ("Monte le son", "set_volume", {"change": 10}),
    ("Baisse un peu le volume", "set_volume", {"change": -10}),
    ("Mets le volume à trente", "set_volume", {"level": 30}),
    ("Volume à 75 %", "set_volume", {"level": 75}),
    ("Coupe le son", "set_volume", {"mute": True}),
    ("Mets pause", "media", {"action": "pause"}),
    ("Chanson suivante", "media", {"action": "next"}),
    ("Mets un minuteur de 5 minutes pour les pâtes", "set_timer", {"seconds": 300, "label": "les pâtes"}),
    ("Minuteur une heure et demie", "set_timer", {"seconds": 5400}),
    ("Cherche recette de crêpes", "web_search", {"query": "recette de crêpes"}),
    ("Ferme Discord", "close_app", {"name": "discord"}),
    ("Verrouille l'ordinateur", "lock_screen", {}),
    ("Éteins l'ordinateur", "power", {"action": "shutdown"}),
    ("Annule l'extinction", "cancel_power", {}),
    ("Qu'est-ce que tu vois ?", "describe_screen", {}),
    ("C'est quoi cette erreur ?", "describe_screen", {"question": "c est quoi cette erreur"}),
    ("Explique-moi ce code", "describe_screen", {"question": "explique moi ce code"}),
    ("Baisse le volume de la vidéo", "browser_media", {"action": "volume_down", "value": 10}),
    ("Mets le volume de la vidéo à vingt", "browser_media", {"action": "volume", "value": 20}),
    ("Mets la vidéo en pause", "browser_media", {"action": "pause"}),
    ("Avance de 30 secondes", "browser_media", {"action": "forward", "value": 30}),
    ("Lance la deuxième vidéo", "browser_open", {"video": 2}),
    ("Clique sur Paramètres", "browser_open", {"text": "paramètres"}),
    ("Cherche des tutos Python sur YouTube", "browser_search", {"query": "des tutos python", "site": "youtube"}),
    ("Mets une vidéo de chats", "browser_search", {"query": "chats", "site": "youtube"}),
    ("Descends", "browser_scroll", {"direction": "down"}),
    ("Page précédente", "browser_navigate", {"direction": "back"}),
    ("Ferme l'onglet", "browser_close_tab", {}),
])
def test_commands(text, tool, arguments):
    assert commands.parse(text) == commands.Command(tool, arguments)


@pytest.mark.parametrize("text", ["C'est quoi la capitale du Pérou ?", "Mets de la musique",
                                  "Ouvre une application de dessin", "Raconte-moi une blague", "Lance un minuteur"])
def test_everything_else_goes_to_the_llm(text):
    assert commands.parse(text) is None


@pytest.mark.parametrize(("text", "number"), [("30", 30), ("trente", 30), ("vingt et un", 21),
                                              ("soixante dix sept", 77), ("quatre vingt dix", 90), ("cent", 100),
                                              ("beaucoup", None)])
def test_parse_number(text, number):
    assert commands.parse_number(text) == number


@pytest.mark.parametrize(("text", "seconds"), [("5 minutes", 300), ("une demi heure", 1800),
                                               ("1 heure 30 minutes", 5400), ("quarante cinq secondes", 45),
                                               ("deux heures et demie", 9000), ("5 minutes pile", None)])
def test_parse_duration(text, seconds):
    assert commands.parse_duration(text) == seconds
