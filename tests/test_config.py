import pytest

from jarvis import config, hardware

APPLE_16 = hardware.Hardware("darwin", "arm64", 16.0, True, None)
WINDOWS_RTX_8 = hardware.Hardware("win32", "x86_64", 32.0, False, 8.0)
WINDOWS_CPU = hardware.Hardware("win32", "x86_64", 16.0, False, None)


def test_apple_silicon_runs_everything_on_metal():
    cfg = config.Config().resolve(APPLE_16)
    assert (cfg.stt.backend, cfg.stt.model, cfg.stt.device) == ("mlx", hardware.MLX_WHISPER, "metal")
    assert cfg.llm.model == "qwen3.5:4b-mlx"


def test_windows_gpu_uses_cuda():
    cfg = config.Config().resolve(WINDOWS_RTX_8)
    assert (cfg.stt.backend, cfg.stt.device, cfg.stt.compute_type) == ("faster-whisper", "cuda", "int8_float16")
    assert cfg.llm.model == "qwen3.5:4b"


def test_windows_without_gpu_falls_back_to_small_models():
    cfg = config.Config().resolve(WINDOWS_CPU)
    assert (cfg.stt.model, cfg.stt.device, cfg.stt.compute_type) == ("small", "cpu", "int8")


def test_explicit_backend_gets_matching_defaults():
    cfg = config.Config()
    cfg.stt.backend = "faster-whisper"
    cfg.resolve(APPLE_16)
    assert (cfg.stt.model, cfg.stt.device, cfg.stt.compute_type) == ("small", "cpu", "int8")


def test_yaml_overrides_and_rejects_typos(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  model: qwen3.5:2b\nvad:\n  end_silence_ms: 400\n", encoding="utf-8")
    cfg = config.load(path).resolve(APPLE_16)
    assert cfg.llm.model == "qwen3.5:2b"
    assert cfg.vad.end_silence_ms == 400

    path.write_text("llm:\n  modle: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="llm.modle"):
        config.load(path)


def test_a_broken_config_file_says_what_and_where(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  backend: [ollama\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ligne 3 : ce n'est pas un fichier YAML valide"):
        config.load(path)
    path.write_text("- une liste\n", encoding="utf-8")
    with pytest.raises(ValueError, match="liste de réglages"):
        config.load(path)
