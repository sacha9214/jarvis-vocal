import asyncio
import json
import os
import socket
import threading
import time

import pytest
from websockets.asyncio.client import connect

import jarvis.tools.builtin  # noqa: F401 - enregistre les outils
from jarvis.browser import install, safari
from jarvis.browser.bridge import BrowserUnavailable, start_bridge
from jarvis.config import ToolsConfig
from jarvis.system.foreground import Foreground, ForegroundTracker
from jarvis.tools import ToolExecutor


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
        assert oct((tmp_path / "browser-token").stat().st_mode)[-3:] == "600"


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
