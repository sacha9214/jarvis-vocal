"""Entrées/sorties audio temps réel (PortAudio via sounddevice), Windows et macOS."""
from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from collections.abc import Callable, Iterator

import numpy as np
import sounddevice as sd
import soxr

from . import SAMPLE_RATE
from .devices import resolve
from .vad import CHUNK

LOG = logging.getLogger("jarvis.audio")
_DEAF_AFTER = 6        # attentes de 0,5 s sans une seule trame avant de déclarer le micro perdu (3 s)
_RETRY_EVERY = 10      # une tentative de réouverture toutes les 5 s ensuite


class Microphone:
    """Capture au taux natif du micro (pas tous acceptent 16 kHz), rééchantillonnée en flux
    à 16 kHz et découpée en trames de 32 ms. Le callback ne fait que copier : le travail
    lourd se fait côté consommateur pour ne jamais perdre d'audio."""

    def __init__(self, device: int | str | None = None, frame: int = CHUNK):
        self.on_lost: Callable[[], None] | None = None   # prévenir l'interface et le journal
        self._frame = frame
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._select(device)
        self._stream = self._open()
        self.dropped = 0

    def _select(self, device: int | str | None) -> None:
        self._device = resolve(device, "input")
        info = sd.query_devices(self._device, "input")
        self.name = info["name"]
        self.rate = int(info["default_samplerate"])
        self._resampler = (None if self.rate == SAMPLE_RATE
                           else soxr.ResampleStream(self.rate, SAMPLE_RATE, 1, dtype="float32"))

    def _open(self) -> sd.InputStream:
        return sd.InputStream(device=self._device, channels=1, samplerate=self.rate, dtype="float32",
                              blocksize=int(self.rate * 0.02), latency="low", callback=self._callback)

    def set_device(self, device: int | str | None) -> str:
        """Change de micro sans redémarrer ; renvoie le nom du micro ouvert."""
        previous = (self._device, self.name, self.rate, self._resampler)
        self._select(device)
        if not self._reopen():
            self._device, self.name, self.rate, self._resampler = previous
            self._reopen()
            raise RuntimeError("ce micro ne s'ouvre pas, l'ancien est gardé")
        LOG.info("Micro : %s (%d Hz → 16 kHz)", self.name, self.rate)
        return self.name

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.dropped += 1

    def _reopen(self) -> bool:
        """Rouvre le flux : un micro débranché puis rebranché, ou un périphérique endormi, revient tout seul."""
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001 - le flux est déjà perdu
            pass
        # Surtout ne pas réinitialiser PortAudio (sd._terminate) : mesuré, cela invalide aussi le flux de
        # sortie, et Jarvis perdrait la voix en essayant de retrouver l'oreille.
        try:
            self._stream = self._open()
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - toujours pas de micro : on réessaiera
            LOG.debug("Micro toujours indisponible : %s", exc)
            return False
        LOG.info("Micro retrouvé : %s", self.name)
        return True

    def __enter__(self) -> Microphone:
        self._stream.start()
        LOG.info("Micro : %s (%d Hz → 16 kHz)", self.name, self.rate)
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def frames(self) -> Iterator[np.ndarray]:
        pending = np.zeros(0, np.float32)
        silent = 0
        while True:
            try:
                # timeout : garde Ctrl+C réactif (un get() bloquant l'ignore sous Windows)
                block = self._queue.get(timeout=0.5)
                silent = 0
            except queue.Empty:
                # Plus rien n'arrive : micro débranché, session verrouillée, périphérique pris par une autre
                # application. Sans cela, Jarvis restait sourd sans jamais le dire.
                silent += 1
                if silent == _DEAF_AFTER:
                    LOG.warning("Le micro « %s » ne donne plus rien : je tente de le rouvrir.", self.name)
                    if self.on_lost:
                        self.on_lost()
                if silent >= _DEAF_AFTER and silent % _RETRY_EVERY == 0 and self._reopen():
                    silent = 0
                continue
            if self._resampler is not None:
                block = self._resampler.resample_chunk(block)
            pending = np.concatenate([pending, block])
            while len(pending) >= self._frame:
                yield pending[:self._frame]
                pending = pending[self._frame:]


class Player:
    """Sortie audio ouverte en permanence (évite ~100 ms d'ouverture à chaque réponse)
    et interruptible : stop() vide le tampon, la voix se coupe dans les 20 ms."""

    def __init__(self, device: int | str | None = None, sample_rate: int = 22050):
        self.rate = sample_rate
        self.level = 0.0          # niveau RMS de la sortie, pour l'interface
        self._chunks: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._device = resolve(device, "output")
        self.name = sd.query_devices(self._device, "output")["name"]
        self._stream = self._open()

    def _open(self) -> sd.OutputStream:
        return sd.OutputStream(device=self._device, channels=1, samplerate=self.rate,
                               dtype="float32", latency="low", callback=self._callback)

    def set_device(self, device: int | str | None) -> str:
        """Change de sortie sans redémarrer (la phrase en cours reprend sur la nouvelle) ; renvoie son nom."""
        target = resolve(device, "output")
        stream = sd.OutputStream(device=target, channels=1, samplerate=self.rate,
                                 dtype="float32", latency="low", callback=self._callback)
        stream.start()                     # la nouvelle d'abord : si elle refuse, l'ancienne continue
        old, self._stream, self._device = self._stream, stream, target
        self.name = sd.query_devices(target, "output")["name"]
        try:
            old.stop()
            old.close()
        except Exception:  # noqa: BLE001 - l'ancienne sortie a pu disparaître (casque débranché)
            pass
        LOG.info("Sortie audio : %s", self.name)
        return self.name

    def __enter__(self) -> Player:
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def play(self, audio: np.ndarray, rate: int) -> None:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if rate != self.rate:
            audio = soxr.resample(audio, rate, self.rate)
        with self._lock:
            self._chunks.append(audio)
            self._idle.clear()

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._idle.set()

    def idle(self) -> bool:
        return self._idle.is_set()

    def beep(self, freq: float = 880.0, duration: float = 0.07, volume: float = 0.2) -> None:
        t = np.arange(int(self.rate * duration)) / self.rate
        tone = (np.sin(2 * np.pi * freq * t) * volume).astype(np.float32)
        fade = min(len(tone) // 4, int(self.rate * 0.01))
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        tone[:fade] *= ramp
        tone[-fade:] *= ramp[::-1]
        self.play(tone, self.rate)

    def _callback(self, outdata, frames, time_info, status) -> None:
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames and self._chunks:
                chunk = self._chunks[0]
                n = min(frames - filled, len(chunk))
                out[filled:filled + n] = chunk[:n]
                filled += n
                if n == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[n:]
            if not self._chunks:
                self._idle.set()
        out[filled:] = 0
        self.level = float(np.sqrt(np.mean(out * out))) if frames else 0.0
