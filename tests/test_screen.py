import time

import numpy as np
import pytest

from jarvis.config import ScreenConfig
from jarvis.vision.screen import ScreenWatcher, describe_prompt


class FakeScreen:
    def __init__(self):
        self.brightness = 10.0
        self.app = "Visual Studio Code"
        self.prompts = []

    def grab(self, max_width):
        return b"png", np.full((18, 32), self.brightness)

    def describe(self, image, prompt):
        self.prompts.append(prompt)
        return f"L'utilisateur code (vue {len(self.prompts)})."


def make_watcher(screen, **cfg):
    return ScreenWatcher(ScreenConfig(**cfg), screen.describe, grab=screen.grab, front=lambda: screen.app)


def test_unchanged_screen_is_not_described_again():
    screen = FakeScreen()
    watcher = make_watcher(screen)
    first = watcher.observe()
    second = watcher.observe()
    assert first.text == second.text == "L'utilisateur code (vue 1)."
    assert len(screen.prompts) == 1
    screen.brightness = 200.0
    assert watcher.observe().text == "L'utilisateur code (vue 2)."
    screen.app = "Safari"
    assert watcher.observe().text == "L'utilisateur code (vue 3)."          # changement d'application


def test_the_model_is_anchored_on_the_real_front_app():
    screen = FakeScreen()
    make_watcher(screen).observe()
    assert "« Visual Studio Code »" in screen.prompts[0]
    assert "N'invente rien" in screen.prompts[0]
    assert "n'est pas connue" in describe_prompt("")


def test_context_mentions_the_front_app_and_expires():
    screen = FakeScreen()
    watcher = make_watcher(screen)
    assert watcher.context() is None
    watcher.observe()
    context = watcher.context()
    assert "Visual Studio Code" in context
    assert "L'utilisateur code" in context
    watcher.latest = watcher.latest.__class__(watcher.latest.text, watcher.latest.app, 0.0)
    assert watcher.context() is None


def test_disabled_watcher_gives_no_context():
    screen = FakeScreen()
    watcher = make_watcher(screen, enabled=False)
    watcher.observe()
    assert watcher.context() is None


def test_look_forwards_the_question():
    screen = FakeScreen()
    watcher = make_watcher(screen)
    watcher.look("C'est quoi cette erreur ?")
    watcher.look()
    assert "C'est quoi cette erreur ?" in screen.prompts[0]
    assert screen.prompts[1] == describe_prompt("Visual Studio Code")


def test_a_missing_or_asleep_monitor_gives_a_clear_error():
    from jarvis.vision.screen import pick_monitor

    assert pick_monitor([{"width": 3000, "height": 2000}, {"left": 0, "top": 0, "width": 1440, "height": 900}]) == \
        {"left": 0, "top": 0, "width": 1440, "height": 900}
    with pytest.raises(RuntimeError, match="verrouillée ou écran en veille"):
        pick_monitor([{"left": 0, "top": 0, "width": 0, "height": 0}])      # Mac en veille : ce que mss renvoie
    with pytest.raises(RuntimeError):
        pick_monitor([])


def test_the_watcher_waits_while_memory_is_short(monkeypatch):
    screen = FakeScreen()
    short = {"value": True}
    watcher = ScreenWatcher(ScreenConfig(interval_s=0.01), screen.describe, grab=screen.grab,
                            front=lambda: screen.app, pressure=lambda gb: short["value"])
    watcher.start()
    time.sleep(0.1)
    assert screen.prompts == []                    # pas une seule analyse tant que la mémoire manque
    short["value"] = False
    deadline = time.monotonic() + 2
    while not screen.prompts and time.monotonic() < deadline:
        time.sleep(0.01)
    watcher.stop()
    assert len(screen.prompts) >= 1
