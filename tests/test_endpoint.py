from collections.abc import Iterator

import numpy as np

from jarvis.audio.endpoint import UtteranceRecorder
from jarvis.audio.vad import CHUNK
from jarvis.config import VadConfig


class ScriptedVad:
    """Faux VAD : la probabilité de parole est la valeur portée par la trame."""

    def reset(self) -> None:
        pass

    def __call__(self, frame: np.ndarray) -> float:
        return float(frame[0])


def frames(pattern: list[tuple[float, int]]) -> Iterator[np.ndarray]:
    for prob, count in pattern:
        for _ in range(count):
            yield np.full(CHUNK, prob, np.float32)


def test_captures_speech_with_preroll_and_stops_after_the_silence():
    cfg = VadConfig(end_silence_ms=320, preroll_ms=96)          # 10 et 3 trames de 32 ms
    stream = frames([(0.0, 20), (0.9, 30), (0.0, 50)])
    audio = UtteranceRecorder(ScriptedVad(), cfg).record(stream, start_timeout_s=5)
    assert audio is not None
    assert 30 <= len(audio) // CHUNK <= 30 + 3 + 5               # parole + pré-roll + traîne
    assert sum(1 for _ in stream) == 40                           # rien lu au-delà de la fin


def test_returns_none_when_nobody_speaks():
    assert UtteranceRecorder(ScriptedVad(), VadConfig()).record(frames([(0.0, 1000)]), 1.0) is None


def test_short_clicks_do_not_start_a_recording():
    stream = frames([(0.9, 2), (0.0, 100)] * 5)
    assert UtteranceRecorder(ScriptedVad(), VadConfig()).record(stream, 1.0) is None


def test_cancel_stops_waiting_immediately():
    class Cancelled:
        def is_set(self):
            return True

    assert UtteranceRecorder(ScriptedVad(), VadConfig()).record(frames([(0.9, 100)]), 30, cancel=Cancelled()) is None


def test_hysteresis_keeps_recording_through_soft_syllables():
    cfg = VadConfig(end_silence_ms=160)
    stream = frames([(0.9, 5), (0.4, 20), (0.9, 5), (0.0, 20)])
    audio = UtteranceRecorder(ScriptedVad(), cfg).record(stream, 5)
    assert audio is not None and len(audio) // CHUNK >= 30
