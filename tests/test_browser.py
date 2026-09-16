import asyncio
import json
import os
import socket
import threading
import time

import pytest
from websockets.asyncio.client import connect

import jarvis.tools.builtin as builtin  # noqa: F401 - enregistre les outils
from jarvis import commands
from jarvis.browser import install, safari
from jarvis.browser.bridge import BrowserUnavailable, start_bridge
from jarvis.config import ToolsConfig
from jarvis.system.foreground import Foreground, ForegroundTracker
from jarvis.tools import REGISTRY, ToolExecutor


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def bridge():
    port = free_port()
    started = start_bridge("jeton-secret", port)
    yield started, port
    started.server.stop()


class FakeExtension(threading.Thread):
    """Imite l'extension : se relie au pont et répond à chaque demande."""

    def __init__(self, port, token="jeton-secret", origin="chrome-extension://abcdef"):
        super().__init__(daemon=True)
        self.port, self.token, self.origin = port, token, origin
        self.stop = threading.Event()
        self.errors = []
        self.requests = []

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        try:
            async with connect(f"ws://127.0.0.1:{self.port}/bridge", origin=self.origin) as ws:
                await ws.send(json.dumps({"type": "hello", "token": self.token, "browser": "Chrome", "focused": True}))
                while not self.stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.2)
                    except TimeoutError:
                        continue
                    message = json.loads(raw)
                    self.requests.append(message)
                    await ws.send(json.dumps({"id": message["id"], "ok": True,
                                              "result": {"found": True, "volume": 42}}))
        except Exception as exc:  # noqa: BLE001 - relevé par les tests
            self.errors.append(exc)


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_extension_answers_jarvis_requests(bridge):
    server, port = bridge
    extension = FakeExtension(port)
    extension.start()
    try:
        assert wait_for(lambda: server.browsers() == ["Chrome"])
        result = server.call("media", {"action": "volume_down", "value": 10})
        assert result == {"found": True, "volume": 42}
        assert extension.requests[0]["action"] == "media"
        assert extension.requests[0]["params"] == {"action": "volume_down", "value": 10}
    finally:
        extension.stop.set()
        extension.join(2)
    assert wait_for(lambda: server.browsers() == [])


def test_a_call_during_a_reconnection_waits_for_the_browser(bridge):
    server, port = bridge
    extension = FakeExtension(port)
    threading.Timer(0.4, extension.start).start()             # le navigateur se relie 400 ms après l'appel
    try:
        assert server.call("media", {"action": "pause"})["found"] is True
    finally:
        extension.stop.set()
        extension.join(2)
    started = time.monotonic()
    with pytest.raises(BrowserUnavailable):
        server.call("media", {"action": "pause"}, grace=0.3)
    assert time.monotonic() - started < 1.5


def test_wrong_token_is_rejected(bridge):
    server, port = bridge
    extension = FakeExtension(port, token="mauvais")
    extension.start()
    extension.join(3)
    assert extension.errors                                  # connexion fermée par Jarvis
    assert server.browsers() == []
    with pytest.raises(BrowserUnavailable):
        server.call("media", {"action": "pause"})


def test_web_pages_cannot_connect(bridge):
    _, port = bridge
    extension = FakeExtension(port, origin="https://site-malveillant.example")
    extension.start()
    extension.join(3)
    assert extension.errors


