"""Détection du matériel et choix des modèles qui tiennent la latence sur cette machine."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

MLX_WHISPER = "mlx-community/whisper-large-v3-turbo-q4"


@dataclass(frozen=True)
class Hardware:
    os: str                      # "darwin" | "win32" | "linux"
    arch: str                    # "arm64" | "x86_64"
    ram_gb: float
    apple_silicon: bool
    cuda_vram_gb: float | None   # None sans GPU NVIDIA

    def __str__(self) -> str:
        gpu = ("Apple Silicon (mémoire unifiée)" if self.apple_silicon
               else f"NVIDIA {self.cuda_vram_gb:.0f} Go" if self.cuda_vram_gb else "CPU seul")
        return f"{self.os}/{self.arch}, {self.ram_gb:.0f} Go RAM, {gpu}"


@dataclass(frozen=True)
class Plan:
    stt_backend: str   # "mlx" | "faster-whisper"
    stt_model: str
    stt_device: str    # "metal" | "cuda" | "cpu"
    stt_compute: str
    llm_model: str
    reason: str


def _ram_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (OSError, ValueError, AttributeError):
        return 8.0


def _cuda_vram_gb() -> float | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        values = [float(v) for v in out.split()]
        return max(values) / 1024 if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def detect() -> Hardware:
    arch = platform.machine().lower()
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(arch, arch)
    return Hardware(
        os=sys.platform,
        arch=arch,
        ram_gb=round(_ram_gb(), 1),
        apple_silicon=sys.platform == "darwin" and arch == "arm64",
        cuda_vram_gb=_cuda_vram_gb(),
    )


def recommend(hw: Hardware) -> Plan:
    """Le plus gros modèle qui garde une réponse fluide, sans faire swapper la machine."""
    if hw.apple_silicon:
        # Mesuré sur M5 16 Go : turbo-q4 transcrit comme turbo, un peu plus vite, avec ~1 Go de
        # moins en mémoire unifiée ; les variantes -mlx d'Ollama pré-remplissent le prompt plus vite.
        size = "9b" if hw.ram_gb >= 24 else "4b" if hw.ram_gb >= 12 else "2b"
        return Plan("mlx", MLX_WHISPER, "metal", "float16", f"qwen3.5:{size}-mlx",
                    "Apple Silicon : Whisper et LLM sur le GPU Metal")
    if hw.cuda_vram_gb:
        vram = hw.cuda_vram_gb
        if vram >= 10:
            return Plan("faster-whisper", "large-v3-turbo", "cuda", "float16", "qwen3.5:9b",
                        f"GPU {vram:.0f} Go : Whisper turbo + LLM 9B tiennent en VRAM")
        if vram >= 6:
            return Plan("faster-whisper", "large-v3-turbo", "cuda", "int8_float16", "qwen3.5:4b",
                        f"GPU {vram:.0f} Go : Whisper turbo int8 + LLM 4B")
        return Plan("faster-whisper", "small", "cuda", "int8_float16", "qwen3.5:2b",
                    f"GPU {vram:.0f} Go : petits modèles pour rester en VRAM")
    llm = "qwen3.5:4b" if hw.ram_gb >= 16 else "qwen3.5:2b"
    return Plan("faster-whisper", "small", "cpu", "int8", llm,
                "Pas de GPU : modèles réduits, latence plus élevée")
