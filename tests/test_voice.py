import numpy as np
import pytest

from jarvis import fastpath
from jarvis.stt import echoes_prompt, is_repetitive, prepare_audio, suspicious, vocabulary_prompt
from jarvis.system import apps
from jarvis.system.apps import App

APPS = tuple(App(name, f"/Applications/{name}.app", key) for name, key in [
    ("Spotify", "spotify"), ("Discord", "discord"), ("Blender", "blender"), ("Notes", "notes"),
])


def test_vocabulary_prompt_lists_each_app_once():
    prompt = vocabulary_prompt(["Obsidian", "Spotify"])
    assert prompt.startswith("Jarvis")
    assert prompt.count("Spotify") == 1
    assert "Obsidian" in prompt


def test_prompt_echo_is_dropped_but_short_commands_are_kept():
    prompt = vocabulary_prompt(["Obsidian"])
    assert echoes_prompt("Monte le volume, mets pause, verrouille l'écran", prompt)
    assert not echoes_prompt("Passe sur Claude", prompt)
    assert not echoes_prompt("Raconte-moi une histoire de pirates", prompt)


def test_decoding_loops_are_detected():
    assert is_repetitive("Voici un moment de vue de la voie" + " de la voie" * 30)
    assert not is_repetitive("Allume la lumière du salon et mets une ambiance calme pour ce soir.")
    assert suspicious("Merci d'avoir regardé cette vidéo !", vocabulary_prompt([]))
    assert not suspicious("Ouvre Spotify.", vocabulary_prompt([]))


def test_quiet_audio_is_amplified_and_loud_audio_untouched():
    quiet = np.full(1600, 0.05, np.float32)
    assert np.isclose(np.max(prepare_audio(quiet)), 0.4)
    loud = np.full(1600, 0.6, np.float32)
    assert np.array_equal(prepare_audio(loud), loud)


@pytest.mark.parametrize(("text", "unfinished"), [
    ("Ouvre...", True), ("Mets de la musique et", True), ("Lance le", True),
    ("Ouvre Spotify.", False), ("Quelle heure est-il ?", False),
])
def test_unfinished_sentences(text, unfinished):
    assert fastpath.looks_unfinished(text) is unfinished


@pytest.mark.parametrize(("heard", "expected"), [
    ("Spotifaille", "Spotify"), ("Discorde", "Discord"), ("Blendeur", "Blender"), ("Spoon", None),
])
def test_app_names_are_matched_by_sound(heard, expected):
    app = apps.find_app(heard, APPS)
    assert (app.name if app else None) == expected
