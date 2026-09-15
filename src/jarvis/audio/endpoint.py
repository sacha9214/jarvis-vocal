"""Capture d'une phrase : début et fin détectés par le VAD, pas par un délai fixe."""
from __future__ import annotations

from collections import deque
from collections.abc import Iterator

import numpy as np

from ..config import VadConfig
from . import SAMPLE_RATE
from .vad import CHUNK, SileroVad

_SPEECH_START_FRAMES = 3   # ~100 ms de parole avant de déclarer un début (ignore les clics)
_TAIL_S = 0.15             # silence gardé après la dernière syllabe


class UtteranceRecorder:
    def __init__(self, vad: SileroVad, cfg: VadConfig):
        self.vad = vad
        self.cfg = cfg

    def record(self, frames: Iterator[np.ndarray], start_timeout_s: float) -> np.ndarray | None:
        """Consomme les trames jusqu'à la fin de la phrase. None si personne ne parle."""
        cfg = self.cfg
        frame_s = CHUNK / SAMPLE_RATE
        preroll = deque(maxlen=max(1, round(cfg.preroll_ms / 1000 / frame_s)) + _SPEECH_START_FRAMES)
        end_frames = max(1, round(cfg.end_silence_ms / 1000 / frame_s))
        max_frames = round(cfg.max_utterance_s / frame_s)
        start_limit = max(1, round(start_timeout_s / frame_s))
        tail = max(1, round(_TAIL_S / frame_s))
        off_threshold = max(0.05, cfg.threshold - 0.15)   # hystérésis : pas de coupure sur un souffle

        self.vad.reset()
        audio: list[np.ndarray] = []
        waited = speech_run = silence = 0
        for frame in frames:
            prob = self.vad(frame)
            if not audio:
                preroll.append(frame)
                waited += 1
                speech_run = speech_run + 1 if prob >= cfg.threshold else 0
                if speech_run >= _SPEECH_START_FRAMES:
                    audio = list(preroll)
                elif waited >= start_limit:
                    return None
                continue
            audio.append(frame)
            silence = 0 if prob >= off_threshold else silence + 1
            if silence >= end_frames:
                return np.concatenate(audio[:len(audio) - silence + tail])
            if len(audio) >= max_frames:
                return np.concatenate(audio)
        return np.concatenate(audio) if audio else None
