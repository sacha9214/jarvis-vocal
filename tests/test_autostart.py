"""Démarrage à l'ouverture de session.

Le point vérifié ici n'est pas seulement que ça marche : c'est que **rien ne s'installe
sans un geste explicite**. Jarvis écoute un micro ; un programme pareil ne se met pas tout
seul au démarrage de la machine.
"""
import plistlib
import sys

import pytest

from jarvis import config as config_module
from jarvis.events import EventBus
from jarvis.system import autostart
from jarvis.ui import schema
from jarvis.ui.controller import Controller


def test_off_by_default():
    assert config_module.Config().ui.autostart is False


def test_the_setting_exists_and_is_a_toggle():
    field = schema.FIELDS["ui.autostart"]
    assert field.type == "toggle"
    assert field.live is True


@pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent : macOS")
def test_mac_writes_then_removes_the_launch_agent(tmp_path):
    plist = autostart.mac_plist_path(tmp_path)
    assert autostart.is_enabled(tmp_path) is False

    autostart.enable(tmp_path)
    assert plist.exists()
    assert autostart.is_enabled(tmp_path) is True

    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == autostart.LABEL
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"][-2:] == ["-m", "jarvis"]
    # Fermé par l'utilisateur, il reste fermé : sinon la machine le relance en boucle.
    assert data["KeepAlive"] is False

    autostart.disable(tmp_path)
    assert not plist.exists()
    assert autostart.is_enabled(tmp_path) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent : macOS")
def test_disabling_twice_says_so_instead_of_failing(tmp_path):
    assert "pas activé" in autostart.disable(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="clé Run : Windows")
def test_windows_registry_value_is_written_then_removed():
    import winreg
    name = "JarvisTest"
    try:
        assert autostart.is_enabled(value_name=name) is False
        autostart.enable(value_name=name)
        assert autostart.is_enabled(value_name=name) is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, name)
        assert "-m" in value and "jarvis" in value
        autostart.disable(value_name=name)
        assert autostart.is_enabled(value_name=name) is False
    finally:
        try:
            autostart.disable(value_name=name)
        except OSError:
            pass


# -- le chemin qui compte : le réglage de l'interface

def _controller(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "LOADED_PATH", tmp_path / "config.yaml")
    return Controller(config_module.Config(), EventBus())


def test_the_toggle_installs_only_when_the_user_turns_it_on(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: bool(calls and calls[-1] == "enable"))
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: calls.append("enable") or "ok")
    monkeypatch.setattr(autostart, "disable", lambda *a, **k: calls.append("disable") or "ok")
    controller = _controller(tmp_path, monkeypatch)

    controller.update_config({"ui": {"hotkey": "ctrl+alt+k"}})
    assert calls == []                                   # un autre réglage ne touche à rien

    controller.update_config({"ui": {"autostart": True}})
    assert calls == ["enable"]

    controller.update_config({"ui": {"autostart": True}})
    assert calls == ["enable"]                           # déjà installé : on n'y retouche pas

    controller.update_config({"ui": {"autostart": False}})
    assert calls == ["enable", "disable"]


def test_a_system_refusal_is_not_written_to_the_config(tmp_path, monkeypatch):
    """Si l'installation échoue, config.yaml ne doit pas prétendre le contraire."""
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein")))
    controller = _controller(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="disque plein"):
        controller.update_config({"ui": {"autostart": True}})
    assert not (tmp_path / "config.yaml").exists()
    assert controller.cfg.ui.autostart is False


def test_the_displayed_state_follows_the_system_not_the_file(tmp_path, monkeypatch):
    """Le LaunchAgent peut être retiré hors de Jarvis : la case doit le refléter."""
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: True)
    controller = _controller(tmp_path, monkeypatch)
    controller.cfg.ui.autostart = False
    assert controller.state()["config"]["ui"]["autostart"] is True
