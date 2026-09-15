import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from jarvis.config import ClaudeConfig, Config
from jarvis.events import EventBus
from jarvis.llm.claude_code import ClaudeCodeLLM
from jarvis.review import collect, engines
from jarvis.review.manager import ReviewManager
from jarvis.review.project import find_project, jetbrains_projects, recent, title_parts, uri_path
from jarvis.system.foreground import Foreground

FAKE = Path(__file__).parent / "fake_claude.py"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")


def editor_state(base, folders, files=(), opened=None):
    """Historique d'un VS Code factice : storage.json et state.vscdb."""
    storage = base / "Code" / "User" / "globalStorage"
    storage.mkdir(parents=True)
    windows = {"lastActiveWindow": {"folder": opened.as_uri()} if opened else {}}
    (storage / "storage.json").write_text(json.dumps({"windowsState": windows}), encoding="utf-8")
    entries = ([{"folderUri": f.as_uri()} for f in folders] + [{"fileUri": f.as_uri()} for f in files]
               + [{"folderUri": "vscode-remote://ssh-remote+box/srv/app"}])
    with closing(sqlite3.connect(storage / "state.vscdb")) as db:
        db.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        db.execute("INSERT INTO ItemTable VALUES (?, ?)",
                   ("history.recentlyOpenedPathsList", json.dumps({"entries": entries})))
        db.commit()
    return [base / "Code" / "User"]


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize(("title", "app", "parts"), [
    ("● pipeline.py - jarvis-vocal - Visual Studio Code", "Visual Studio Code", ["pipeline.py", "jarvis-vocal"]),
    ("pipeline.py — jarvis-vocal", "Code", ["pipeline.py", "jarvis-vocal"]),
    ("main.rs - demo [SSH: box] - Visual Studio Code - Insiders", "Visual Studio Code - Insiders", ["main.rs", "demo"]),
    ("app.tsx - site - Cursor", "Cursor", ["app.tsx", "site"]),
])
def test_editor_window_titles(title, app, parts):
    assert title_parts(title, app) == parts


def test_editor_uris():
    assert uri_path("file:///c%3A/Users/sacha/mon%20projet").as_posix() == "c:/Users/sacha/mon projet"
    assert uri_path("vscode-remote://ssh-remote+box/srv/app") is None


def test_project_comes_from_the_editor_window_then_history(tmp_path):
    jarvis, site = tmp_path / "jarvis-vocal", tmp_path / "site"
    (jarvis / "src").mkdir(parents=True)
    site.mkdir()
    (jarvis / "src" / "pipeline.py").write_text("x = 1\n", encoding="utf-8")
    dirs = editor_state(tmp_path / "state", [site, jarvis], opened=site)
    assert recent(dirs)[0] == [site, jarvis]

    window = Foreground("Visual Studio Code", "pipeline.py - jarvis-vocal - Visual Studio Code", 1, 0.0)
    project = find_project(window, dirs=dirs)
    assert project.root == jarvis
    assert project.current_file == jarvis / "src" / "pipeline.py"
    assert find_project(None, dirs=dirs).root == site                  # sans fenêtre : le plus récent
    assert find_project(None, "Jarvis vocal", dirs=dirs).root == jarvis
    assert find_project(None, "inconnu", dirs=dirs) is None


def test_named_project_is_found_without_editor_history(tmp_path):
    desktop = tmp_path / "Desktop"
    (desktop / "jarvis-vocal").mkdir(parents=True)
    (desktop / "clients" / "site-vitrine").mkdir(parents=True)
    assert find_project(None, "Jarvis vocal", dirs=[], bases=[desktop]).root == desktop / "jarvis-vocal"
    assert find_project(None, "site vitrine", dirs=[], bases=[desktop]).root == desktop / "clients" / "site-vitrine"
    assert find_project(None, "", dirs=[], bases=[desktop]) is None
    pycharm = Foreground("PyCharm", "jarvis-vocal – pipeline.py", 1, 0.0)
    assert find_project(pycharm, dirs=[], bases=[desktop]).root == desktop / "jarvis-vocal"


