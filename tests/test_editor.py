"""Extension VS Code : le pont `/editor`, le VSIX, l'extension exécutée pour de vrai sous Node (module vscode
simulé), et les outils `code_*` côté Jarvis."""
import asyncio
import itertools
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest
from test_browser import free_port, wait_for
from websockets.asyncio.client import connect

import jarvis.tools.builtin as builtin
from jarvis import commands
from jarvis.browser.bridge import EDITOR, BrowserUnavailable, start_bridge
from jarvis.config import Config, ToolsConfig
from jarvis.editor import EditorController, install
from jarvis.review.manager import ReviewManager
from jarvis.tools import ToolExecutor

HARNESS = Path(__file__).parent / "fake_vscode.js"
EXTENSION = Path(__file__).parent.parent / "src" / "jarvis" / "editor" / "extension"


# -- pont /editor : Node n'envoie pas d'Origin, un navigateur toujours

class FakeVsCode(threading.Thread):
    def __init__(self, port, token="jeton-secret", origin=None):
        super().__init__(daemon=True)
        self.port, self.token, self.origin = port, token, origin
        self.stop = threading.Event()
        self.errors, self.requests = [], []

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        try:
            async with connect(f"ws://127.0.0.1:{self.port}/editor", origin=self.origin) as ws:
                await ws.send(json.dumps({"type": "hello", "token": self.token, "app": "Visual Studio Code",
                                          "focused": True}))
                while not self.stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.2)
                    except TimeoutError:
                        continue
                    message = json.loads(raw)
                    self.requests.append(message)
                    await ws.send(json.dumps({"id": message["id"], "ok": True,
                                              "result": {"workspace": "/projet", "file": "/projet/src/app.py"}}))
        except Exception as exc:  # noqa: BLE001 - relevé par les tests
            self.errors.append(exc)


@pytest.fixture
def bridge():
    port = free_port()
    changes = []
    started = start_bridge("jeton-secret", port, on_change=lambda kind, names: changes.append((kind, names)))
    yield started, port, changes
    started.server.stop()


def test_the_editor_is_a_separate_client_from_browsers(bridge):
    server, port, changes = bridge
    editor = FakeVsCode(port)
    editor.start()
    try:
        assert wait_for(lambda: server.editors() == ["Visual Studio Code"])
        assert server.browsers() == []
        assert changes == [("editor", ["Visual Studio Code"])]
        with pytest.raises(BrowserUnavailable, match="Aucun navigateur"):
            server.call("media", {"action": "pause"})
        controller = EditorController(server)
        assert controller.linked()
        assert controller.call("status") == {"workspace": "/projet", "file": "/projet/src/app.py"}
        assert controller.current() == (Path("/projet"), Path("/projet/src/app.py"))
        assert editor.requests[0]["action"] == "status"
    finally:
        editor.stop.set()
        editor.join(2)
    assert wait_for(lambda: server.editors() == [])
    assert not EditorController(server).linked()
    assert EditorController(server).current() is None


def test_a_web_page_cannot_pose_as_the_editor(bridge):
    server, port, _ = bridge
    page = FakeVsCode(port, origin="https://site-malveillant.example")
    page.start()
    page.join(3)
    assert page.errors
    assert server.editors() == []
    wrong = FakeVsCode(port, token="mauvais")
    wrong.start()
    wrong.join(3)
    assert wrong.errors
    with pytest.raises(BrowserUnavailable, match="jarvis code"):
        server.call("status", kind=EDITOR)


# -- VSIX

def test_the_vsix_is_a_complete_package(tmp_path):
    vsix = install.build(tmp_path / "jarvis.vsix")
    with zipfile.ZipFile(vsix) as archive:
        names = set(archive.namelist())
        manifest = archive.read("extension.vsixmanifest").decode("utf-8")
        package = json.loads(archive.read("extension/package.json"))
    assert {"extension.vsixmanifest", "[Content_Types].xml", "extension/package.json", "extension/extension.js",
            "extension/LICENSE.txt"} <= names
    assert 'Id="jarvis" Version="1.0.0" Publisher="jarvis"' in manifest
    assert package["capabilities"]["untrustedWorkspaces"]["supported"] == "limited"   # sinon VS Code ne l'active pas
    assert package["activationEvents"] == ["onStartupFinished"]
    assert "jarvis.port" in package["contributes"]["configuration"]["properties"]


