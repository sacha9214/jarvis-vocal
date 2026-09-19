"""Choix de la sortie audio et du micro, de l'écran et de la taille de la fenêtre, terminal du journal.

PortAudio, pywebview et le terminal sont simulés : les runners de la CI n'ont ni carte son ni écran.
"""
import sys

import pytest

from jarvis.audio import devices, io
from jarvis.ui import log_terminal, schema, window

FAKE = [
    {"name": "Micro MacBook", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0,
     "default_samplerate": 48000.0},
    {"name": "Haut-parleurs", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2,
     "default_samplerate": 48000.0},
    {"name": "Casque AirPods", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 2,
     "default_samplerate": 24000.0},
    {"name": "Haut-parleurs", "hostapi": 1, "max_input_channels": 0, "max_output_channels": 2,   # WASAPI, doublon
     "default_samplerate": 48000.0},
]


class FakeDefault:
    hostapi = 0


class FakeStream:
    opened: list = []

    def __init__(self, device=None, **kwargs):
        self.device, self.closed, self.started = device, False, False
        FakeStream.opened.append(self)

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_audio(monkeypatch):
    def query(device=None, kind=None):
        if device is None and kind is None:
            return FAKE
        index = device if device is not None else (1 if kind == "output" else 0)
        return FAKE[index]

    for module in (devices.sd, io.sd):
        monkeypatch.setattr(module, "query_devices", query)
        monkeypatch.setattr(module, "default", FakeDefault)
        monkeypatch.setattr(module, "OutputStream", FakeStream)
        monkeypatch.setattr(module, "InputStream", FakeStream)
    FakeStream.opened = []


def test_each_output_is_listed_once(fake_audio):
    assert devices.names("output") == ["Haut-parleurs", "Casque AirPods"]
    assert devices.names("input") == ["Micro MacBook", "Casque AirPods"]


def test_a_name_opens_the_device_of_the_default_audio_api(fake_audio):
    assert devices.resolve("Haut-parleurs", "output") == 1      # pas le doublon WASAPI
    assert devices.resolve("", "output") is None                 # défaut du système
    assert devices.resolve("Casque débranché", "output") is None


def test_the_output_changes_without_restarting(fake_audio):
    player = io.Player(None, 24000)
    first = player._stream
    assert player.set_device("Casque AirPods") == "Casque AirPods"
    assert player._stream.device == 2 and player._stream.started and first.closed


def test_a_refused_output_keeps_the_old_one(fake_audio, monkeypatch):
    player = io.Player(None, 24000)
    first = player._stream

    class Refused(FakeStream):
        def start(self):
            raise RuntimeError("périphérique occupé")

    monkeypatch.setattr(io.sd, "OutputStream", Refused)
    with pytest.raises(RuntimeError):
        player.set_device("Casque AirPods")
    assert player._stream is first and not first.closed


def test_the_microphone_changes_and_follows_the_new_rate(fake_audio):
    mic = io.Microphone(None)
    assert mic.rate == 48000
    assert mic.set_device("Casque AirPods") == "Casque AirPods"
    assert mic.rate == 24000 and mic._stream.device == 2


def test_settings_list_what_is_plugged_in_now(fake_audio):
    field = schema.FIELDS["audio.output_device"]
    assert [v for v, _ in field.current_options()] == ["", "Haut-parleurs", "Casque AirPods"]
    schema.validate({"audio.output_device": "Casque AirPods"}, set())
    with pytest.raises(ValueError):
        schema.validate({"audio.output_device": "Enceinte inconnue"}, set())
    assert schema.is_live("audio.output_device") and schema.is_live("ui.show_logs")


class Screen:
    def __init__(self, width, height):
        self.width, self.height = width, height


def test_the_window_opens_on_the_chosen_screen_and_size(monkeypatch):
    screens = [Screen(1470, 956), Screen(2560, 1440)]
    assert window.window_options("2", "plein_ecran", screens) == {"fullscreen": True, "maximized": False,
                                                                 "screen": screens[1]}
    assert window.window_options("auto", "agrandie", screens) == {"fullscreen": False, "maximized": True}
    assert "screen" not in window.window_options("3", "fenetre", screens)     # débranché : écran principal


def test_screens_appear_in_the_settings(monkeypatch):
    monkeypatch.setattr(window, "SCREENS", [("1", "Écran 1 · 1470 × 956 · principal"), ("2", "Écran 2 · 2560 × 1440")])
    values = [v for v, _ in schema.FIELDS["ui.screen"].current_options()]
    assert values == ["auto", "1", "2"]
    assert not schema.is_live("ui.screen") and not schema.is_live("ui.display")


def test_the_log_terminal_follows_the_journal(tmp_path, monkeypatch):
    launched = []

    class FakeProcess:
        def __init__(self, command, creationflags=0):
            launched.append(command)
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.alive = False

    monkeypatch.setattr(log_terminal.subprocess, "Popen", FakeProcess)
    terminal = log_terminal.LogTerminal(tmp_path / "jarvis.log")
    terminal.show()
    command = " ".join(launched[0])
    if sys.platform == "win32":
        assert "Get-Content" in command and "-Wait" in command and str(tmp_path) in command
        terminal.show()
        assert len(launched) == 1                   # déjà ouvert : pas de deuxième fenêtre
        terminal.hide()
        assert not terminal.open
    elif sys.platform == "darwin":
        script = (tmp_path / "journal.command").read_text()
        assert launched[0][:3] == ["open", "-a", "Terminal"] and "tail -n 200 -F" in script
        terminal.hide()                             # sans tail vivant : ne plante pas
    assert (tmp_path / "jarvis.log").exists()
