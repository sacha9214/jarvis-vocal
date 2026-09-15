"""Boucle vocale : mot d'activation → phrase → transcription → réponse parlée en flux.

Tout vise la latence perçue (fin de ta phrase → première syllabe) :
- fin de phrase détectée par un VAD neuronal, pas par un délai fixe ;
- modèles chargés et chauffés au démarrage, gardés en mémoire ;
- réflexes (heure, date, stop, bascule local/Claude) répondus sans LLM ;
- la réponse est synthétisée morceau par morceau pendant que le LLM écrit ;
- l'écoute continue pendant qu'il parle : « Hey Jarvis » lui coupe la parole.
"""
from __future__ import annotations

import difflib
import logging
import queue
import threading
from collections.abc import Iterator
from datetime import date

import numpy as np

from . import fastpath, prompts
from .app import Components
from .audio import SAMPLE_RATE, to_int16
from .audio.endpoint import UtteranceRecorder
from .audio.io import Microphone, Player
from .audio.vad import CHUNK
from .config import Config
from .llm.base import Delta, Done, Message, Notice
from .metrics import TurnTimer
from .stt import clean_transcript
from .text.chunker import SpeechChunker
from .tts import TextToSpeech

LOG = logging.getLogger("jarvis")
_ECHO_GUARD_S = 0.35   # après une réponse, ignore la réverbération de sa propre voix


class Speaker:
    """Fil de synthèse : reçoit des morceaux de texte et les joue dès qu'ils sont prêts."""

    def __init__(self, tts: TextToSpeech, player: Player, cancel: threading.Event, timer: TurnTimer):
        self._tts = tts
        self._player = player
        self._cancel = cancel
        self._timer = timer
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        self._queue.put(text)

    def close(self) -> None:
        self._queue.put(None)

    def finished(self) -> bool:
        return not self._thread.is_alive()

    def _run(self) -> None:
        while (text := self._queue.get()) is not None:
            if self._cancel.is_set():
                continue
            try:
                for block in self._tts.synthesize(text):
                    if self._cancel.is_set():
                        break
                    self._timer.mark("first_audio")
                    self._player.play(block, self._tts.sample_rate)
            except Exception:
                LOG.exception("Synthèse vocale en échec pour %r", text)


class Assistant:
    def __init__(self, cfg: Config, parts: Components, mic: Microphone, player: Player):
        self.cfg = cfg
        self.parts = parts
        self.mic = mic
        self.player = player
        self.recorder = UtteranceRecorder(parts.vad, cfg.vad)
        self.history: list[Message] = []
        self._last_reply = ""
        self._system = ""
        self._system_day: date | None = None

    def run(self) -> None:
        frames = self.mic.frames()
        wakeword = self.parts.wakeword
        LOG.info("À l'écoute : dis « Hey Jarvis ». %s", self.parts.llm.describe())
        for frame in frames:
            if wakeword.process(to_int16(frame)) >= wakeword.threshold:
                wakeword.reset()
                self._conversation(frames)
                wakeword.reset()
                LOG.info("En veille.")

    def _conversation(self, frames: Iterator[np.ndarray]) -> None:
        self.player.beep()
        timeout = self.cfg.vad.start_timeout_s
        while True:
            audio = self.recorder.record(frames, timeout)
            if audio is None:
                return
            timer = TurnTimer()
            text = clean_transcript(self.parts.stt.transcribe(audio))
            timer.mark("stt")
            if not text or self._is_echo(text):
                return
            LOG.info("🗣  %s", text)
            if fastpath.is_stop(text):
                self.player.stop()
                return
            interrupted = self._answer(text, timer, frames)
            LOG.info("⏱  %s", timer.summary())
            if interrupted:
                self.player.beep()
                timeout = self.cfg.vad.start_timeout_s
                continue
            if self.cfg.audio.follow_up_s <= 0:
                return
            self._skip(frames, _ECHO_GUARD_S)
            timeout = self.cfg.audio.follow_up_s

    def _reflex(self, text: str) -> str | None:
        if target := fastpath.switch_target(text):
            return self.parts.llm.switch(target)
        if fastpath.asks_engine(text):
            return self.parts.llm.describe()
        return fastpath.reply(text)

    def _answer(self, text: str, timer: TurnTimer, frames: Iterator[np.ndarray]) -> bool:
        """Répond en parlant ; renvoie True si « Hey Jarvis » a coupé la réponse."""
        cancel = threading.Event()
        speaker = Speaker(self.parts.tts, self.player, cancel, timer)
        parts: list[str] = []
        producer = None
        if quick := self._reflex(text):
            timer.mark("fastpath")
            parts.append(quick)
            speaker.say(quick)
            speaker.close()
        else:
            producer = threading.Thread(target=self._produce, name="llm", daemon=True,
                                        args=(self._messages(text), speaker, cancel, timer, parts))
            producer.start()
        interrupted = self._listen_while_speaking(frames, producer, speaker, cancel)
        if producer is not None:
            producer.join(timeout=5)
        reply = "".join(parts).strip()
        LOG.info("🤖 %s%s", reply, " [interrompu]" if interrupted else "")
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply or "…"}]
        self.history = self.history[-2 * self.cfg.llm.history_turns:]
        self._last_reply = reply
        return interrupted

    def _produce(self, messages: list[Message], speaker: Speaker, cancel: threading.Event,
                 timer: TurnTimer, parts: list[str]) -> None:
        chunker = SpeechChunker()
        try:
            for event in self.parts.llm.stream(messages, cancel=cancel):
                if isinstance(event, Delta):
                    timer.mark("llm_first_token")
                    parts.append(event.text)
                    for piece in chunker.feed(event.text):
                        timer.mark("first_chunk")
                        speaker.say(piece)
                elif isinstance(event, Notice):
                    speaker.say(event.text)
                elif isinstance(event, Done):
                    LOG.debug("LLM : prompt %d tokens, %d tokens produits",
                              event.prompt_tokens, event.output_tokens)
            for piece in chunker.flush():
                timer.mark("first_chunk")
                speaker.say(piece)
        except Exception as exc:
            LOG.error("LLM en échec : %s", exc)
            speaker.say("Désolé, le modèle ne répond pas.")
        finally:
            speaker.close()

    def _listen_while_speaking(self, frames: Iterator[np.ndarray], producer: threading.Thread | None,
                               speaker: Speaker, cancel: threading.Event) -> bool:
        wakeword = self.parts.wakeword
        for frame in frames:
            if wakeword.process(to_int16(frame)) >= wakeword.threshold:
                cancel.set()
                self.player.stop()
                wakeword.reset()
                return True
            if (producer is None or not producer.is_alive()) and speaker.finished() and self.player.idle():
                return False
        return False

    def _messages(self, text: str) -> list[Message]:
        today = date.today()
        if today != self._system_day:
            self._system = prompts.system_prompt(self.cfg.user_name, today)
            self._system_day = today
        return [{"role": "system", "content": self._system}, *self.history, {"role": "user", "content": text}]

    def _is_echo(self, text: str) -> bool:
        """Le micro a-t-il simplement réentendu la dernière réponse dans les haut-parleurs ?"""
        heard = fastpath.normalize(text)
        said = fastpath.normalize(self._last_reply)
        if len(heard) < 8 or not said:
            return False
        return heard in said or difflib.SequenceMatcher(None, heard, said).ratio() > 0.6

    @staticmethod
    def _skip(frames: Iterator[np.ndarray], seconds: float) -> None:
        for _ in range(round(seconds * SAMPLE_RATE / CHUNK)):
            if next(frames, None) is None:
                return
