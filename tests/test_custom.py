"""Commandes personnalisées (config.yaml › commands) et LLM Ollama sur une autre machine."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.tools.builtin as builtin
from jarvis import custom
from jarvis.config import Config, LlmConfig, ScreenConfig, ToolsConfig, is_local_host
from jarvis.llm.ollama_backend import OllamaLLM, pick_model
from jarvis.tools import REGISTRY, ToolExecutor
from jarvis.vision.screen import screen_host


@pytest.fixture
def commands(tmp_path):
    notes = tmp_path / "notes.txt"
    cfg = Config()
    cfg.commands = [
        {"name": "projet jarvis", "say": ["ouvre mon projet", "lance le projet Jarvis"], "open": str(tmp_path)},
        {"name": "note", "say": ["note *", "prends note de *"], "reply": "C'est noté, {text}.",
         "run": f'echo {{text}} >> "{notes}"'},
        {"name": "version", "say": "quelle version de python", "run": f'"{sys.executable}" --version',
         "speak_output": True},
        {"name": "capture", "say": ["fais une capture"], "keys": "ctrl+shift+4", "confirm": True},
        {"name": "minuteur thé", "say": ["thé"], "tool": "set_timer", "args": {"seconds": 180, "label": "thé"}},
    ]
    loaded = custom.load_commands(cfg)
    custom.register(loaded)
    yield loaded, custom.Matcher(loaded), notes
    for command in loaded:
        REGISTRY.pop(command.tool_name, None)


def test_phrases_are_recognised_without_the_llm(commands):
    _, matcher, _ = commands
    assert matcher.match("Ouvre mon projet !").tool == "custom_projet_jarvis"
    assert matcher.match("lance le projet jarvis").arguments == {}
    assert matcher.match("Note acheter du pain").arguments == {"text": "acheter du pain"}
    assert matcher.match("prends note de rappeler Léa demain").arguments == {"text": "rappeler léa demain"}
    assert matcher.match("thé").tool == "custom_minuteur_the"
    assert matcher.match("ouvre mon projet préféré") is None                # phrase entière, pas un préfixe
    assert matcher.match("note") is None                                    # rien à noter


def test_each_command_is_also_a_tool_for_the_model(commands):
    loaded, _, _ = commands
    executor = ToolExecutor(ToolsConfig())
    names = {tool.name: tool for tool in executor.tools("")}
    assert names["custom_note"].parameters["required"] == ["text"]
    assert names["custom_projet_jarvis"].parameters["properties"] == {}
    assert names["custom_capture"].level == "N2" and names["custom_capture"].question({}) == "Je lance capture ?"
    assert "« note * »" in names["custom_note"].description


def test_run_open_keys_and_tool_actions(commands, monkeypatch, tmp_path):
    loaded, matcher, notes = commands
    by_name = {c.name: c for c in loaded}
    assert custom.execute(by_name["note"], 'acheter du "pain"') == 'C\'est noté, acheter du "pain".'
    deadline = __import__("time").monotonic() + 3
    while not notes.exists() and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.02)
    assert "acheter du pain" in notes.read_text(encoding="utf-8")          # guillemets retirés du shell
    assert custom.execute(by_name["version"]).startswith("Python 3.")
    opened = []
    monkeypatch.setattr("jarvis.system.folders.open_path", lambda path: opened.append(path))
    assert custom.execute(by_name["projet jarvis"]) == "C'est fait pour projet jarvis."
    assert opened == [Path(tmp_path)]
    assert "Je ne trouve pas" in custom.execute(custom.CustomCommand("x", ["x"], open=str(tmp_path / "absent")))
    monkeypatch.setattr(builtin, "DESKTOP", SimpleNamespace(shortcut=lambda keys: f"J'ai appuyé sur {keys}."))
    assert custom.execute(by_name["capture"]) == "J'ai appuyé sur ctrl+shift+4."
    started = []
    monkeypatch.setattr(builtin.TIMERS, "start", lambda seconds, label="": started.append((seconds, label)) or "ok")
    assert custom.execute(by_name["minuteur thé"]) == "ok"
    assert started == [(180, "thé")]


def test_invalid_commands_are_explained():
    cfg = Config()
    for raw, message in (
        ({"say": ["x"], "run": "ls"}, "il manque `name`"),
        ({"name": "a", "run": "ls"}, "au moins une phrase"),
        ({"name": "a", "say": "x"}, "exactement une action"),
        ({"name": "a", "say": "x", "run": "ls", "open": "y"}, "exactement une action"),
        ({"name": "a", "say": "x", "tool": "inconnu"}, "n'existe pas"),
        ({"name": "a", "say": "x", "run": "ls", "bidule": 1}, "clé inconnue bidule"),
    ):
        cfg.commands = [raw]
        with pytest.raises(ValueError, match=message):
            custom.load_commands(cfg)
    cfg.commands = [{"name": "Thé", "say": "a", "run": "ls"}, {"name": "the", "say": "b", "run": "ls"}]
    with pytest.raises(ValueError, match="même identifiant"):
        custom.load_commands(cfg)


# -- Ollama sur une autre machine

def test_local_and_remote_hosts_are_told_apart():
    assert is_local_host("http://127.0.0.1:11434") and is_local_host("http://localhost:11434")
    assert not is_local_host("http://192.168.1.20:11434") and not is_local_host("http://pc-bureau:11434")
    assert LlmConfig(host="http://192.168.1.20:11434").remote


def test_a_remote_server_picks_its_biggest_qwen_or_lists_what_it_has():
    assert pick_model(["llama3:8b", "qwen3.5:4b", "qwen3.5:9b-q4"]) == "qwen3.5:9b-q4"
    assert pick_model(["llama3:8b"]) == "llama3:8b"
    with pytest.raises(RuntimeError, match="Aucun modèle"):
        pick_model([])
    cfg = LlmConfig(host="http://192.168.1.20:11434")
    llm = OllamaLLM(cfg)
    llm._client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model="qwen3.5:9b")]))
    assert cfg.model == "auto" and Config(llm=cfg).resolve().llm.model == "auto"      # distant : pas de choix local
    llm.check()
    assert llm.model == cfg.model == "qwen3.5:9b"
    llm.model = "mistral"
    with pytest.raises(RuntimeError, match="absent sur http://192.168.1.20:11434. Modèles présents.*qwen3.5:9b"):
        llm.check()

    class Down:
        def list(self):
            raise ConnectionError("refusé")
    llm._client = Down()
    with pytest.raises(RuntimeError, match="OLLAMA_HOST=0.0.0.0"):
        llm.check()


def test_screenshots_stay_on_this_machine_unless_asked():
    remote = Config(llm=LlmConfig(host="http://192.168.1.20:11434"))
    assert screen_host(remote) == "http://127.0.0.1:11434"
    assert screen_host(Config()) == "http://127.0.0.1:11434"
    remote.screen = ScreenConfig(host="http://192.168.1.20:11434")
    assert screen_host(remote) == "http://192.168.1.20:11434"
