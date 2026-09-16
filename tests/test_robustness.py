"""Ce qui évite un plantage ou une lenteur : journal sur disque, rechauffe différée, garde-fous de la boucle."""
import logging
import threading
import time
from types import SimpleNamespace

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

    def warmup(self, system):
        self.warmups += 1
        self.gate.wait(1)


def make_assistant(llm):
    cfg = Config()
    parts = SimpleNamespace(vad=object(), executor=ToolExecutor(cfg.tools), screen=None, review=None, llm=llm,
                            foreground=None, tts=None, wakeword=None, stt=None)
    assistant = Assistant(cfg, parts, mic=SimpleNamespace(dropped=0), player=SimpleNamespace(stop=lambda: None),
                          bus=EventBus())
    assistant._system = "prompt système"
    return assistant


def test_the_cache_is_rewarmed_once_at_wake_not_after_every_screen_analysis():
    llm = FakeLocalLLM()
    assistant = make_assistant(llm)
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
