"""L'arrière-plan de l'extension, exécuté pour de vrai (sous Node, avec des API Chrome simulées) : ce que ni
les tests du pont ni la page de test ne couvrent. Le transport WebSocket est testé dans test_browser.py."""
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarvis.browser import install

HARNESS = Path(__file__).parent / "fake_browser.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node absent")


class FakeBrowser:
    """Parle à background.js comme le ferait Jarvis par le pont."""

    def __init__(self, folder: Path, calls: Path, env: dict | None = None):
        self.calls = calls
        self.ids = itertools.count(1)
        # encoding explicite : Node écrit en UTF-8, Windows lirait sinon en cp1252 (« Vidéo » abîmé).
        self.process = subprocess.Popen([shutil.which("node"), str(HARNESS), str(folder), str(calls)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        env={**os.environ, **(env or {})})
        self.hello = self._read()

    def _read(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"L'extension s'est arrêtée : {self.process.stderr.read()}")
        return json.loads(line)

    def call(self, action: str, params: dict | None = None) -> dict:
        request = {"id": next(self.ids), "action": action, "params": params or {}}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        while True:
            message = self._read()
            if message.get("id") != request["id"]:
                continue                                   # ping ou annonce de focus
            if not message.get("ok"):
                raise RuntimeError(message.get("error") or "refus sans explication")
            return message.get("result") or {}

    def recorded(self, name: str) -> list[dict]:
        return [call for call in json.loads(self.calls.read_text(encoding="utf-8")) if call["name"] == name]

    def close(self) -> None:
        self.process.kill()


@pytest.fixture
def extension(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    folders = install.build(47831, tmp_path / "extension")
    browser = FakeBrowser(folders["chromium"], tmp_path / "calls.json")
    yield browser
    browser.close()


def test_the_extension_announces_itself_with_its_token(extension, tmp_path):
    assert extension.hello == {"type": "hello", "token": install.token(), "browser": "Chrome",
                               "focused": True, "version": "1.0"}


def test_media_actions_reach_every_frame_of_the_page(extension):
    assert extension.call("media", {"action": "volume_down", "value": 10}) == {
        "found": True, "volume": 42, "clicked": True, "title": "Vidéo test"}
    injection = extension.recorded("executeScript")[-1]
    assert injection["world"] == "MAIN"           # le lecteur YouTube n'est joignable que dans ce monde
    assert injection["allFrames"] is True         # une vidéo vit souvent dans une iframe
    assert injection["params"] == {"action": "volume_down", "value": 10}
    assert injection["action"] == "media"


def test_tabs_are_listed_switched_and_navigated(extension):
    assert extension.call("status") == {"title": "Vidéo test", "url": "https://exemple.test/watch?v=1",
                                        "browser": "Chrome"}
    assert [tab["title"] for tab in extension.call("tabs", {"action": "list"})["tabs"]] == \
        ["Vidéo test", "Deuxième onglet"]
    assert extension.call("tabs", {"action": "switch", "index": 2})["title"] == "Deuxième onglet"
    assert extension.call("navigate", {"direction": "back"}) == {"done": True}
    assert extension.recorded("goBack")[-1]["id"] == 1
    assert extension.call("tabs", {"action": "new", "url": "https://exemple.test"})["title"] == "Nouvel onglet"


def test_a_protected_page_is_explained_and_the_link_survives(extension):
    with pytest.raises(RuntimeError, match="page est protégée"):
        extension.call("click", {"text": "interdit"})
    assert extension.call("status")["browser"] == "Chrome"


def test_an_unknown_action_is_refused(extension):
    with pytest.raises(RuntimeError, match="Action inconnue"):
        extension.call("danse")


@pytest.mark.skipif(sys.platform == "win32", reason="droits POSIX")
def test_the_token_file_is_readable_only_by_you(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    install.build(47831, tmp_path / "extension")
    assert oct((tmp_path / "bridge-token").stat().st_mode)[-3:] == "600"


def test_without_site_access_firefox_is_told_to_click_the_icon(tmp_path, monkeypatch):
    """Firefox n'accorde pas l'accès aux sites à l'installation : l'injection ne renvoie rien, sans erreur."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    folders = install.build(47831, tmp_path / "extension")
    browser = FakeBrowser(folders["chromium"], tmp_path / "calls.json", env={"FAKE_NO_PERMISSION": "1"})
    try:
        with pytest.raises(RuntimeError, match="clique sur l'icône Jarvis"):
            browser.call("page", {"max_chars": 100})
        time.sleep(0.1)
        assert {"name": "setBadgeText", "text": "!"} in browser.recorded("setBadgeText")
    finally:
        browser.close()


def test_the_firefox_manifest_keeps_the_bridge_in_clear_text(tmp_path, monkeypatch):
    """Mesuré : la CSP MV3 par défaut de Firefox (upgrade-insecure-requests) transformait ws:// en wss://,
    et le pont recevait un handshake TLS."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    folders = install.build(47831, tmp_path / "extension")
    manifest = json.loads((folders["firefox"] / "manifest.json").read_text(encoding="utf-8"))
    policy = manifest["content_security_policy"]["extension_pages"]
    assert "upgrade-insecure-requests" not in policy and "script-src 'self'" in policy
