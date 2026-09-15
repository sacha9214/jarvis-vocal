import sys
import types

import numpy as np
import pytest

from jarvis import app
from jarvis.config import Config
from jarvis.tts import pocket_backend


class FakeTensor:
    def __init__(self, n):
        self.n = n

    def detach(self):
        return self

    def to(self, device):
        return self

    def numpy(self):
        return np.zeros(self.n, dtype=np.float64)


class FakeModel:
    sample_rate = 24000
    delay = 0.0
    blocks = 3
    voices = []
    temperature = None

    @classmethod
    def load_model(cls, language=None, temp=None):
        assert language == pocket_backend.LANGUAGE
        cls.temperature = temp
        return cls()

    def get_state_for_audio_prompt(self, voice):
        if voice == "inconnue":
            raise ValueError("voix inconnue")
        self.voices.append(voice)
        return {"voice": voice}

    def generate_audio_stream(self, state, text):
        import time
        for _ in range(self.blocks):
            time.sleep(self.delay)
            yield FakeTensor(2400)


@pytest.fixture(autouse=True)
def fake_pocket(monkeypatch):
    FakeModel.delay = 0.0
    FakeModel.blocks = 3
    FakeModel.voices = []
    monkeypatch.setitem(sys.modules, "pocket_tts", types.SimpleNamespace(TTSModel=FakeModel))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=lambda n: None))


def test_pocket_streams_float32_blocks_and_switches_voice_live():
    tts = pocket_backend.PocketTTS("fantine", temperature=0.5)
    assert FakeModel.temperature == 0.5
    blocks = list(tts.synthesize("Bonjour."))
    assert len(blocks) == 3
    assert all(b.dtype == np.float32 and b.ndim == 1 for b in blocks)
    tts.set_voice("cosette")
    assert tts.voice == "cosette"
    assert FakeModel.voices == ["fantine", "cosette"]


def test_runaway_generation_is_cut():
    FakeModel.blocks = 1000                       # 100 s d'audio pour « Oui. »
    audio = sum(len(b) for b in pocket_backend.PocketTTS("fantine").synthesize("Oui."))
    assert audio <= pocket_backend.max_samples("Oui.", 24000) + 2400


def test_too_slow_machine_is_detected():
    FakeModel.delay = 0.1                         # 0,3 s pour 0,3 s d'audio : facteur 1
    with pytest.raises(pocket_backend.TooSlow):
        pocket_backend.PocketTTS("fantine").warmup()


def test_auto_falls_back_to_piper(monkeypatch):
    built = []

    class FakePiper:
        name = "piper"
        sample_rate = 22050

        def __init__(self, path, length_scale):
            built.append(path)

        def warmup(self):
            pass

    import jarvis.tts.piper_backend as piper_backend
    monkeypatch.setattr(piper_backend, "PiperTTS", FakePiper)
    monkeypatch.setattr(app.assets, "piper_voice", lambda name: f"/modeles/{name}.onnx")
    cfg = Config()
    cfg.tts.voice = "inconnue"
    assert app.build_tts(cfg).name == "piper"
    assert built == ["/modeles/fr_FR-siwis-medium.onnx"]
    cfg.tts.backend = "pocket"
    with pytest.raises(RuntimeError, match="Pocket TTS"):
        app.build_tts(cfg)
