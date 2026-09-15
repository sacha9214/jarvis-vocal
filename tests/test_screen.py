import numpy as np

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
