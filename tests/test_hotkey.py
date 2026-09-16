"""Raccourci clavier global : réveiller Jarvis sans parler.

Sous Windows, le test enregistre vraiment le raccourci auprès du système puis lui envoie le message qu'envoie
Windows à l'appui des touches : c'est la vraie boucle de messages qui est vérifiée, sur la CI.
"""
import sys
import threading
import time

import pytest

from jarvis.system import hotkey


@pytest.mark.parametrize(("combo", "expected"), [
    ("ctrl+alt+j", (["ctrl", "alt"], "j")),
    ("Control + Option + J", (["ctrl", "alt"], "j")),
    ("ctrl+shift+space", (["ctrl", "shift"], "space")),
    ("cmd+f5", (["cmd"], "f5")),
])
def test_shortcuts_are_understood(combo, expected):
    assert hotkey.parse(combo) == expected


@pytest.mark.parametrize("combo", ["j", "ctrl+alt", "ctrl+alt+bidule", ""])
def test_invalid_shortcuts_are_refused_with_a_reason(combo):
    with pytest.raises(ValueError, match="raccourci"):
        hotkey.parse(combo)


def test_the_shortcut_wakes_jarvis_like_the_wake_word():
    """Pendant qu'il parle, le raccourci le coupe comme « Hey Jarvis » (drapeau lu dans l'écoute pendant la parole)."""
    import inspect

    from jarvis.pipeline import Assistant

    source = inspect.getsource(Assistant._listen_while_speaking)
    assert "self.manual_wake.is_set() or wakeword.process" in source
    assert "self.manual_wake.clear()" in source


def test_a_failing_reaction_does_not_kill_the_shortcut():
    calls = []

    def boom():
        calls.append(True)
        raise RuntimeError("réaction cassée")

    shortcut = hotkey.Hotkey("ctrl+alt+j", boom)
    shortcut._fire()
    shortcut._fire()
    assert calls == [True, True]


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey : Windows")
def test_windows_registers_the_shortcut_and_reacts_to_it():
    import ctypes

    pressed = threading.Event()
    shortcut = hotkey.Hotkey("ctrl+alt+shift+f11", pressed.set).start()
    try:
        if not shortcut.active:
            pytest.skip(f"raccourci indisponible sur cette machine : {shortcut.error}")
        # Le message qu'envoie Windows quand la combinaison est pressée, déposé dans la file du fil d'écoute.
        assert ctypes.windll.user32.PostThreadMessageW(shortcut._thread_id, 0x0312, 1, 0)
        assert pressed.wait(5)
        taken = hotkey.Hotkey("ctrl+alt+shift+f11", lambda: None).start()   # déjà pris : refus explicite
        assert not taken.active and "déjà pris" in taken.error
    finally:
        shortcut.stop()
    deadline = time.monotonic() + 5
    while shortcut.active and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not shortcut.active


@pytest.mark.skipif(sys.platform != "darwin", reason="event tap Quartz : macOS")
def test_macos_listens_or_explains_the_missing_permission():
    shortcut = hotkey.Hotkey("ctrl+alt+shift+j", lambda: None).start()
    try:
        assert shortcut.active or "Surveillance de l'entrée" in shortcut.error
    finally:
        shortcut.stop()
