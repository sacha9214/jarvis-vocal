"""Choix du micro et de la sortie audio par leur nom.

Le nom plutôt que le numéro : les numéros de PortAudio changent dès qu'un casque est branché ou débranché.
Sous Windows, chaque périphérique apparaît une fois par API audio (MME, DirectSound, WASAPI, WDM-KS) :
on ne propose que ceux de l'API par défaut, celle qu'utilise déjà Jarvis, pour n'avoir chaque sortie qu'une fois.
"""
from __future__ import annotations

import logging

import sounddevice as sd

LOG = logging.getLogger("jarvis.audio")
DEFAULT = ""          # le périphérique par défaut du système, qui suit ses changements


def _channels(kind: str) -> str:
    return "max_input_channels" if kind == "input" else "max_output_channels"


def names(kind: str) -> list[str]:
    """Les périphériques d'entrée (« input ») ou de sortie (« output ») branchés, sans doublon."""
    try:
        devices = sd.query_devices()
        hostapi = sd.default.hostapi
    except Exception as exc:  # noqa: BLE001 - PortAudio indisponible : le réglage garde « par défaut »
        LOG.debug("Liste des périphériques audio impossible : %s", exc)
        return []
    found: list[str] = []
    for device in devices:
        if device[_channels(kind)] > 0 and (hostapi < 0 or device["hostapi"] == hostapi):
            if device["name"] not in found:
                found.append(device["name"])
    return found


def resolve(device: int | str | None, kind: str) -> int | str | None:
    """Ce que PortAudio doit ouvrir : None (défaut du système), un numéro, ou le numéro du nom choisi.
    Un nom qui n'est plus branché retombe sur le défaut du système au lieu d'empêcher Jarvis de démarrer."""
    if device is None or device == DEFAULT:
        return None
    if isinstance(device, int) or (isinstance(device, str) and device.isdigit()):
        return int(device)
    try:
        devices = sd.query_devices()
        hostapi = sd.default.hostapi
    except Exception:  # noqa: BLE001
        return None
    matches = [i for i, d in enumerate(devices) if d["name"] == device and d[_channels(kind)] > 0]
    preferred = [i for i in matches if devices[i]["hostapi"] == hostapi]
    if preferred or matches:
        return (preferred or matches)[0]
    LOG.warning("%s « %s » introuvable (débranché ?) : périphérique par défaut du système.",
                "Micro" if kind == "input" else "Sortie audio", device)
    return None