def test_jetbrains_recent_projects(tmp_path):
    options = tmp_path / "JetBrains" / "PyCharm2026.2" / "options"
    options.mkdir(parents=True)
    project = tmp_path / "home" / "PycharmProjects" / "demo"
    project.mkdir(parents=True)
    (options / "recentProjects.xml").write_text(
        '<application><component name="RecentProjectsManager"><option name="additionalInfo"><map>'
        '<entry key="$USER_HOME$/PycharmProjects/demo"><value><RecentProjectMetaInfo /></value></entry>'
        '<entry key="$USER_HOME$/disparu"><value /></entry></map></option></component></application>',
        encoding="utf-8")
    assert jetbrains_projects([tmp_path / "JetBrains"], home=tmp_path / "home") == [project]


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@needs_git
def test_project_files_and_uncommitted_changes(tmp_path):
    root = tmp_path / "demo"
    (root / "node_modules" / "lib").mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "uv.lock").write_text("verrou\n", encoding="utf-8")
    (root / "node_modules" / "lib" / "index.js").write_text("x\n", encoding="utf-8")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "new.py").write_text("print('nouveau')\n", encoding="utf-8")

    assert [p.name for p in collect.project_files(root)] == ["app.py", "new.py"]
    material = collect.changes(root)
    assert "-    return a + b" in material.diff and "+    return a - b" in material.diff
    assert [p.name for p in material.files] == ["new.py"]
    (tmp_path / "pas-git").mkdir()
    assert collect.changes(tmp_path / "pas-git") is None


def test_chunks_fit_the_local_context():
    long_file = "### a.py\n" + "\n".join(f"{i}| valeur = {i}" for i in range(1, 400))
    parts = collect.chunks([long_file, "### b.py\n1| y = 2"], 1000)
    assert all(len(part) <= 1100 for part in parts)
    assert parts[1].startswith("### a.py (suite)")
    assert "### b.py" in parts[-1]


