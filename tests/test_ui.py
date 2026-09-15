import json

import httpx
import pytest

from jarvis import config
from jarvis.config import Config
from jarvis.events import EventBus
from jarvis.server import start_server
from jarvis.tools import ToolExecutor
from jarvis.ui.controller import Controller

WRITE = {"X-Jarvis": "1"}


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOADED_PATH", tmp_path / "config.yaml")
    cfg = Config()
    bus = EventBus()
    server = start_server(ToolExecutor(cfg.tools), 0, Controller(cfg, bus))
    client = httpx.Client(base_url=server.url, timeout=5)
    yield server, client, cfg, bus, tmp_path
    client.close()
    server.stop()


def test_page_needs_the_session_token(ui):
    server, client, *_ = ui
    assert client.get("/").status_code == 403
    assert client.get("/api/state").status_code == 403
    page = client.get(f"/?t={server.token}")
    assert page.status_code == 200
    assert "J.A.R.V.I.S" in page.text
    assert client.get("/static/hud.js").status_code == 200          # cookie de session posé
    assert client.get("/static/../server.py").status_code == 404


def test_settings_are_validated_saved_and_applied(ui):
    server, client, cfg, _, tmp_path = ui
    client.get(f"/?t={server.token}")
    state = client.get("/api/state").json()
    assert any(f["key"] == "vad.end_silence_ms" for s in state["sections"] for f in s["fields"])
    assert {t["name"] for t in state["tools"]} >= {"open_app", "power"}

    updates = {"vad": {"end_silence_ms": 400}, "stt": {"model": "small"}}
    assert client.post("/api/config", json={"updates": updates}).status_code == 403     # sans X-Jarvis
    result = client.post("/api/config", json={"updates": updates}, headers=WRITE).json()
    assert cfg.vad.end_silence_ms == 400
    assert result["applied"] == ["vad.end_silence_ms"]
    assert result["restart"] == ["stt.model"]
    assert "end_silence_ms: 400" in (tmp_path / "config.yaml").read_text(encoding="utf-8")

    too_high = client.post("/api/config", json={"updates": {"vad": {"end_silence_ms": 99999}}}, headers=WRITE)
    assert too_high.status_code == 400
    assert cfg.vad.end_silence_ms == 400
    assert client.post("/api/config", json={"updates": {"vad": {"nope": 1}}}, headers=WRITE).status_code == 400
    unknown_tool = {"tools": {"disabled": ["rm_rf"]}}
    assert client.post("/api/config", json={"updates": unknown_tool}, headers=WRITE).status_code == 400


def test_events_stream_to_the_page(ui):
    server, client, _, bus, _ = ui
    client.get(f"/?t={server.token}")
    bus.publish("state", state="listening")
    with client.stream("GET", "/api/events") as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                assert json.loads(line[5:])["state"] == "listening"
                break


def test_actions_wait_for_the_voice_loop(ui):
    server, client, *_ = ui
    client.get(f"/?t={server.token}")
    response = client.post("/api/action", json={"action": "wake"}, headers=WRITE)
    assert response.status_code == 400
    assert "charger" in response.json()["error"]
