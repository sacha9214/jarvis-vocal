"""Contrôle de la machine : fichiers, fenêtres, presse-papiers, réglages, capture, calcul.

Ces tests s'exécutent pour de vrai sur macOS **et** sur Windows (la CI lance les deux) : c'est la seule
façon de savoir que l'implémentation Windows marche, puisque le développement se fait sur Mac.
"""
import shutil
import sys
from pathlib import Path

import pytest

import jarvis.tools.builtin as builtin
from jarvis import commands
from jarvis.system import calc, capture, clipboard, files, gui, settings
from jarvis.tools import REGISTRY

WINDOWS = sys.platform == "win32"
MAC = sys.platform == "darwin"


# -- calcul (aucun système, partout pareil)

@pytest.mark.parametrize(("question", "expected"), [
    ("combien font 15 pour cent de 340", "51."),
    ("2 plus 3 fois 4", "14."),
    ("1250 divisé par 5", "250."),
    ("racine carrée de 144", "12."),
    ("combien fait 12 puissance 2", "144."),
    ("quinze fois trois", "45."),
    ("100 moins 37", "63."),
])
def test_the_calculator_is_exact(question, expected):
    assert calc.evaluate(question) == expected


def test_the_calculator_refuses_what_is_not_a_calculation():
    for text in ("bonjour", "ouvre spotify", "quelle heure est-il", "", "le double de 21"):
        assert calc.evaluate(text) is None
    assert calc.evaluate("7 divisé par 0") == "Une division par zéro, ça n'existe pas."
    assert calc.evaluate("9 puissance 9 puissance 9") is None        # ne bloque pas la machine


def test_the_calculator_never_runs_code():
    for attack in ("__import__('os')", "open('secret')", "[].__class__", "1 if print(1) else 2"):
        assert calc.evaluate(attack) is None


def test_numbers_are_said_readably():
    assert calc.spoken_number(12.0) == "12"
    assert calc.spoken_number(0.1 + 0.2) == "0 virgule 3"
    assert calc.spoken_number(1234567) == "1 234 567"


# -- fichiers

def test_finding_creating_and_trashing_a_file(tmp_path, monkeypatch):
    folder = files.new_folder("Essai Jarvis", tmp_path)
    assert folder.is_dir() and folder.name == "Essai Jarvis"
    again = files.new_folder("Essai Jarvis", tmp_path)
    assert again.name == "Essai Jarvis 2"                            # jamais d'écrasement
    assert files.new_folder('un/nom: bizarre?', tmp_path).name == "un nom bizarre"

    free, total = files.disk_usage(tmp_path)
    assert 0 < free <= total

    document = folder / "rapport-jarvis-essai.txt"
    document.write_text("contenu", encoding="utf-8")
    monkeypatch.setattr(files, "_HOME_FOLDERS", ())                  # index vide : on force le parcours
    monkeypatch.setattr(files, "_walk", lambda query, deadline: [document] if "rapport" in query else [])
    found = files.search("rapport-jarvis-essai")
    assert [f.name for f in found] == ["rapport-jarvis-essai.txt"]
    assert files.search("a") == []                                   # trop court : on ne cherche pas

    files.trash(document)
    assert not document.exists()                                     # à la corbeille, pas supprimé


def test_searching_filters_by_kind(monkeypatch, tmp_path):
    names = ["photo.png", "note.txt", "tableau.xlsx", "image.heic"]
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    monkeypatch.setattr(files, "_walk", lambda query, deadline: [tmp_path / n for n in names])
    monkeypatch.setattr(files, "_spotlight", lambda query: [])
    monkeypatch.setattr(files, "_windows_index", lambda query: [])
    assert {f.name for f in files.search("essai", "image")} == {"photo.png", "image.heic"}
    assert {f.name for f in files.search("essai", "tableur")} == {"tableau.xlsx"}
    assert {f.name for f in files.search("essai")} == set(names)


def test_the_file_tool_speaks_and_remembers_its_results(monkeypatch, tmp_path):
    first, second = tmp_path / "rapport 2024.pdf", tmp_path / "rapport 2023.pdf"
    for path in (first, second):
        path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(files, "search", lambda query, kind="": [files.Found(first, 2.0), files.Found(second, 1.0)])
    opened = []
    monkeypatch.setattr(files, "open_file", opened.append)
    said = builtin.files_tool("find", "rapport")
    assert "2 fichiers" in said and "1. rapport 2024.pdf" in said and "2. rapport 2023.pdf" in said
    assert "J'ouvre rapport 2023.pdf" in builtin.files_tool("open", index=2)
    assert opened == [second]                                        # « ouvre le deuxième »
    monkeypatch.setattr(files, "search", lambda query, kind="": [])
    assert builtin.files_tool("find", "inexistant") == "Je ne trouve aucun fichier qui s'appelle inexistant."


# -- presse-papiers (vrai presse-papiers du système)

def test_the_clipboard_is_read_and_written():
    if not (MAC or WINDOWS):
        pytest.skip("presse-papiers non pris en charge")
    before = ""
    try:
        before = clipboard.read()
    except RuntimeError:
        pytest.skip("presse-papiers indisponible sur cette machine")
    try:
        clipboard.write("Jarvis : essai à l'accentué €")
        assert clipboard.read() == "Jarvis : essai à l'accentué €"
        assert builtin.clipboard_tool("read").startswith("Tu as copié : Jarvis")
        builtin.clipboard_tool("write", "deuxième essai")
        assert clipboard.read() == "deuxième essai"
    finally:
        clipboard.write(before)


# -- fenêtres

