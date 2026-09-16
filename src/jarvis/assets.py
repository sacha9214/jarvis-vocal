"""Téléchargement vérifié (SHA-256) des petits modèles : VAD, mot d'activation, voix."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from .paths import models_dir

LOG = logging.getLogger("jarvis.assets")


@dataclass(frozen=True)
class Asset:
    path: str                  # relatif au dossier des modèles
    url: str
    sha256: str | None = None


_OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
_PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

SILERO_VAD = Asset(
    "silero_vad.onnx",
    "https://github.com/snakers4/silero-vad/raw/v6.2.1/src/silero_vad/data/silero_vad.onnx",
    "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
)
MELSPEC = Asset("openwakeword/melspectrogram.onnx", _OWW + "melspectrogram.onnx",
                "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f")
EMBEDDING = Asset("openwakeword/embedding_model.onnx", _OWW + "embedding_model.onnx",
                  "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f")
WAKEWORDS = {
    "hey_jarvis": Asset("openwakeword/hey_jarvis_v0.1.onnx", _OWW + "hey_jarvis_v0.1.onnx",
                        "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"),
}
_PIPER_SHA256 = {
    "fr_FR-siwis-medium.onnx": "641d1ab097da2b81128c076810edb052b385decc8be3381814802a64a73baf99",
    "fr_FR-siwis-medium.onnx.json": "39479916c2db192b5ac9764daddd0c744d83e023ad890c6976c0633ae4df8959",
    "fr_FR-tom-medium.onnx": "bf65074ccdeeeeaa832e75edb1c0a513c01c9a972bdf085ff8a6e71ea234fd41",
    "fr_FR-tom-medium.onnx.json": "2f7f885ad5a0aad802e3cc24e4f57239febdcb142b4876de5d238094674361cc",
}


def ensure(asset: Asset) -> Path:
    dest = models_dir() / asset.path
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Nom propre au processus : deux téléchargements en parallèle ne s'écrasent pas.
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    digest = hashlib.sha256()
    LOG.info("Téléchargement de %s…", asset.path)
    try:
        with httpx.stream("GET", asset.url, follow_redirects=True,
                          timeout=httpx.Timeout(60.0, connect=10.0)) as response:
            response.raise_for_status()
            with tmp.open("wb") as file:
                for chunk in response.iter_bytes(1 << 16):
                    file.write(chunk)
                    digest.update(chunk)
    except BaseException:          # coupure réseau, Ctrl+C : ne pas laisser de fichier à moitié écrit
        tmp.unlink(missing_ok=True)
        raise
    if asset.sha256 and digest.hexdigest() != asset.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{asset.path} : empreinte SHA-256 inattendue, fichier rejeté.")
    if not asset.sha256:
        LOG.warning("%s : pas d'empreinte connue, fichier non vérifié.", asset.path)
    tmp.replace(dest)
    return dest


def piper_voice(name: str) -> Path:
    """« fr_FR-siwis-medium » → télécharge le modèle et sa config, renvoie le .onnx."""
    try:
        lang, speaker, quality = name.split("-")
    except ValueError:
        raise ValueError(f"Nom de voix Piper invalide : {name} (ex. fr_FR-siwis-medium)") from None
    remote = f"{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}"
    for suffix in (".onnx.json", ".onnx"):
        ensure(Asset(f"piper/{name}{suffix}", _PIPER + remote + suffix, _PIPER_SHA256.get(name + suffix)))
    return models_dir() / "piper" / f"{name}.onnx"


def wakeword(name: str) -> tuple[Path, Path, Path]:
    if name not in WAKEWORDS:
        raise ValueError(f"Mot d'activation inconnu : {name} (disponibles : {', '.join(WAKEWORDS)})")
    return ensure(MELSPEC), ensure(EMBEDDING), ensure(WAKEWORDS[name])
