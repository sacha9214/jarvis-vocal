"""Boucle vocale : mot d'activation → phrase → transcription → réponse parlée en flux.

Tout vise la latence perçue (fin de ta phrase → première syllabe) :
- fin de phrase détectée par un VAD neuronal, pas par un délai fixe ;
- modèles chargés et chauffés au démarrage, gardés en mémoire ;
- réflexes (heure, date, stop, bascule local/Claude) et commandes du PC sans LLM ;
- la réponse est synthétisée morceau par morceau pendant que le LLM écrit ;
- l'écoute continue pendant qu'il parle : « Hey Jarvis » lui coupe la parole.

Un seul fil lit le micro. Les confirmations demandées par un outil, les annonces des
minuteurs et les boutons de l'interface, qui viennent d'autres fils, lui arrivent par une file
ou un drapeau.
"""
from __future__ import annotations

import difflib
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from . import commands, fastpath, prompts
from .app import Components
from .audio import SAMPLE_RATE, to_int16
from .audio.endpoint import UtteranceRecorder
from .audio.io import Microphone, Player
from .audio.vad import CHUNK
from .config import Config
from .events import EventBus
from .llm.base import Delta, Done, Message, Notice, ToolCall
from .metrics import TurnTimer
from .stt import clean_transcript
from .text.chunker import SpeechChunker
from .tools import ALWAYS, NO, YES, ToolResult
from .tools.builtin import POWER, TIMERS
from .tts import TextToSpeech
from .vision.screen import Observation

LOG = logging.getLogger("jarvis")
_ECHO_GUARD_S = 0.35   # après une réponse, ignore la réverbération de sa propre voix
_CONTINUATION_S = 1.5  # attente de la suite d'une phrase inachevée (« Ouvre… euh… »)
_LEVEL_EVERY = 3       # trames entre deux niveaux audio publiés (~10 par seconde)
_MAX_TOOL_ROUNDS = 3   # lire la page, puis agir, puis répondre : au-delà, le petit modèle tourne en rond
_NEAR_MISS_RATIO = 0.6    # score atteint, en part du seuil, à partir duquel on signale un « presque »
_NEAR_MISS_EVERY_S = 20.0


@dataclass
class _Request:
    kind: str                       # "confirm" | "say"
    text: str
    allow_always: bool = False
    answer: str = NO
    done: threading.Event = field(default_factory=threading.Event)


class Speaker:
    """Fil de synthèse : reçoit des morceaux de texte et les joue dès qu'ils sont prêts."""

    def __init__(self, tts: TextToSpeech, player: Player, cancel: threading.Event, timer: TurnTimer,
                 on_start: Callable[[], None] | None = None):
        self._tts = tts
        self._player = player
        self._cancel = cancel
        self._timer = timer
        self._on_start = on_start
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
        started = False
        while (text := self._queue.get()) is not None:
            if self._cancel.is_set():
                continue
            try:
                for block in self._tts.synthesize(text):
                    if self._cancel.is_set():
                        break
                    self._timer.mark("first_audio")
                    if not started:
                        started = True
                        if self._on_start:
                            self._on_start()
                    self._player.play(block, self._tts.sample_rate)
            except Exception:
                LOG.exception("Synthèse vocale en échec pour %r", text)


