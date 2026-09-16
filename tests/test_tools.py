import time

import anyio
import httpx
import pytest

import jarvis.tools.builtin as builtin  # noqa: F401 - enregistre les outils intégrés avant les copies du registre
from jarvis import commands, fastpath
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


# -- extinction programmée (« éteins l'ordinateur dans 25 minutes »)

def test_a_delayed_shutdown_is_announced_warned_and_cancellable(monkeypatch):
    """Aucune vraie extinction : la commande système est remplacée."""
    from jarvis.system import power as power_module

    fired = []
    monkeypatch.setattr(power_module, "execute", lambda action: fired.append(action))
    # Rappel 1,5 s avant l'action : un écart de quelques millisecondes pouvait inverser les deux fils sur la CI.
    monkeypatch.setattr(power_module, "WARN_BEFORE_S", 1.5)
    monkeypatch.setattr(power_module, "MIN_WARNED_DELAY_S", 1.6)
    scheduler = power_module.PowerScheduler()
    said = []
    scheduler.announce = said.append

    assert scheduler.schedule("shutdown", 1500) == (
        "C'est parti pour l'extinction dans 25 minutes. Dis « annule l'extinction » quand tu veux pour arrêter.")
    action, remaining = scheduler.pending()
    assert action == "shutdown" and 1490 < remaining <= 1500
    assert scheduler.status() == "L'extinction de l'ordinateur dans 25 minutes."    # arrondi, pas « 24 min 59 s »
    assert scheduler.cancel() == "J'annule l'extinction."
    assert scheduler.pending() is None and scheduler.status() == "Rien n'est programmé pour le moment."

    def wait_until(condition, seconds=15.0):
        """Attend sans dépendre de la charge de la machine (la CI est parfois très lente)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        return condition()

    scheduler.schedule("sleep", 2)                   # rappel à 0,5 s, action à 2 s
    assert wait_until(lambda: any("dans une minute" in text for text in said))     # prévenu avant
    assert fired == []                               # et pas encore agi
    assert wait_until(lambda: fired == ["sleep"])
    assert scheduler.pending() is None

    said.clear()
    scheduler.schedule("shutdown", 1)
    scheduler.cancel()
    time.sleep(1.5)
    assert fired == ["sleep"] and said == []         # annulé : ni action ni rappel

    assert "inconnue" in scheduler.schedule("exploser", 60)
    scheduler.schedule("shutdown", 10 ** 9)          # délai absurde : ramené à 24 h
    assert scheduler.pending()[1] <= 24 * 3600
    scheduler.cancel()


def test_the_power_tool_passes_the_delay_and_says_it_before_confirming(monkeypatch):
    scheduled = []
    monkeypatch.setattr(builtin.POWER, "schedule", lambda action, delay: scheduled.append((action, delay)) or "ok")
    assert builtin.power_action("shutdown", 1500) == "ok"
    assert scheduled[-1] == ("shutdown", 1500)
    builtin.power_action("shutdown")
    assert scheduled[-1] == ("shutdown", builtin.POWER_DELAY_S)      # sans délai : court, pour pouvoir annuler
    builtin.power_action("restart", 0)
    assert scheduled[-1] == ("restart", builtin.POWER_DELAY_S)       # zéro n'est pas un délai

    question = REGISTRY["power"].question
    assert question({"action": "shutdown", "seconds": 1500}) == \
        "Tu confirmes l'extinction de l'ordinateur dans 25 minutes ?"
    assert question({"action": "sleep"}) == "Tu confirmes la mise en veille de l'ordinateur ?"
    assert REGISTRY["power"].level == "N3"                            # confirmation à chaque fois


def test_asking_the_computer_state_mentions_a_planned_shutdown(monkeypatch):
    monkeypatch.setattr(builtin.status, "sentence", lambda: "Batterie à 80 pour cent.")
    monkeypatch.setattr(builtin.POWER, "pending", lambda: None)
    assert builtin.system_status() == "Batterie à 80 pour cent."
    monkeypatch.setattr(builtin.POWER, "pending", lambda: ("shutdown", 300.0))
    monkeypatch.setattr(builtin.POWER, "status", lambda: "L'extinction de l'ordinateur dans 5 minutes.")
    assert builtin.system_status() == "Batterie à 80 pour cent. L'extinction de l'ordinateur dans 5 minutes."


@pytest.mark.parametrize(("phrase", "arguments"), [
    ("Éteins l'ordinateur dans 25 minutes", {"action": "shutdown", "seconds": 1500}),
    ("éteins dans 25 minutes", {"action": "shutdown", "seconds": 1500}),
    ("Redémarre le pc dans une heure et demie", {"action": "restart", "seconds": 5400}),
    ("mets l'ordinateur en veille dans 20 minutes", {"action": "sleep", "seconds": 1200}),
    ("arrête le mac dans 45 minutes", {"action": "shutdown", "seconds": 2700}),
])
def test_a_delay_spoken_out_loud_needs_no_llm(phrase, arguments):
    command = commands.parse(phrase)
    assert command.tool == "power" and command.arguments == arguments


def test_a_timer_is_still_a_timer_not_a_shutdown():
    assert commands.parse("mets un minuteur de 25 minutes").tool == "set_timer"
    assert commands.parse("éteins la lumière dans 5 minutes") is None
