import anyio
import httpx
import pytest

import jarvis.tools.builtin  # noqa: F401 - enregistre les outils intégrés avant les copies du registre
from jarvis import fastpath
from jarvis.config import ToolsConfig
from jarvis.server import start_server
from jarvis.system import timers, web
from jarvis.tools import ALWAYS, N2, N3, NO, REGISTRY, YES, ToolExecutor, tool
from jarvis.tools.mcp_server import build_mcp


@pytest.fixture
def calls():
    saved = dict(REGISTRY)
    log = []
    tool("t_n1", "n1", {"name": {"type": "string"}, "count": {"type": "integer"}}, ("name",))(
        lambda name, count=1: log.append(("n1", name, count)) or f"ok {name} {count}")
    tool("t_n2", "n2", level=N2, confirm=lambda a: "Je fais n2 ?")(lambda: log.append(("n2",)) or "n2 fait")
    tool("t_n3", "n3", level=N3)(lambda: log.append(("n3",)) or "n3 fait")
    tool("t_boom", "boom")(lambda: 1 / 0)
    yield log
    REGISTRY.clear()
    REGISTRY.update(saved)


def make_executor(answers=(), cfg=None):
    questions = []
    replies = iter(answers)

    def confirm(question, allow_always):
        questions.append((question, allow_always))
        return next(replies)
    return ToolExecutor(cfg or ToolsConfig(), confirm=confirm), questions


def test_n1_runs_without_asking_and_cleans_arguments(calls):
    executor, questions = make_executor()
    assert executor.run("t_n1", {"name": "x", "count": "3", "inconnu": 1}) == "ok x 3"
    assert questions == []
    assert calls == [("n1", "x", 3)]


def test_n2_asks_and_remembers_always(calls):
    cfg = ToolsConfig()
    remembered = []
    executor, questions = make_executor([NO, ALWAYS], cfg)
    executor.on_always = remembered.append
    assert executor.run("t_n2") == "D'accord, j'annule."
    assert calls == []
    assert executor.run("t_n2") == "n2 fait"
    assert executor.run("t_n2") == "n2 fait"                 # plus de question
    assert questions == [("Je fais n2 ?", True)] * 2
    assert cfg.always_allow == ["t_n2"]
    assert remembered == ["t_n2"]


def test_n3_asks_every_time_and_is_never_remembered(calls):
    cfg = ToolsConfig()
    executor, questions = make_executor([ALWAYS, YES], cfg)
    executor.run("t_n3")
    executor.run("t_n3")
    assert [allow for _, allow in questions] == [False, False]
    assert cfg.always_allow == []


def test_disabled_and_unknown_tools_do_nothing(calls):
    executor, _ = make_executor(cfg=ToolsConfig(disabled=["t_n1"]))
    assert "pas disponible" in executor.run("t_n1", {"name": "x"})
    assert "pas disponible" in executor.run("inexistant")
    assert calls == []


def test_failures_are_spoken_not_raised(calls):
    executor, _ = make_executor()
    assert executor.run("t_boom").startswith("L'action a échoué")
    assert "précisions" in executor.run("t_n1", {})


def test_mcp_exposes_tools_through_the_executor(calls):
    executor = ToolExecutor(ToolsConfig(disabled=[name for name in REGISTRY if name != "t_n1"]))
    server = build_mcp(executor)

    async def scenario():
        names = [t.name for t in await server.list_tools()]
        result = await server.call_tool("t_n1", {"name": "salut"})
        return names, result

    names, result = anyio.run(scenario)
    assert names == ["t_n1"]
    assert calls == [("n1", "salut", 1)]
    assert "ok salut 1" in str(result)


def test_local_server_requires_token_and_local_host():
    server = start_server(ToolExecutor(ToolsConfig()))
    try:
        auth = {"Authorization": f"Bearer {server.token}"}
        assert httpx.post(server.mcp_url, json={}).status_code == 403
        assert httpx.post(server.mcp_url, json={}, headers={**auth, "Host": "evil.example"}).status_code == 403
        response = httpx.post(server.mcp_url, headers={**auth, "Accept": "application/json, text/event-stream"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}},
        })
        assert response.status_code == 200
        assert "jarvis" in response.text
    finally:
        server.stop()


@pytest.mark.parametrize(("text", "decision"), [("Oui.", "yes"), ("Oui vas-y", "yes"), ("Toujours", "always"),
                                                ("Non merci", "no"), ("Peut-être", None)])
def test_confirmation_answers(text, decision):
    assert fastpath.confirmation(text) == decision


def test_spoken_durations_and_sites():
    assert timers.spoken_duration(5400) == "1 heure 30 minutes"
    assert timers.spoken_duration(45) == "45 secondes"
    assert web.resolve_site("YouTube") == "https://www.youtube.com"
    assert web.resolve_site("netflix.com") == "https://netflix.com"
    assert web.resolve_site("le site de Wikipédia") == "https://fr.wikipedia.org"
    assert web.resolve_site("n'importe quoi") is None
