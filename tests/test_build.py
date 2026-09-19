"""Exécutable autonome : ligne de commande PyInstaller et comportement une fois empaqueté.

La vraie construction (5 à 15 minutes) tourne dans le workflow « Build » sur Windows, qui lance ensuite
l'autotest dans l'exécutable produit.
"""
import sys
from pathlib import Path

from jarvis import build
from jarvis.system import autostart


def test_the_bundle_is_a_windowless_folder_with_the_runtime_data(tmp_path):
    args = build.arguments(tmp_path, tmp_path / "build", platform=sys.platform)
    assert "--windowed" in args and "--onefile" not in args      # dossier : pas 2 Go décompressés par lancement
    collected = {args[i + 1] for i, a in enumerate(args) if a == "--collect-all"}
    assert {"jarvis", "pocket_tts", "piper", "onnxruntime", "webview"} <= collected
    if sys.platform == "win32":
        assert {"faster_whisper", "ctranslate2", "uiautomation"} <= collected
    assert args[args.index("--distpath") + 1] == str(tmp_path / "dist")


def test_the_entry_point_survives_a_windowless_launch():
    assert "multiprocessing.freeze_support()" in build.ENTRY
    assert "sys.stdout = open(os.devnull" in build.ENTRY


def test_selftest_passes_from_the_repository():
    assert build.selftest() == 0


def test_autostart_launches_the_executable_itself_once_built(monkeypatch, tmp_path):
    exe = tmp_path / "Jarvis.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert autostart.launch_command() == [str(exe)]
    assert autostart.project_dir() == Path(exe).resolve().parent


def test_the_console_accepts_emoji_even_in_cp1252(monkeypatch):
    import io

    from jarvis import __main__ as entry
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    entry._utf8_console()
    print("✅ prêt, cœur")
    console.flush()
    assert raw.getvalue().decode("utf-8").startswith("✅ prêt, cœur")