def test_install_reports_the_editor_answer(tmp_path):
    vsix = install.build(tmp_path / "jarvis.vsix")
    if sys.platform == "win32":
        script = tmp_path / "code.cmd"
        script.write_text('@echo %* > "%~dpn0.args"\n', encoding="utf-8")
    else:
        script = tmp_path / "code"
        script.write_text('#!/bin/sh\necho "$@" > "$0.args"\nexit 0\n', encoding="utf-8")
        script.chmod(0o755)
    assert install.install(vsix, str(script)) is None
    assert f"--install-extension {vsix} --force" in (tmp_path / "code.args").read_text(encoding="utf-8")
    assert install.install(vsix, str(tmp_path / "absent")) is not None


# -- l'extension pour de vrai (Node)

class FakeEditor:
    def __init__(self, calls: Path, data_dir: Path):
        self.calls = calls
        self.ids = itertools.count(1)
        self.process = subprocess.Popen([shutil.which("node"), str(HARNESS), str(EXTENSION), str(calls), str(data_dir)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
        self.hello = self._read()

    def _read(self) -> dict:
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"L'extension s'est arrêtée : {self.process.stderr.read()}")
        return json.loads(line)

    def send(self, text: str) -> None:
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def call(self, action: str, params: dict | None = None) -> dict:
        request = {"id": next(self.ids), "action": action, "params": params or {}}
        self.send(json.dumps(request))
        while True:
            message = self._read()
            if message.get("id") != request["id"]:
                continue
            if not message.get("ok"):
                raise RuntimeError(message.get("error") or "refus sans explication")
            return message.get("result") or {}

    def recorded(self, name: str) -> list[dict]:
        return [call for call in json.loads(self.calls.read_text(encoding="utf-8")) if call["name"] == name]

    def close(self) -> None:
        self.process.kill()


@pytest.fixture
def extension(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("Node absent")
    (tmp_path / "bridge-token").write_text("jeton-vscode\n", encoding="utf-8")
    editor = FakeEditor(tmp_path / "calls.json", tmp_path)
    yield editor
    editor.close()


def test_the_extension_announces_itself_with_the_token_read_on_disk(extension):
    assert extension.hello == {"type": "hello", "token": "jeton-vscode", "app": "Visual Studio Code", "focused": True,
                               "version": "1.134.0", "workspace": "projet"}
    assert extension.call("status") == {"app": "Visual Studio Code", "workspace": "/projet", "workspaces": ["/projet"],
                                        "file": "/projet/src/app.py", "language": "python", "line": 6, "dirty": False,
                                        "selection": True}
    extension.send("@blur")
    assert extension._read() == {"type": "focus", "focused": False}


def test_reading_gives_numbered_lines_around_the_cursor(extension):
    whole = extension.call("read", {"what": "file"})
    assert whole["name"] == "src/app.py" and (whole["from"], whole["to"]) == (1, 10)
    assert whole["text"].startswith("1| import os\n2| \n3| \n4| def main():")
    assert whole["selection"] == "def main():\n    total = add(1, 2)\n    print(total)"
    window = extension.call("read", {"what": "file", "max_chars": 40})
    assert window["from"] <= 6 <= window["to"] and window["to"] - window["from"] < 6
    assert "5| " in window["text"]
    selection = extension.call("read", {"what": "selection"})
    assert (selection["from"], selection["to"]) == (4, 6)
    tabs = extension.call("read", {"what": "tabs"})["tabs"]
    assert [(t["index"], t["label"], t["active"], t["dirty"]) for t in tabs] == \
        [(1, "app.py", True, False), (2, "README.md", False, True), (3, "Réglages", False, False)]


def test_errors_come_first_and_hints_are_dropped(extension):
    project = extension.call("errors", {"scope": "project"})
    assert (project["errors"], project["warnings"]) == (1, 2)
    assert [(i["file"], i["line"], i["severity"]) for i in project["items"]] == \
        [("src/app.py", 5, "erreur"), ("src/app.py", 1, "avertissement"), ("src/app_test.py", 2, "avertissement")]
    assert project["items"][0]["source"] == "Pylance"
    current = extension.call("errors", {"scope": "file"})
    assert [i["line"] for i in current["items"]] == [5, 1]


def test_open_prefers_the_exact_name_in_the_shortest_path(extension):
    opened = extension.call("open", {"file": "app.py", "line": 6})
    assert opened == {"file": "/projet/src/app.py", "name": "src/app.py", "line": 6}
    assert extension.recorded("findFiles")[-1]["glob"] == "**/*app.py*"
    assert extension.recorded("revealRange")[-1] == {"name": "revealRange", "line": 5, "kind": 2}
    assert extension.call("open", {"file": "app_test"})["name"] == "src/app_test.py"
    assert extension.call("open", {"file": "app test.py"})["name"] == "src/app_test.py"     # dit à la voix
    assert extension.call("open", {"file": "app test py"})["name"] == "src/app_test.py"     # sans le point
    assert extension.call("open", {"file": "app py"})["name"] == "src/app.py"
    assert extension.call("open", {"tab": 2})["name"] == "README.md"
    with pytest.raises(RuntimeError, match="n'est pas un fichier"):
        extension.call("open", {"tab": 3})
    with pytest.raises(RuntimeError, match="Je ne trouve pas"):
        extension.call("open", {"file": "inexistant.rs"})


def test_every_command_offered_to_the_model_exists_in_the_extension(extension):
    for action in builtin.CODE_COMMANDS:
        result = extension.call("command", {"action": action})
        assert result["done"] and result["command"], action
    ids = [call["id"] for call in extension.recorded("executeCommand")]
    assert len(ids) == len(builtin.CODE_COMMANDS)
    assert "workbench.action.files.save" in ids
    assert not any("sendSequence" in i or "git.push" in i for i in ids)
    with pytest.raises(RuntimeError, match="Action inconnue"):
        extension.call("command", {"action": "danse"})


def test_search_run_and_insert(extension):
    extension.call("search", {"query": "def main"})
    assert extension.recorded("executeCommand")[-1] == {"name": "executeCommand", "id": "workbench.action.findInFiles",
                                                        "args": {"query": "def main", "triggerSearch": True}}
    extension.call("run", {"mode": "test"})
    assert extension.recorded("executeCommand")[-1]["id"] == "testing.runAll"
    extension.call("insert", {"text": "pass", "mode": "cursor"})
    assert extension.recorded("insert")[-1] == {"name": "insert", "line": 5, "character": 16, "text": "pass"}
    extension.call("insert", {"text": "# note", "mode": "line"})
    assert extension.recorded("insert")[-1]["text"] == "\n    # note"       # même indentation que la ligne
    extension.call("insert", {"text": "x = 1", "mode": "replace"})
    assert extension.recorded("replace")[-1] == {"name": "replace", "from": 3, "to": 5, "text": "x = 1"}
    with pytest.raises(RuntimeError, match="Dis-moi quoi chercher"):
        extension.call("search", {"query": " "})


# -- côté Jarvis : outils, réflexes, review

class LinkedEditor:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def linked(self):
        return True

    def call(self, action, params=None, timeout=8.0):
        self.calls.append((action, params or {}))
        return self.answers.get(action, {})

    def current(self):
        return Path("/projet"), Path("/projet/src/app.py")


@pytest.fixture
def linked(monkeypatch):
    editor = LinkedEditor({
        "read": {"name": "src/app.py", "language": "python", "line": 5, "from": 1, "to": 9, "total_lines": 9,
                 "text": "1| import os", "selection": "os"},
        "errors": {"errors": 1, "warnings": 0, "items": [{"file": "src/app.py", "line": 5, "severity": "erreur",
                                                          "message": "add n'est pas défini", "source": "Pylance"}]},
        "open": {"name": "src/app.py", "line": 12},
    })
    monkeypatch.setattr(builtin, "EDITOR", editor)
    return editor


def test_code_tools_speak_the_editor_answers(linked):
    assert builtin.code_read() == "Fichier : src/app.py (python), curseur ligne 5\nTexte sélectionné : os\n1| import os"
    assert builtin.app_read() == builtin.code_read()          # VS Code : l'accessibilité ne voit rien, l'extension si
    assert builtin.code_errors("file") == ("1 erreur(s), 0 avertissement(s) :\n"
                                           "- src/app.py:5 [erreur Pylance] add n'est pas défini")
    assert builtin.code_open(file="app.py", line=12) == "J'ouvre src/app.py, ligne 12."
    assert builtin.code_open(line=12) == "Ligne 12."
    assert builtin.code_open() == "Dis-moi quel fichier ouvrir, ou quelle ligne."
    assert builtin.code_command("save") == "Fichier enregistré."
    assert builtin.code_search("main") == "Je cherche main dans le projet."
    assert builtin.code_run("test") == "Tests lancés."
    assert linked.calls[-1] == ("run", {"mode": "test"})


def test_code_tools_are_offered_only_in_the_editor(monkeypatch):
    monkeypatch.setattr(builtin, "EDITOR", None)
    executor = ToolExecutor(ToolsConfig())
    names = lambda context: {s["function"]["name"] for s in executor.schemas(context)}  # noqa: E731
    expected = {"code_read", "code_errors", "code_open", "code_command", "code_search", "code_insert", "code_run"}
    assert expected <= names("code")
    assert not {"code_read", "code_command"} & names("browser")
    assert "code_read" not in names("app")
    assert not executor.speaks("code_read") and not executor.speaks("code_errors") and executor.speaks("code_command")
    assert "aucun éditeur" in executor.run("code_command", {"action": "save"})


def test_editor_reflexes_do_not_need_the_llm():
    parse = lambda text: commands.parse(text, "code")  # noqa: E731
    assert parse("formate le fichier").arguments == {"action": "format"}
    assert parse("va à la ligne 42").tool == "code_open" and parse("va à la ligne 42").arguments == {"line": 42}
    assert parse("ouvre le fichier pipeline.py").arguments == {"file": "pipeline.py"}
    assert parse("ouvre pipeline point py").arguments == {"file": "pipeline.py"}
    assert parse("ouvre le fichier fake browser point js").arguments == {"file": "fake browser.js"}
    assert parse("enregistre").arguments == {"action": "save"}
    assert parse("enregistre tout").arguments == {"action": "save_all"}
    assert parse("ferme l'onglet").arguments == {"action": "close_tab"}
    assert parse("cherche foreground dans le projet").arguments == {"query": "foreground"}
    assert parse("lance les tests").arguments == {"mode": "test"}
    assert parse("montre les problèmes").arguments == {"action": "problems"}
    assert commands.parse("enregistre", "app").tool == "app_shortcut"          # hors éditeur : raccourci clavier
    assert commands.parse("ferme l'onglet", "browser").tool == "browser_close_tab"


def test_the_review_trusts_the_linked_editor_over_window_titles(tmp_path):
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    manager = ReviewManager(Config(), engine=lambda: "local", chat=lambda s, c, n: "RAS", reports=tmp_path / "r",
                            dirs=[], hint=lambda: (root, root / "src" / "app.py"))
    spoken = []
    manager.announce = spoken.append
    assert manager.start("file").startswith("Je lance la review d'app.py avec le modèle local")
    assert wait_for(lambda: manager._job is None)
    assert spoken and "app.py" in spoken[0]
    assert manager.start("project", "autre").startswith("Je ne trouve pas le projet autre")   # nommé : pas l'indice
