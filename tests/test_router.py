import threading

import pytest

from jarvis.llm.base import Delta, Done, Notice
from jarvis.llm.router import CLAUDE, LOCAL, Router

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


class FakeBackend:
    def __init__(self, model, deltas=(), fail_before=False, fail_after=False, check_error=None):
        self.model = model
        self.deltas = list(deltas)
        self.fail_before = fail_before
        self.fail_after = fail_after
        self.check_error = check_error
        self.tools_seen = []

    def check(self):
        if self.check_error:
            raise RuntimeError(self.check_error)

    def warmup(self, system_prompt, tools=None):
        pass

    def stream(self, messages, cancel=None, tools=None):
        self.tools_seen.append(tools)
        if self.fail_before:
            raise RuntimeError("hors ligne")
        for delta in self.deltas:
            yield Delta(delta)
        if self.fail_after:
            raise RuntimeError("coupé")
        yield Done()


def spoken(events):
    return [e.text for e in events if isinstance(e, Delta | Notice)]


def test_claude_failing_before_speaking_falls_back_to_local_and_stays_there():
    local = FakeBackend("qwen", ["Salut."])
    router = Router({LOCAL: local, CLAUDE: FakeBackend("haiku", fail_before=True)}, CLAUDE, LOCAL)
    router.tools = [{"name": "open_app"}]
    events = list(router.stream(MESSAGES))
    assert isinstance(events[0], Notice)
    assert "local" in events[0].text
    assert spoken(events)[1:] == ["Salut."]
    assert router.active == LOCAL
    assert local.tools_seen == [[{"name": "open_app"}]]


def test_failure_in_the_middle_of_an_answer_is_not_hidden():
    router = Router({LOCAL: FakeBackend("qwen", ["x"]), CLAUDE: FakeBackend("haiku", ["Bon"], fail_after=True)},
                    CLAUDE, LOCAL)
    with pytest.raises(RuntimeError, match="coupé"):
        list(router.stream(MESSAGES))


def test_no_fallback_when_disabled():
    router = Router({LOCAL: FakeBackend("qwen"), CLAUDE: FakeBackend("haiku", fail_before=True)}, CLAUDE, None)
    with pytest.raises(RuntimeError, match="hors ligne"):
        list(router.stream(MESSAGES))


def test_cancelled_answer_does_not_fall_back():
    cancel = threading.Event()
    cancel.set()
    router = Router({LOCAL: FakeBackend("qwen", ["x"]), CLAUDE: FakeBackend("haiku", fail_before=True)},
                    CLAUDE, LOCAL)
    with pytest.raises(RuntimeError):
        list(router.stream(MESSAGES, cancel=cancel))


def test_switch_checks_the_target_first():
    claude = FakeBackend("haiku", check_error="pas connecté")
    router = Router({LOCAL: FakeBackend("qwen"), CLAUDE: claude}, LOCAL, LOCAL)
    assert "terminal" in router.switch(CLAUDE)
    assert router.active == LOCAL
    claude.check_error = None
    assert router.switch(CLAUDE) == "C'est fait, je passe sur Claude."
    assert router.active == CLAUDE
    assert router.switch(CLAUDE) == "J'utilise déjà Claude."
    assert router.describe() == "J'utilise Claude, modèle haiku."


def test_startup_falls_back_when_claude_is_not_logged_in():
    router = Router({LOCAL: FakeBackend("qwen"), CLAUDE: FakeBackend("haiku", check_error="expiré")}, CLAUDE, LOCAL)
    router.check()
    assert router.active == LOCAL


def test_startup_fails_loudly_when_nothing_works():
    router = Router({LOCAL: FakeBackend("qwen", check_error="ollama absent"),
                     CLAUDE: FakeBackend("haiku", check_error="expiré")}, CLAUDE, LOCAL)
    with pytest.raises(RuntimeError, match="expiré"):
        router.check()


def test_switching_to_claude_gives_back_the_local_model_memory():
    """Le modèle local occupe 3,6 Go mesurés : inutile de les garder pendant qu'on parle à Claude."""
    import time

    freed = []

    class Local(FakeBackend):
        def unload(self):
            freed.append(True)

    local, claude = Local("local"), FakeBackend("claude")
    router = Router({LOCAL: local, CLAUDE: claude}, LOCAL)
    router.switch(CLAUDE)
    deadline = time.monotonic() + 3
    while not freed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert freed == [True]

    freed.clear()
    router.switch(LOCAL)                       # retour au local : on ne décharge pas ce qu'on va utiliser
    time.sleep(0.2)
    assert freed == []

    router.free_local_memory = False           # réglage coupé : on garde le modèle chaud
    router.switch(CLAUDE)
    time.sleep(0.2)
    assert freed == []
