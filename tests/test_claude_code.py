import json
import sys
import threading
import time
from pathlib import Path

import pytest

from jarvis.config import ClaudeConfig
from jarvis.llm.base import Delta, Done
from jarvis.llm.claude_code import ClaudeCodeLLM, child_env

FAKE = Path(__file__).parent / "fake_claude.py"
SYSTEM = {"role": "system", "content": "Tu es Jarvis."}


@pytest.fixture
def llm(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    backend = ClaudeCodeLLM(ClaudeConfig(), command=[sys.executable, str(FAKE)])
    yield backend
    backend.close()


def invocations(tmp_path):
    log = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def user(text):
    return {"role": "user", "content": text}


def ask(llm, messages, cancel=None):
    return "".join(e.text for e in llm.stream(messages, cancel=cancel) if isinstance(e, Delta))


def test_streams_text_then_usage(llm):
    events = list(llm.stream([SYSTEM, user("Bonjour")]))
    assert "".join(e.text for e in events if isinstance(e, Delta)) == "Reçu : Bonjour"
    assert isinstance(events[-1], Done)
    assert events[-1].prompt_tokens == 100


def test_one_process_serves_every_question(llm, tmp_path):
    assert ask(llm, [SYSTEM, user("A")]) == "Reçu : A"
    assert ask(llm, [SYSTEM, user("A"), {"role": "assistant", "content": "Reçu : A"}, user("B")]) == "Reçu : B"
    assert len(invocations(tmp_path)) == 1


def test_turns_answered_elsewhere_are_given_as_context(llm):
    first = ask(llm, [SYSTEM, user("A")])
    history = [SYSTEM, user("A"), {"role": "assistant", "content": first},
               user("Quelle heure est-il ?"), {"role": "assistant", "content": "Il est midi."}, user("C")]
    second = ask(llm, history)
    assert "Utilisateur : Quelle heure est-il ?\nJarvis : Il est midi." in second
    assert second.endswith("Demande actuelle : C")
    assert "Reçu : A" not in second                     # déjà dans la session : pas répété
    assert ask(llm, [*history, {"role": "assistant", "content": second}, user("D")]) == "Reçu : D"


def test_new_system_prompt_restarts_the_process(llm, tmp_path):
    ask(llm, [SYSTEM, user("A")])
    ask(llm, [{"role": "system", "content": "Nouveau jour."}, user("B")])
    assert len(invocations(tmp_path)) == 2


def test_interrupting_a_long_answer_keeps_the_process(llm, tmp_path):
    cancel = threading.Event()
    received = []
    start = time.perf_counter()
    for event in llm.stream([SYSTEM, user("une longue réponse")], cancel=cancel):
        if isinstance(event, Delta):
            received.append(event.text)
            cancel.set()
    assert time.perf_counter() - start < 3
    assert len(received) < 50
    assert ask(llm, [SYSTEM, user("E")]) == "Reçu : E"
    assert len(invocations(tmp_path)) == 1


def test_error_result_gives_an_actionable_message(llm, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(RuntimeError, match="claude auth login"):
        ask(llm, [SYSTEM, user("A")])


def test_check_detects_a_missing_login(llm, monkeypatch):
    llm.check()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "logged_out")
    with pytest.raises(RuntimeError, match="claude auth login"):
        llm.check()


def test_claude_starts_isolated_from_the_user_setup(llm, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    ask(llm, [SYSTEM, user("A")])
    call = invocations(tmp_path)[0]
    argv = call["argv"]
    assert argv[argv.index("--disallowedTools") + 1] == "*"
    assert {"--disable-slash-commands", "--no-session-persistence"} <= set(argv)
    assert call["settings"]["enabledPlugins"] == {"sage@sage": False}
    assert call["settings"]["disableAllHooks"] is True
    assert call["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert "CLAUDECODE" not in call["env"]


def test_jarvis_tools_replace_claude_code_tools(llm, tmp_path):
    llm.configure_tools("http://127.0.0.1:1234/mcp", "jeton")
    ask(llm, [SYSTEM, user("A")])
    argv = invocations(tmp_path)[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--disallowedTools" not in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__jarvis"
    mcp = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["jarvis"]["url"] == "http://127.0.0.1:1234/mcp"


def test_child_env_uses_the_subscription_not_an_api_key():
    environ = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "desktop",
               "CLAUDE_CODE_OAUTH_TOKEN": "jeton-utilisateur", "ANTHROPIC_API_KEY": "cle",
               "ANTHROPIC_BASE_URL": "http://proxy"}
    env = child_env("subscription", environ)
    assert env["PATH"] == "/bin"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "jeton-utilisateur"
    assert not {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"} & env.keys()
    api = child_env("api_key", environ)
    assert api["ANTHROPIC_API_KEY"] == "cle"
    assert api["ANTHROPIC_BASE_URL"] == "http://proxy"