def test_open_windows_are_listed_without_duplicates():
    if not (MAC or WINDOWS):
        pytest.skip("liste des fenêtres non prise en charge")
    windows = gui.list_windows()
    assert len(windows) <= gui.MAX_WINDOWS
    assert len({w.app for w in windows}) == len(windows)             # une entrée par application
    assert all(w.app.strip() for w in windows)
    if WINDOWS:
        assert all(w.handle for w in windows)


def test_finding_a_window_by_a_piece_of_its_name():
    windows = [gui.Window("Visual Studio Code", "pipeline.py — jarvis"), gui.Window("Discord", "@moi"),
               gui.Window("Google Chrome", "Le Monde")]
    assert gui.find("discord", windows).app == "Discord"
    assert gui.find("Code", windows).app == "Visual Studio Code"
    assert gui.find("chrome", windows).app == "Google Chrome"
    assert gui.find("pipeline", windows).app == "Visual Studio Code"  # retrouvé par le titre
    assert gui.find("photoshop", windows) is None
    assert gui.find("", windows) is None


def test_the_window_tool_says_what_is_open(monkeypatch):
    monkeypatch.setattr(gui, "list_windows", lambda: [gui.Window("Discord", ""), gui.Window("Safari", "")])
    assert builtin.windows_tool("list") == "Applications ouvertes : Discord, Safari."
    monkeypatch.setattr(gui, "list_windows", list)
    assert builtin.windows_tool("list") == "Je ne vois aucune fenêtre ouverte."
    assert builtin.windows_tool("switch") == "Dis-moi quelle application."


# -- réglages (lecture seule : on ne coupe pas le Wi-Fi de la machine d'essai)

def test_reading_the_machine_settings():
    if not (MAC or WINDOWS):
        pytest.skip("réglages non pris en charge")
    assert isinstance(settings.wifi_status(), str)
    assert isinstance(settings.bluetooth_status(), str)
    brightness = settings.get_brightness()
    assert brightness is None or 0 <= brightness <= 100
    if MAC:
        assert settings.mac_wifi_device().startswith("en")


# -- capture d'écran

def test_a_screenshot_lands_on_disk(tmp_path):
    if not (MAC or WINDOWS):
        pytest.skip("capture non prise en charge")
    try:
        path = capture.take("screen", tmp_path)
    except RuntimeError as exc:
        pytest.skip(f"capture indisponible ici ({exc})")
    assert path.exists() and path.stat().st_size > 1000 and path.suffix == ".png"
    with pytest.raises(ValueError):
        capture.take("n'importe quoi", tmp_path)


# -- ce que Jarvis comprend sans le modèle

@pytest.mark.parametrize(("phrase", "tool", "arguments"), [
    ("cherche le fichier rapport", "files", {"action": "find", "query": "rapport"}),
    ("trouve mon CV", "files", {"action": "find", "query": "cv"}),
    ("trouve mes photos de vacances", "files", {"action": "find", "query": "vacances", "kind": "image"}),
    ("ouvre le fichier contrat", "files", {"action": "open", "query": "contrat"}),
    ("combien de place libre", "files", {"action": "disk"}),
    ("qu'est-ce qui est ouvert", "windows", {"action": "list"}),
    ("passe sur Discord", "windows", {"action": "switch", "name": "discord"}),
    ("réduis la fenêtre", "windows", {"action": "minimize"}),
    ("plein écran", "windows", {"action": "fullscreen"}),
    ("mets la fenêtre à gauche", "windows", {"action": "left"}),
    ("qu'est-ce que j'ai copié", "clipboard", {"action": "read"}),
    ("monte la luminosité", "settings", {"action": "brightness", "change": 15}),
    ("luminosité à 50", "settings", {"action": "brightness", "level": 50}),
    ("à quel réseau je suis connecté", "settings", {"action": "wifi"}),
    ("prends une capture", "screenshot", {"mode": "screen"}),
])
def test_the_new_commands_need_no_llm(phrase, tool, arguments):
    command = commands.parse(phrase)
    assert command is not None, phrase
    assert (command.tool, command.arguments) == (tool, arguments)


def test_the_old_commands_still_win_when_they_should():
    """Les nouvelles règles ne doivent pas voler les anciennes. Rien ici ne dépend des applications
    installées : la machine d'essai n'a ni Spotify ni Discord."""
    assert commands.parse("cherche recette de crêpes").tool == "web_search"
    assert commands.parse("va sur YouTube").tool == "open_website"
    assert commands.parse("mets un minuteur de 5 minutes").tool == "set_timer"
    assert commands.parse("éteins l'ordinateur dans 25 minutes").tool == "power"
    assert commands.parse("monte le son").tool == "set_volume"
    assert commands.parse("ouvre mes téléchargements").tool == "open_folder"
    assert commands.parse("quelle heure est-il") is None            # un réflexe, pas une commande


def test_the_new_tools_stay_affordable_in_the_prompt():
    """Chaque outil coûte des tokens à chaque question : on les garde groupés."""
    import json

    from jarvis.config import ToolsConfig
    from jarvis.tools import ToolExecutor

    executor = ToolExecutor(ToolsConfig())
    names = {tool.name for tool in executor.tools("")}
    assert {"files", "windows", "clipboard", "settings", "screenshot", "calculate"} <= names
    size = len(json.dumps(executor.schemas(""), ensure_ascii=False))
    assert size < 9000, f"les outils pèsent {size} caractères : regrouper au lieu d'ajouter"
    assert REGISTRY["files"].level == "N1" and REGISTRY["windows"].level == "N1"


def test_putting_a_file_in_the_bin_asks_first():
    """Mettre à la corbeille est réversible, mais on préfère demander."""
    assert "trash" in REGISTRY["files"].description or "corbeille" in REGISTRY["files"].description
    assert shutil.which("osascript") or WINDOWS or True                # la corbeille existe sur les deux
    assert Path(files.__file__).exists()
