import ctypes
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import jarvis.tools.builtin as builtin
from jarvis import commands
from jarvis.config import ToolsConfig
from jarvis.desktop import DesktopController, DesktopUnavailable, Element, Snapshot, mac, parse_keys, windows
from jarvis.system.foreground import Foreground, ForegroundTracker
from jarvis.tools import ToolExecutor

PATTERNS = {"text": 1, "value": 2, "invoke": 3, "toggle": 4, "selection_item": 5, "expand_collapse": 6, "legacy": 7}


class FakeControl:
    """Contrôle UI Automation factice : mêmes attributs et méthodes que ceux du paquet uiautomation."""

    def __init__(self, kind, name="", children=(), offscreen=False, enabled=True, patterns=None, password=False):
        self.ControlTypeName, self.Name = kind, name
        self.IsOffscreen, self.IsEnabled, self.IsPassword = offscreen, enabled, password
        self.children, self.patterns, self.clicked = list(children), patterns or {}, None

    def GetChildren(self):
        return self.children

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)

    def Click(self, simulateMove=True):
        self.clicked = simulateMove


def text_pattern(text):
    return SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda limit: text if limit < 0 else text[:limit]))


def test_windows_tree_gives_visible_text_and_clickable_elements():
    invoked = []
    save = FakeControl("ButtonControl", "Enregistrer", patterns={3: SimpleNamespace(Invoke=lambda: invoked.append(1))})
    tree = FakeControl("WindowControl", "Rapport.docx - Word", [
        FakeControl("ToolBarControl", "", [save, FakeControl("ButtonControl", "Grisé", enabled=False)]),
        FakeControl("DocumentControl", "", patterns={1: text_pattern("Bonjour Sacha,\nvoici le rapport.")}),
        FakeControl("TextControl", "Page 1 sur 3"),
        FakeControl("ButtonControl", "Caché", offscreen=True),
        FakeControl("EditControl", "Mot de passe", patterns={2: SimpleNamespace(Value="secret")}, password=True),
    ])
    texts, elements = windows.collect(tree, PATTERNS, 1000, 10)
    assert texts == ["Bonjour Sacha,\nvoici le rapport.", "Page 1 sur 3"]
    assert [(e.role, e.name) for e in elements] == [("bouton", "Enregistrer"), ("champ", "Mot de passe")]

    windows.press(save, PATTERNS)
    assert invoked == [1]
    plain = FakeControl("ButtonControl", "Sans motif")
    windows.press(plain, PATTERNS)
    assert plain.clicked is False                  # clic de secours, sans déplacer la souris


def test_windows_keys():
    assert windows.typed_text("a{b}\nc") == "a{{}b{}}{Enter}c"
    assert windows.keys_text(["ctrl", "shift", "s"]) == "{Ctrl}{Shift}s"
    assert windows.keys_text(["alt", "f4"]) == "{Alt}{F4}"


