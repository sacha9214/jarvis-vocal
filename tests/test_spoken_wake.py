"""Mot d'activation personnalisé : découpage des phrases courtes, amorce, et règles de reconnaissance.

Whisper et le détecteur de voix sont simulés : le test tourne à l'identique sur la CI macOS et Windows.
Les taux de détection réels (Whisper sur voix de synthèse) sont dans le docstring de spoken_wake.
"""
import numpy as np
import pytest

from jarvis.app import custom_wake_phrase
from jarvis.audio.spoken_wake import SpokenWakeWord, is_builtin, match, validate
from jarvis.audio.vad import CHUNK
from jarvis.config import Config
from jarvis.ui import schema


class FakeVad:
    """Parole tant que la trame n'est pas nulle."""

    def __call__(self, frame):
        return 0.9 if np.abs(frame).max() > 0 else 0.0

    def reset(self):
        pass


def speech(seconds):
    return [np.full(CHUNK, 3000, np.int16)] * round(seconds * 16000 / CHUNK)


def silence(seconds):
    return [np.zeros(CHUNK, np.int16)] * round(seconds * 16000 / CHUNK)


def listen(detector, frames):
    return max((detector.process(frame) for frame in frames), default=0.0)


@pytest.mark.parametrize(("heard", "phrase"), [
    ("Hey Friday.", "Hey Friday"), ("High Friday.", "Hey Friday"), ("Bye Friday.", "Hey Friday"),
    ("Et Nova.", "Hey Nova"),
    ("Salut Karl.", "Salut Karl"), ("Dis Alfred !", "Dis Alfred"), ("Hé, Friday !", "Hey Friday"),
])
def test_the_chosen_word_is_recognised(heard, phrase):
    assert match(heard, phrase) >= 0.9


@pytest.mark.parametrize(("heard", "phrase"), [
    ("Salut Carole !", "Salut Karl"),            # proche, mais pas le nom
    ("La maison.", "Ok Maison"),                 # le nom sans la salutation choisie
    ("Ça va Friday.", "Hey Friday"),
    ("Hey Friday, ouvre Spotify et monte le son.", "Hey Friday"),   # une phrase entière, pas un appel
    ("Il fait froid.", "Hey Friday"),
    ("", "Hey Friday"),
])
def test_close_sentences_do_not_wake_it(heard, phrase):
    assert match(heard, phrase) < 0.9


def test_a_short_call_followed_by_silence_is_transcribed_with_the_word_as_hint():
    calls = []

    def transcribe(audio, hint):
        calls.append((len(audio), hint))
        return "Hey Friday."

    detector = SpokenWakeWord("hey  friday", FakeVad(), transcribe)
    assert listen(detector, silence(0.5) + speech(0.8) + silence(0.5)) >= detector.threshold
    assert len(calls) == 1 and calls[0][1] == "hey friday."
    assert calls[0][0] >= 0.8 * 16000          # la parole entière, pré-roulement compris


def test_long_speech_and_clicks_cost_no_transcription():
    detector = SpokenWakeWord("Hey Friday", FakeVad(), lambda audio, hint: pytest.fail("transcription inutile"))
    assert listen(detector, speech(5) + silence(0.6) + speech(0.1) + silence(0.6)) == 0.0
    assert detector.transcriptions == 0


def test_a_failing_transcription_does_not_stop_listening():
    answers = iter([RuntimeError("Whisper indisponible"), "Hey Nova."])

    def transcribe(audio, hint):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    detector = SpokenWakeWord("Hey Nova", FakeVad(), transcribe)
    assert listen(detector, speech(0.8) + silence(0.5)) == 0.0
    assert listen(detector, speech(0.8) + silence(0.5)) >= detector.threshold


@pytest.mark.parametrize("phrase", ["", "Hey", "Ok Al", "dis moi tout de suite là"])
def test_unusable_words_are_refused_with_a_reason(phrase):
    with pytest.raises(ValueError):
        validate(phrase)


def test_hey_jarvis_keeps_the_dedicated_model():
    assert is_builtin("Hey Jarvis") and is_builtin("jarvis") and not is_builtin("Hey Friday")
    cfg = Config()
    assert custom_wake_phrase(cfg) is None
    cfg.wakeword.phrase = "Hey Friday"
    assert custom_wake_phrase(cfg) == "Hey Friday"
    cfg.wakeword.phrase = "Hé"                  # invalide à la main dans config.yaml : « Hey Jarvis » reste actif
    assert custom_wake_phrase(cfg) is None


def test_the_setting_is_validated_before_being_saved():
    schema.validate({"wakeword.phrase": "Hey Friday"}, set())
    with pytest.raises(ValueError, match="Mot d'activation"):
        schema.validate({"wakeword.phrase": "Yo"}, set())
    assert not schema.is_live("wakeword.phrase")