def test_extension_folders_carry_port_and_token(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    folders = install.build(47831, tmp_path / "extension")
    config = (folders["chromium"] / "config.js").read_text(encoding="utf-8")
    assert '"port": 47831' in config
    assert install.token() in config
    chromium = json.loads((folders["chromium"] / "manifest.json").read_text(encoding="utf-8"))
    firefox = json.loads((folders["firefox"] / "manifest.json").read_text(encoding="utf-8"))
    assert chromium["background"] == {"service_worker": "background.js"}
    assert firefox["background"]["scripts"][0] == "config.js"
    assert (folders["firefox"] / "actions.js").exists()
    if os.name == "posix":
        assert oct((tmp_path / "bridge-token").stat().st_mode)[-3:] == "600"


def test_safari_script_embeds_the_shared_actions():
    source = safari.script("media", {"action": "pause", "value": None})
    assert "const JarvisActions =" in source
    assert 'JarvisActions["media"]({"action": "pause", "value": null})' in source


def test_foreground_tracker_ignores_jarvis_and_knows_the_context():
    probes = iter([
        Foreground("Google Chrome", "YouTube", 111, 1.0),
        Foreground("Jarvis", "", os.getpid(), 2.0),           # la fenêtre de Jarvis elle-même
        Foreground("Visual Studio Code", "pipeline.py — jarvis", 222, 3.0),
    ])
    tracker = ForegroundTracker(probe_fn=lambda: next(probes))
    tracker.poll()
    assert tracker.context() == "browser"
    tracker.poll()
    assert tracker.context() == "browser"
    tracker.poll()
    assert tracker.context() == "code"
    assert tracker.last_browser().app == "Google Chrome"


def test_browser_tools_are_offered_only_in_the_browser():
    executor = ToolExecutor(ToolsConfig())
    names = lambda context: {s["function"]["name"] for s in executor.schemas(context)}  # noqa: E731
    assert "browser_media" in names("browser")
    assert "browser_media" not in names("")
    assert "open_app" in names("browser")
    assert not executor.speaks("browser_read")
    assert executor.speaks("browser_media")


class FakeBrowserLink:
    """Navigateur relié : retient les appels et renvoie ce qu'on lui a préparé."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def in_browser(self):
        return True

    def call(self, action, params=None):
        self.calls.append((action, params or {}))
        return self.answers.get(action, {})


def test_writing_into_a_page_field_by_name_or_number(monkeypatch):
    """Le manque signalé par Jarvis : il lisait les liens et les boutons, pas les champs, et ne pouvait
    écrire que dans celui déjà sélectionné."""
    link = FakeBrowserLink({"type": {"typed": True, "field": "Description"}})
    monkeypatch.setattr(builtin, "BROWSER", link)
    assert builtin.browser_type("Ma description", field="Description") == "C'est écrit dans le champ Description."
    assert link.calls[-1] == ("type", {"text": "Ma description", "field": "Description", "index": None,
                                       "replace": False, "submit": False})
    builtin.browser_type("Mon titre", index=2, submit=True)
    assert link.calls[-1][1]["index"] == 2 and link.calls[-1][1]["submit"] is True

    link.answers["type"] = {"typed": True, "field": ""}
    assert builtin.browser_type("bonjour") == "Voilà, c'est écrit dans la page."
    link.answers["type"] = {"typed": False, "error": "Je ne trouve pas de champ Titre sur cette page."}
    assert builtin.browser_type("x", field="Titre") == "Je ne trouve pas de champ Titre sur cette page."


def test_reading_a_page_lists_fields_and_what_they_contain(monkeypatch):
    link = FakeBrowserLink({"page": {"title": "Nouvelle vidéo", "url": "https://exemple.test/upload", "text": "…",
                                     "items": [{"index": 1, "text": "Publier", "kind": "bouton"},
                                               {"index": 2, "text": "Description", "kind": "champ",
                                                "value": "déjà écrit"},
                                               {"index": 3, "text": "Titre", "kind": "champ", "value": ""}]}})
    monkeypatch.setattr(builtin, "BROWSER", link)
    read = builtin.browser_read()
    assert "2. [champ] Description (contient : déjà écrit)" in read
    assert "3. [champ] Titre" in read and "Titre (contient" not in read
    assert "champs de saisie" in REGISTRY["browser_read"].description


def test_saying_write_this_in_that_field_needs_no_llm():
    for phrase in ("écris ma description dans le champ Description",
                   "tape ma description dans la zone Description",
                   "mets ma description dans Description"):
        command = commands.parse(phrase, "browser")
        assert command.tool == "browser_type", phrase
        assert command.arguments == {"text": "ma description", "field": "description"}, phrase
    assert commands.parse("mets la vidéo en pause", "browser").tool == "browser_media"
    assert REGISTRY["browser_type"].question({"text": "salut", "field": "Description"}) == \
        "J'écris « salut » dans le champ Description ?"