def test_mac_tree_and_shortcuts():
    tree = {"AXRole": "AXWindow", "AXTitle": "Notes", "AXChildren": [
        {"AXRole": "AXButton", "AXDescription": "Nouvelle note", "AXEnabled": True},
        {"AXRole": "AXButton", "AXTitle": "Désactivé", "AXEnabled": False},
        {"AXRole": "AXStaticText", "AXValue": "Liste de courses"},
        {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField", "AXValue": "motdepasse"},
        {"AXRole": "AXGroup", "AXChildren": [{"AXRole": "AXTextArea", "AXValue": "Acheter du pain"}]},
    ]}
    texts, elements = mac.collect(tree, lambda element, name: element.get(name), 1000, 10)
    assert texts == ["Liste de courses", "Acheter du pain"]
    assert [(e.role, e.name) for e in elements] == [("bouton", "Nouvelle note")]
    assert mac.shortcut_script(["ctrl", "shift", "s"]) == \
        'tell application "System Events" to keystroke "s" using {command down, shift down}'
    assert mac.shortcut_script(["alt", "f4"]) == 'tell application "System Events" to key code 118 using {option down}'


@pytest.mark.parametrize(("spoken", "keys"), [
    ("ctrl+s", ["ctrl", "s"]), ("contrôle shift T", ["ctrl", "shift", "t"]), ("alt f4", ["alt", "f4"]),
    ("Maj Tab", ["shift", "tab"]), ("bonjour", None), ("ctrl", None), ("ctrl s v", None),
])
def test_spoken_shortcuts(spoken, keys):
    assert parse_keys(spoken) == keys


class FakeBackend:
    def __init__(self, snapshot):
        self.snap, self.snapshots, self.pressed, self.typed, self.keys = snapshot, 0, [], [], []

    def snapshot(self, window, max_chars, max_items):
        self.snapshots += 1
        return self.snap

    def press(self, window, element):
        self.pressed.append(element.name)

    def type_text(self, window, text, submit):
        self.typed.append((text, submit))

    def shortcut(self, window, keys):
        self.keys.append(keys)


def tracker_on(app="Microsoft Word", title="Rapport.docx - Word"):
    tracker = ForegroundTracker(probe_fn=lambda: Foreground(app, title, 4242, time.time(), 99))
    tracker.poll()
    return tracker


def test_controller_reads_then_clicks_safely():
    snapshot = Snapshot("Microsoft Word", "Rapport.docx - Word", ["Voulez-vous supprimer ce fichier ?"],
                        [Element("bouton", "Enregistrer"), Element("bouton", "Supprimer le fichier"),
                         Element("bouton", "Oui")])
    backend = FakeBackend(snapshot)
    desktop = DesktopController(tracker_on(), backend)
    page = desktop.read()
    assert page.startswith("Application : Microsoft Word — Rapport.docx - Word")
    assert "1 : bouton « Enregistrer »" in page

    assert desktop.press(index=1, forbid=builtin.RISKY) == "Je clique sur Enregistrer."
    assert backend.snapshots == 1                  # le numéro vient de la lecture précédente
    assert "action sensible" in desktop.press(text="supprimer le fichier", forbid=builtin.RISKY)
    assert "confirmer une action sensible" in desktop.press(text="oui", forbid=builtin.RISKY)
    assert desktop.press(text="introuvable", forbid=builtin.RISKY).startswith("Je ne trouve pas")
    assert backend.pressed == ["Enregistrer"]
    assert desktop.shortcut("contrôle s") == "J'ai appuyé sur ctrl s dans Microsoft Word."
    assert backend.keys == [["ctrl", "s"]]
    assert desktop.type("Bonjour", submit=True) == "C'est tapé dans Microsoft Word."


def test_controller_needs_a_known_window():
    desktop = DesktopController(ForegroundTracker(probe_fn=lambda: None), FakeBackend(Snapshot("", "")))
    with pytest.raises(DesktopUnavailable):
        desktop.read()


def test_application_tools_are_offered_outside_the_browser(monkeypatch):
    executor = ToolExecutor(ToolsConfig())

    def names(context):
        return {schema["function"]["name"] for schema in executor.schemas(context)}

    assert {"app_read", "app_press", "app_type", "app_shortcut"} <= names("app")
    assert "app_read" in names("code")
    assert "app_read" not in names("browser") and "app_read" not in names("")
    assert not executor.speaks("app_read")
    monkeypatch.setattr(builtin, "DESKTOP", DesktopController(ForegroundTracker(probe_fn=lambda: None)))
    assert builtin.app_read().startswith("Je ne sais pas encore quelle application")


def test_other_applications_have_their_own_context():
    tracker = ForegroundTracker(probe_fn=lambda: Foreground("Microsoft Word", "Rapport.docx - Word", 5, 0.0))
    tracker.poll()
    assert tracker.context() == "app"


@pytest.mark.parametrize(("text", "tool", "arguments"), [
    ("Clique sur Enregistrer", "app_press", {"text": "enregistrer"}),
    ("Clique sur le bouton Nouveau message", "app_press", {"text": "nouveau message"}),
    ("Appuie sur contrôle S", "app_shortcut", {"keys": "ctrl+s"}),
    ("Fais contrôle shift T", "app_shortcut", {"keys": "ctrl+shift+t"}),
    ("Enregistre le fichier", "app_shortcut", {"keys": "ctrl+s"}),
    ("Monte le son", "set_volume", {"change": 10}),
])
def test_commands_in_an_application(text, tool, arguments):
    assert commands.parse(text, "app") == commands.Command(tool, arguments)


@pytest.mark.skipif(sys.platform != "win32", reason="UI Automation : Windows seulement")
def test_windows_backend_reads_and_types_in_notepad():
    import uiautomation as auto

    backend = windows.WindowsBackend()
    process = subprocess.Popen(["notepad.exe"])
    try:
        window = None
        deadline = time.time() + 20
        while window is None and time.time() < deadline:
            candidate = auto.WindowControl(searchDepth=1, ClassName="Notepad")
            if candidate.Exists(1, 0.2):
                window = candidate
        if window is None:
            pytest.skip("Bloc-notes introuvable sur cette machine")
        target = Foreground("Bloc-notes", window.Name, process.pid, time.time(), window.NativeWindowHandle)
        snapshot = backend.snapshot(target, 2000, 40)
        assert snapshot.title
        assert snapshot.elements                   # menus Fichier, Édition…
        backend._call(backend._activate, target)
        if ctypes.windll.user32.GetForegroundWindow() != target.handle:
            pytest.skip("Windows a refusé de mettre le Bloc-notes au premier plan")
        backend.type_text(target, "Bonjour {Jarvis}", submit=False)
        time.sleep(0.5)
        assert "Bonjour {Jarvis}" in "\n".join(backend.snapshot(target, 2000, 40).texts)
    finally:
        process.kill()
