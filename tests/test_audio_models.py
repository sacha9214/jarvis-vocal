import numpy as np
import pytest

from jarvis import assets
from jarvis.audio.vad import CHUNK, SileroVad
from jarvis.audio.wakeword import FRAME, WakeWord

pytestmark = pytest.mark.network


def test_vad_stays_quiet_on_silence_and_noise():
    vad = SileroVad(assets.ensure(assets.SILERO_VAD))
    rng = np.random.default_rng(0)
    probs = [vad(np.zeros(CHUNK, np.float32)) for _ in range(20)]
    probs += [vad(rng.normal(0, 0.01, CHUNK).astype(np.float32)) for _ in range(20)]
    assert max(probs) < 0.3


def test_wakeword_ignores_silence():
    wakeword = WakeWord(*assets.wakeword("hey_jarvis"))
    for _ in range(50):
        score = wakeword.process(np.zeros(FRAME, np.int16))
    assert score < 0.1