def test_local_review_reads_in_passes_then_merges(tmp_path):
    (tmp_path / "a.py").write_text("def f(x):\n    return 1 / x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("ok = True\n", encoding="utf-8")
    material = collect.Material("project", tmp_path, [tmp_path / "a.py", tmp_path / "b.py"])
    calls = []

    def chat(system, content, tokens):
        calls.append(system)
        if system == engines.REDUCE_SYSTEM:
            return ("RÉSUMÉ : Division par zéro possible dans f.\n\n"
                    "## Problèmes importants\n- `a.py:2` : x peut valoir 0.")
        return "- a.py:2 : division par zéro → vérifier x" if "a.py" in content else "RAS"

    report, note = engines.local_review(material, chat, max_chars=10_000, size=60)
    assert calls == [engines.MAP_SYSTEM, engines.MAP_SYSTEM, engines.REDUCE_SYSTEM]
    assert engines.split_report(report) == ("Division par zéro possible dans f.",
                                            "## Problèmes importants\n- `a.py:2` : x peut valoir 0.")
    assert note == ""
    report, note = engines.local_review(material, lambda system, content, tokens: "RAS", max_chars=30, size=60)
    assert report == engines.NOTHING_FOUND
    assert "relu 1 sur 2" in note


def test_summary_falls_back_to_the_first_sentences():
    summary, _ = engines.split_report("Tout va **bien**. Rien à signaler. Autre chose.")
    assert summary == "Tout va bien. Rien à signaler."


def test_summary_written_as_a_heading():
    report = "# RÉSUMÉ\nUne injection SQL.\n\n## Problèmes importants\n- `db.py:4` : requête formatée."
    summary, body = engines.split_report(report)
    assert (summary, body) == ("Une injection SQL.", "## Problèmes importants\n- `db.py:4` : requête formatée.")
    assert engines.split_report("**Résumé** : Rien de grave.\n\n## À améliorer")[0] == "Rien de grave."


def test_local_budgets_fit_the_context():
    size, notes = engines.budgets(4096)
    assert size / 2.9 + 250 + engines.MAP_TOKENS <= 4096
    assert notes / 2.9 + 450 + engines.REDUCE_TOKENS <= 4096


def test_claude_reviews_read_only_from_outside_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    log = tmp_path / "claude.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    root = tmp_path / "demo"
    root.mkdir()
    material = collect.Material("changes", root, [root / "new.py"], "diff --git a/x.py b/x.py\n+ligne ajoutée")
    request, args = engines.claude_request(material)
    assert "+ligne ajoutée" in request and "new.py" in request

    claude = ClaudeCodeLLM(ClaudeConfig(), command=[sys.executable, str(FAKE)])
    report = claude.run_task(request, engines.CLAUDE_SYSTEM, args, model="sonnet", timeout=30)
    assert report.startswith("RÉSUMÉ : Relu")
    call = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    assert argv[argv.index("--add-dir") + 1] == str(root)
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert Path(call["cwd"]).resolve() != root.resolve()
    assert call["settings"]["disableAllHooks"] is True

    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(RuntimeError, match="claude auth login"):
        claude.run_task(request, engines.CLAUDE_SYSTEM, args, timeout=30)


def test_review_falls_back_to_local_and_announces_the_summary(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "a.py").write_text("x = 1 / 0\n", encoding="utf-8")
    bus = EventBus()
    events = []
    bus.subscribe(events.append)

    def claude(request, system, args):
        raise RuntimeError("Connexion Claude Code absente")

    def chat(system, content, tokens):
        return ("RÉSUMÉ : Division par zéro.\n\n## Problèmes importants\n- `a.py:1`" if system == engines.REDUCE_SYSTEM
                else "- a.py:1 : division par zéro")

    manager = ReviewManager(Config(), engine=lambda: "claude", bus=bus, chat=chat, claude=claude,
                            reports=tmp_path / "rapports", dirs=editor_state(tmp_path / "state", [root]))
    spoken = []
    manager.announce = spoken.append
    assert manager.start("project") == "Je lance la review du projet demo avec Claude, je te préviens quand c'est fini."
    assert wait_for(lambda: manager._job is None)
    assert spoken == ["La review du projet demo est terminée. Division par zéro."]
    final = next(event for event in events if event["type"] == "review")
    assert final["engine"] == "local" and "Claude indisponible" in final["note"]
    assert Path(final["path"]).read_text(encoding="utf-8").startswith("# Review du projet demo")
    assert manager.status().startswith("Aucune review en cours.")


def test_review_explains_what_it_cannot_do(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    dirs = editor_state(tmp_path / "state", [root])
    manager = ReviewManager(Config(), engine=lambda: "local", chat=lambda s, c, n: "RAS", reports=tmp_path / "r",
                            dirs=dirs)
    assert "n'est pas un dépôt git" in manager.start("changes")
    assert manager.start("project", "inconnu").startswith("Je ne trouve pas le projet inconnu")
    nothing = ReviewManager(Config(), engine=lambda: "local", dirs=[])
    assert nothing.start().startswith("Je ne trouve pas de projet ouvert")


def test_only_one_review_at_a_time(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    release = threading.Event()

    def slow_chat(system, content, tokens):
        release.wait(5)
        return "RAS"

    manager = ReviewManager(Config(), engine=lambda: "local", chat=slow_chat, reports=tmp_path / "r",
                            dirs=editor_state(tmp_path / "state", [root]))
    manager.start()
    assert "déjà en cours" in manager.start()
    assert manager.status().startswith("La review du projet demo est en cours avec le modèle local")
    assert manager.cancel() == "J'arrête la review du projet demo."
    release.set()
    assert wait_for(lambda: manager._job is None)