class Assistant:
    def __init__(self, cfg: Config, parts: Components, mic: Microphone, player: Player,
                 bus: EventBus | None = None):
        self.cfg = cfg
        self.parts = parts
        self.mic = mic
        self.player = player
        self.bus = bus or EventBus()
        self.recorder = UtteranceRecorder(parts.vad, cfg.vad)
        self.history: list[Message] = []
        self.manual_wake = threading.Event()
        self._stop_requested = threading.Event()
        self._current_state = "sleeping"
        self._last_reply = ""
        self._system = ""
        self._system_key: tuple | None = None
        self._requests: queue.Queue[_Request] = queue.Queue()
        self._main_thread: int | None = None
        self._frames: Iterator[np.ndarray] | None = None
        self._frame_count = 0
        self._decision: str | None = None
        self._decided = threading.Event()
        self._cache_stale = False           # l'analyse d'écran ou la review a évincé la conversation d'Ollama
        self._warm_context: str | None = None   # contexte d'outils dont le cache d'Ollama contient le prompt
        self._last_near_miss = 0.0
        self._warming = threading.Lock()
        parts.executor.confirm = self._confirm
        parts.executor.on_result = self._on_tool_result
        TIMERS.announce = self.announce
        POWER.announce = self.announce
        if parts.screen is not None:
            # L'analyse d'écran attend que la conversation soit finie : elle ne ralentit jamais une réponse.
            parts.screen.busy = lambda: self._current_state != "sleeping"
            parts.screen.on_observation = self._on_screen
        automations = getattr(parts, "automations", None)
        if automations is not None:
            automations.run_action = self._run_automation
        if parts.review is not None:
            parts.review.announce = self.announce
            parts.review.busy = lambda: self._current_state != "sleeping"
            parts.review.on_local_done = self._rewarm

    # -- appelés depuis d'autres fils (outils, minuteurs, interface)

    def announce(self, text: str) -> None:
        self._requests.put(_Request("say", text))

    def decide(self, decision: str) -> None:
        self._decision = decision
        self._decided.set()

    def wake(self) -> None:
        self.manual_wake.set()

    def stop(self) -> None:
        """Bouton Stop : coupe la voix et termine la conversation en cours."""
        self._stop_requested.set()
        self.player.stop()

    # -- boucle principale

    def run(self) -> None:
        self._main_thread = threading.get_ident()
        if hasattr(self.mic, "on_lost"):
            self.mic.on_lost = lambda: self.bus.publish(
                "error", text="Le micro ne répond plus : vérifie qu'il est branché et qu'aucune autre "
                              "application ne l'utilise. Je réessaie tout seul.")
        frames = self._frames = self.mic.frames()
        wakeword = self.parts.wakeword
        self._publish_engine()
        self._state("sleeping")
        if self.parts.screen is not None:
            self.parts.screen.start()
        if getattr(self.parts, "automations", None) is not None:
            self.parts.automations.start()      # après le branchement de run_action : « au démarrage » part ici
        if getattr(self.parts, "agenda", None) is not None:
            self.parts.agenda.announce = self.announce
            self.parts.agenda.start()
        LOG.info("À l'écoute : dis « Hey Jarvis ». %s", self.parts.llm.describe())
        for frame in frames:
            if self._tick(frame, frames):
                self._state("sleeping")
            self._near_miss(wakeword)
            if self.manual_wake.is_set() or wakeword.process(to_int16(frame)) >= wakeword.threshold:
                LOG.debug("Mot d'activation : score %.2f (seuil %.2f)", wakeword.score, wakeword.threshold)
                self.manual_wake.clear()
                self._stop_requested.clear()
                wakeword.reset()
                self._prewarm()
                try:
                    self._conversation(frames)
                except Exception:  # noqa: BLE001 - une conversation ratée ne doit jamais tuer l'écoute
                    LOG.exception("Conversation interrompue par une erreur")
                    self.bus.publish("error", text="Erreur pendant la conversation, détail dans le journal.")
                    self.player.stop()
                wakeword.reset()
                self._stop_requested.clear()
                self._state("sleeping")
                self._health()
                LOG.info("En veille.")

    def _conversation(self, frames: Iterator[np.ndarray]) -> None:
        self.player.beep()
        timeout = self.cfg.vad.start_timeout_s
        while True:
            self._state("listening")
            audio = self.recorder.record(frames, timeout, cancel=self._stop_requested, on_frame=self._level)
            if audio is None:
                return
            timer = TurnTimer()
            self._state("thinking")
            raw = self.parts.stt.transcribe(audio)
            if fastpath.looks_unfinished(raw):
                # « Ouvre… euh… » : on laisse finir la phrase, puis on retranscrit le tout.
                self._state("listening")
                more = self.recorder.record(frames, _CONTINUATION_S, cancel=self._stop_requested,
                                            on_frame=self._level)
                if more is not None:
                    self._state("thinking")
                    raw = self.parts.stt.transcribe(np.concatenate([audio, more]))
            text = clean_transcript(raw)
            timer.mark("stt")
            if not text or self._is_echo(text):
                return
            LOG.info("🗣  %s", text)
            self.bus.publish("user", text=text)
            if fastpath.is_stop(text):
                self.player.stop()
                return
            interrupted = self._answer(text, timer, frames)
            LOG.info("⏱  %s", timer.summary())
            self.bus.publish("metrics", marks={name: round(s * 1000) for name, s in timer.marks.items()})
            if self._stop_requested.is_set():
                return
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
            reply = self.parts.llm.switch(target)
            self._publish_engine()
            return reply
        if fastpath.asks_engine(text):
            return self.parts.llm.describe()
        if quick := fastpath.reply(text):
            return quick
        if not self.cfg.tools.enabled:
            return None
        executor = self.parts.executor
        custom = getattr(executor, "custom", None)
        if custom and (command := custom.match(text)):          # tes phrases passent avant les règles
            return executor.run(command.tool, command.arguments)
        if command := commands.parse(text, self._context()):
            return executor.run(command.tool, command.arguments)
        return None

    def _answer(self, text: str, timer: TurnTimer, frames: Iterator[np.ndarray]) -> bool:
        """Répond en parlant ; renvoie True si « Hey Jarvis » a coupé la réponse."""
        cancel = threading.Event()
        speaker = Speaker(self.parts.tts, self.player, cancel, timer, on_start=lambda: self._state("speaking"))
        parts: list[str] = []
        producer = None
        if (quick := self._reflex(text)) is not None:
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
        self.bus.publish("reply", text=reply, interrupted=interrupted)
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply or "…"}]
        self.history = self.history[-2 * self.cfg.llm.history_turns:]
        self._last_reply = reply
        return interrupted

    def _produce(self, messages: list[Message], speaker: Speaker, cancel: threading.Event,
                 timer: TurnTimer, parts: list[str]) -> None:
        chunker = SpeechChunker()

        def say(piece: str) -> None:
            timer.mark("first_chunk")
            speaker.say(piece)

        executor = self.parts.executor
        context = self._context() if self.cfg.tools.enabled else ""
        tools = executor.schemas(context) if self.cfg.tools.enabled else None
        self._warm_context = context        # la réponse laisse ce prompt-là dans le cache
        conversation = list(messages)
        try:
            for _ in range(_MAX_TOOL_ROUNDS):
                data_calls: list[ToolCall] = []
                written: list[str] = []
                for event in self.parts.llm.stream(conversation, cancel=cancel, tools=tools):
                    if isinstance(event, Delta):
                        timer.mark("llm_first_token")
                        parts.append(event.text)
                        written.append(event.text)
                        for piece in chunker.feed(event.text):
                            say(piece)
                    elif isinstance(event, ToolCall):
                        for piece in chunker.flush():
                            say(piece)
                        if executor.speaks(event.name):
                            result = executor.run(event.name, event.arguments)
                            timer.mark("tool")
                            parts.append((" " if parts else "") + result)
                            say(result)
                        else:
                            data_calls.append(event)
                    elif isinstance(event, Notice):
                        speaker.say(event.text)
                    elif isinstance(event, Done):
                        LOG.debug("LLM : prompt %d tokens, %d tokens produits", event.prompt_tokens,
                                  event.output_tokens)
                for piece in chunker.flush():
                    say(piece)
                if not data_calls or cancel.is_set():
                    break
                # Outils de données (page, application, code) : le modèle lit le résultat, puis répond ou agit.
                conversation.append({"role": "assistant", "content": "".join(written), "tool_calls": [
                    {"function": {"name": call.name, "arguments": call.arguments}} for call in data_calls]})
                for call in data_calls:
                    conversation.append({"role": "tool", "tool_name": call.name,
                                         "content": executor.run(call.name, call.arguments)})
                    timer.mark("tool")
        except Exception as exc:
            LOG.error("LLM en échec : %s", exc)
            self.bus.publish("error", text=str(exc))
            speaker.say("Désolé, le modèle ne répond pas.")
        finally:
            speaker.close()

    def _listen_while_speaking(self, frames: Iterator[np.ndarray], producer: threading.Thread | None,
                               speaker: Speaker, cancel: threading.Event) -> bool:
        wakeword = self.parts.wakeword
        for frame in frames:
            self._tick(frame, frames)
            if self._stop_requested.is_set():
                cancel.set()
                self.player.stop()
                return False
            if self.manual_wake.is_set() or wakeword.process(to_int16(frame)) >= wakeword.threshold:
                self.manual_wake.clear()        # raccourci clavier ou bouton : comme « Hey Jarvis »
                cancel.set()
                self.player.stop()
                wakeword.reset()
                return True
            if (producer is None or not producer.is_alive()) and speaker.finished() and self.player.idle():
                return False
        return False

    # -- confirmations et annonces

    def _confirm(self, question: str, allow_always: bool) -> str:
        if threading.get_ident() == self._main_thread and self._frames is not None:
            return self._ask(self._frames, question, allow_always)
        request = _Request("confirm", question, allow_always)
        self._requests.put(request)
        if not request.done.wait(self.cfg.tools.confirm_timeout_s + 60):
            return NO
        return request.answer

    def _ask(self, frames: Iterator[np.ndarray], question: str, allow_always: bool) -> str:
        self._decision = None
        self._decided.clear()
        self._state("confirm")
        self.bus.publish("confirm", question=question, always=allow_always)
        LOG.info("❓ %s", question)
        self._say(frames, question)
        if self._decision is None:
            audio = self.recorder.record(frames, self.cfg.tools.confirm_timeout_s, cancel=self._decided,
                                         on_frame=self._level)
            if self._decision is None and audio is not None:
                heard = clean_transcript(self.parts.stt.transcribe(audio))
                LOG.info("🗣  %s", heard)
                self._decision = fastpath.confirmation(heard)
        decision = self._decision or NO
        if decision == ALWAYS and not allow_always:
            decision = YES
        self.bus.publish("confirm_done", decision=decision)
        self._state("thinking")
        return decision

    def _say(self, frames: Iterator[np.ndarray], text: str) -> None:
        """Dit une phrase hors réponse (question, annonce) en continuant de lire le micro."""
        self._state("speaking")
        for block in self.parts.tts.synthesize(text):
            self.player.play(block, self.parts.tts.sample_rate)
        for frame in frames:
            self._level(frame)
            if self.player.idle() or self._decided.is_set():
                break
        self._skip(frames, _ECHO_GUARD_S)

    def _tick(self, frame: np.ndarray, frames: Iterator[np.ndarray]) -> bool:
        """Publie le niveau audio et traite les demandes des autres fils ; True s'il y en avait."""
        self._level(frame)
        if self._requests.empty():
            return False
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return True
            try:
                if request.kind == "say":
                    self.bus.publish("announce", text=request.text)
                    self._say(frames, request.text)
                else:
                    request.answer = self._ask(frames, request.text, request.allow_always)
            except Exception:  # noqa: BLE001 - voix ou micro en échec : on répond « non » et on continue
                LOG.exception("Demande d'un autre fil en échec (%s)", request.kind)
            finally:
                request.done.set()

    # -- divers

    def _level(self, frame: np.ndarray) -> None:
        self._frame_count += 1
        if self._frame_count % _LEVEL_EVERY == 0:
            self.bus.publish("levels", mic=round(float(np.sqrt(np.mean(frame * frame))), 4),
                             out=round(float(getattr(self.player, "level", 0.0)), 4))

    def _state(self, name: str) -> None:
        self._current_state = name
        self.bus.publish("state", state=name)

    def _publish_engine(self) -> None:
        self.bus.publish("engine", active=self.parts.llm.active, model=self.parts.llm.model)

    def _context(self) -> str:
        """Contexte d'outils : navigateur, éditeur de code, ou aucun (application utilisée avant Jarvis)."""
        return self.parts.foreground.context() if self.parts.foreground is not None else ""

    def _on_tool_result(self, result: ToolResult) -> None:
        self.bus.publish("tool", name=result.name, arguments=result.arguments, level=result.level,
                         text=result.text, allowed=result.allowed, ms=round(result.ms))

    def _on_screen(self, observation: Observation) -> None:
        self.bus.publish("screen", text=observation.text, app=observation.app, at=observation.at)
        self._rewarm()

    def _rewarm(self) -> None:
        # L'analyse d'écran ou la review vient de remplacer la conversation dans le cache d'Ollama. On ne
        # rechauffe pas tout de suite (mesuré : 1,6 à 2,3 s de GPU à chaque fois, pour rien si personne ne
        # parle) mais au prochain « Hey Jarvis », pendant que la phrase est prononcée.
        self._cache_stale = True

    def _prewarm(self) -> None:
        """Au réveil : chauffe le cache avec le prompt et les outils du contexte courant, pendant que la
        phrase est prononcée. Mesuré : avec une autre liste d'outils, la question paie ~1,5 s de plus."""
        context = self._context() if self.cfg.tools.enabled else ""
        if self.parts.llm.active != "local" or (not self._cache_stale and context == self._warm_context):
            return
        if not self._warming.acquire(blocking=False):
            return                      # une rechauffe est déjà en cours
        self._cache_stale = False
        self._warm_context = context
        system = self._system_prompt()
        tools = self.parts.executor.schemas(context) if self.cfg.tools.enabled else None

        def warm() -> None:
            try:
                self.parts.llm.warmup(system, tools)
            except Exception as exc:  # noqa: BLE001 - la question suivante paiera simplement le prompt
                LOG.debug("Rechauffe impossible : %s", exc)
            finally:
                self._warming.release()
        threading.Thread(target=warm, name="rechauffe", daemon=True).start()

    def _run_automation(self, automation) -> None:
        """Appelé par le fil des automatisations. Les actions sensibles gardent leur confirmation."""
        if automation.say:
            self.announce(automation.say)
            return
        command = commands.parse(automation.do, self._context())
        if command is None:
            self.announce(f"Je n'ai pas pu faire « {automation.do} » : je ne comprends plus cette commande.")
            return

        def act() -> None:
            result = self.parts.executor.run(command.tool, command.arguments)
            if self.parts.executor.speaks(command.tool) and result:
                self.announce(result)
        threading.Thread(target=act, name="automatisation", daemon=True).start()

    def _near_miss(self, wakeword) -> None:
        """« Presque » : le mot a été reconnu à moitié. Le dire aide à régler la sensibilité au lieu de
        répéter dans le vide."""
        if not self.cfg.wakeword.near_miss or wakeword.score < wakeword.threshold * _NEAR_MISS_RATIO:
            return
        now = time.monotonic()
        if now - self._last_near_miss < _NEAR_MISS_EVERY_S:
            return
        self._last_near_miss = now
        LOG.info("👂 J'ai cru entendre « Hey Jarvis » (score %.2f, seuil %.2f) : baisse la sensibilité dans les "
                 "réglages si ça se répète.", wakeword.score, wakeword.threshold)
        self.bus.publish("near_miss", score=round(float(wakeword.score), 2), threshold=wakeword.threshold)

    def _health(self) -> None:
        """Une ligne de journal par conversation : ce qu'il faut pour comprendre un ralentissement ou un plantage."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            rss = psutil.Process().memory_info().rss
            LOG.info("💾 Jarvis %.1f Go, libre %.1f Go, swap %.1f Go, trames micro perdues %d, fils %d",
                     rss / 1e9, memory.available / 1e9, swap.used / 1e9, getattr(self.mic, "dropped", 0),
                     threading.active_count())
        except Exception:  # noqa: BLE001 - diagnostic seulement
            pass

    def _system_prompt(self) -> str:
        key = (date.today(), self.cfg.user_name, self.cfg.tools.enabled)
        if key != self._system_key:
            self._system = prompts.system_prompt(self.cfg.user_name, key[0], tools=self.cfg.tools.enabled)
            self._system_key = key
        return self._system

    def _messages(self, text: str) -> list[Message]:
        messages: list[Message] = [{"role": "system", "content": self._system_prompt()}, *self.history]
        # Contexte écran et souvenirs juste avant la question : le début du prompt reste identique (cache KV).
        if self.parts.screen is not None and (context := self.parts.screen.context()):
            messages.append({"role": "system", "content": context})
        memory = getattr(self.parts, "memory", None)
        if memory is not None and (remembered := memory.relevant(text, self.cfg.user_name or "l'utilisateur")):
            messages.append({"role": "system", "content": remembered})
        return [*messages, {"role": "user", "content": text}]

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
