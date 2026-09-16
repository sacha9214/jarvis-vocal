"""Ce qui évite un plantage ou une lenteur : journal sur disque, rechauffe différée, garde-fous de la boucle."""
import inspect
import logging
import threading
import time
from types import SimpleNamespace

import numpy as np

from jarvis import logs
from jarvis.config import Config
from jarvis.events import EventBus
from jarvis.pipeline import Assistant
from jarvis.tools import ToolExecutor
from jarvis.vision.screen import Observation


def test_logs_go_to_a_rotating_file_and_thread_deaths_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = logs.setup()
        assert path == tmp_path / "logs" / "jarvis.log"
        logging.getLogger("jarvis.test").warning("coucou journal")

        def die():
            raise ValueError("fil cassé")
        thread = threading.Thread(target=die, name="fragile")
        thread.start()
        thread.join()
        for handler in root.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "coucou journal" in text
        assert "fragile" in text and "fil cassé" in text
        assert (tmp_path / "logs" / "crash.log").exists()
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        threading.excepthook = threading.__excepthook__


class FakeLocalLLM:
    active = "local"
    model = "faux"

    def __init__(self):
        self.warmups = 0
        self.gate = threading.Event()

    def warmup(self, system, tools=None):
        self.warmups += 1
        self.tools = tools
        self.gate.wait(1)


def make_assistant(llm, context=""):
    cfg = Config()
    parts = SimpleNamespace(vad=object(), executor=ToolExecutor(cfg.tools), screen=None, review=None, llm=llm,
                            foreground=SimpleNamespace(context=lambda: context), tts=None, wakeword=None, stt=None)
    assistant = Assistant(cfg, parts, mic=SimpleNamespace(dropped=0), player=SimpleNamespace(stop=lambda: None),
                          bus=EventBus())
    assistant._system = "prompt système"
    return assistant


def test_the_cache_is_rewarmed_once_at_wake_not_after_every_screen_analysis():
    llm = FakeLocalLLM()
    assistant = make_assistant(llm)
    assistant._warm_context = ""                              # le démarrage a chauffé le prompt sans contexte
    assistant._prewarm()
    assert llm.warmups == 0                                   # rien d'évincé : rien à rechauffer
    for _ in range(3):
        assistant._on_screen(Observation("code", "Code", time.time()))
    assert llm.warmups == 0                                   # plus de rechauffe à chaque analyse (GPU pour rien)
    assistant._prewarm()
    assistant._prewarm()                                      # deuxième réveil pendant la première rechauffe
    llm.gate.set()
    time.sleep(0.05)
    assert llm.warmups == 1
    assistant._prewarm()
    assert llm.warmups == 1                                   # cache à jour : rien à faire
    assistant._rewarm()
    assistant._prewarm()
    time.sleep(0.05)
    assert llm.warmups == 2


def test_a_failed_announcement_does_not_block_the_caller():
    assistant = make_assistant(FakeLocalLLM())
    assistant._say = lambda frames, text: (_ for _ in ()).throw(RuntimeError("voix cassée"))
    assistant.announce("minuteur fini")
    assert assistant._tick(__import__("numpy").zeros(512, "float32"), iter([])) is True
    assert assistant._requests.empty()                        # la demande a été consommée malgré l'erreur


def test_wake_warms_the_tools_of_the_current_context():
    llm = FakeLocalLLM()
    llm.gate.set()
    assistant = make_assistant(llm, context="code")
    assistant._warm_context = ""                              # démarrage : outils sans contexte dans le cache
    assistant._prewarm()                                      # réveil dans l'éditeur : autre liste d'outils
    time.sleep(0.05)
    assert llm.warmups == 1
    assert "code_read" in {t["function"]["name"] for t in llm.tools}
    assert "browser_media" not in {t["function"]["name"] for t in llm.tools}
    assistant._prewarm()
    time.sleep(0.05)
    assert llm.warmups == 1                                   # même contexte : le cache est déjà bon


def test_the_wake_word_threshold_is_the_measured_one():
    """0,25 mesuré sur 25 « Hey Jarvis » et 5 min de parole et de bruit : contre 0,5, la détection passe
    de 48 à 83 % dans le bruit et de 64 à 80 % pendant que Jarvis parle, sans un seul réveil intempestif."""
    from jarvis.audio.wakeword import WakeWord
    from jarvis.config import WakeWordConfig
    from jarvis.ui import schema

    assert WakeWordConfig().threshold == 0.25
    assert inspect.signature(WakeWord.__init__).parameters["threshold"].default == 0.25
    field = next(f for section in schema.SECTIONS for f in section[3] if f.key == "wakeword.threshold")
    assert field.min <= 0.25 <= field.max


def test_a_near_miss_is_reported_once_in_a_while():
    """Un « presque » (score au-dessus de 60 % du seuil) est signalé une fois, pas à chaque trame."""
    assistant = make_assistant(FakeLocalLLM())
    events = []
    assistant.bus.subscribe(lambda e: events.append(e) if e["type"] == "near_miss" else None)
    wakeword = SimpleNamespace(score=0.20, threshold=0.25)
    for _ in range(50):
        assistant._near_miss(wakeword)
    assert len(events) == 1 and events[0]["score"] == 0.2

    assistant._last_near_miss = 0.0
    assistant._near_miss(SimpleNamespace(score=0.05, threshold=0.25))     # trop bas : rien à signaler
    assert len(events) == 1

    assistant.cfg.wakeword.near_miss = False                              # désactivable
    assistant._last_near_miss = 0.0
    assistant._near_miss(SimpleNamespace(score=0.24, threshold=0.25))
    assert len(events) == 1


def test_a_lost_microphone_is_reported_and_reopened(monkeypatch):
    """Micro débranché ou pris par une autre application : Jarvis restait sourd sans jamais le dire."""
    import queue as queue_module

    from jarvis.audio import io

    monkeypatch.setattr(io, "_DEAF_AFTER", 2)
    monkeypatch.setattr(io, "_RETRY_EVERY", 1)

    class SilentThenBack:
        """File du micro : plus rien, jusqu'à ce que la réouverture réussisse."""

        def __init__(self):
            self.audio = None

        def get(self, timeout=None):
            if self.audio is None:
                raise queue_module.Empty
            audio, self.audio = self.audio, None
            return audio

    mic = io.Microphone.__new__(io.Microphone)
    mic._frame, mic._resampler, mic.name = 4, None, "Micro d'essai"
    mic._queue = SilentThenBack()
    lost = []
    mic.on_lost = lambda: lost.append(True)
    attempts = []

    def reopen():
        attempts.append(True)
        if len(attempts) >= 2:                      # le micro revient au deuxième essai
            mic._queue.audio = np.ones(4, np.float32)
            return True
        return False

    mic._reopen = reopen
    assert list(next(mic.frames())) == [1.0, 1.0, 1.0, 1.0]     # l'audio revient tout seul
    assert lost == [True]                                        # prévenu une seule fois
    assert len(attempts) == 2
