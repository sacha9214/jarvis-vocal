"""Mot d'activation choisi librement : « Hey Friday », « Ok Maison », « Dis Alfred »…

Le modèle openWakeWord ne connaît que « Hey Jarvis » : en entraîner un pour un autre mot prend des heures.
Ici, le détecteur de voix repère une courte prise de parole (moins de trois secondes, suivie d'un silence),
la fait transcrire par le Whisper déjà chargé, amorcé avec le mot choisi, puis compare la transcription au mot,
à l'orthographe et au son près.

Mesuré sur 40 « Hey Friday / Ok Maison / Dis Alfred / Salut Karl / Hey Nova » (4 voix de synthèse) et 60 phrases
proches (« Il fait froid », « La maison », « Salut Carole »…) :
- sans amorce : 25 % de détection, 10 intempestifs sur 60 ; Whisper écrit « FID », « offret » pour un nom inconnu ;
- amorcé avec le mot : 52 % ; exiger la salutation, une phrase courte et un seuil de 0,9 : 52 %, 2 sur 60.
Les échecs restants sont des noms que la voix de synthèse rend inaudibles même sans amorce (« Ok Maison » → « Merci ») ;
les noms distinctifs de deux syllabes passent bien (« Hey Friday » 8/8, « Hey Nova » 6/8).
Moins fiable que le modèle dédié à « Hey Jarvis », qui reste utilisé tant que le mot choisi est « Jarvis ».

Coût : en silence, seulement le détecteur de voix, moins cher que le modèle « Hey Jarvis ». Chaque phrase
courte entendue coûte une transcription ; les longues (conversation, vidéo) sont ignorées.
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections import deque
from collections.abc import Callable

import numpy as np

from . import SAMPLE_RATE
from .vad import CHUNK

LOG = logging.getLogger("jarvis.wake")
DEFAULT_PHRASE = "hey jarvis"
MIN_SPEECH_S = 0.25
MAX_SPEECH_S = 3.0
END_SILENCE_S = 0.35
PREROLL_S = 0.3
START_FRAMES = 2
ON, OFF = 0.5, 0.35
THRESHOLD = 0.9           # ressemblance minimale : 0,78 laissait passer « Salut Carole » pour « Salut Karl »
NEAR = 0.55               # en dessous, le score reste à zéro : pas de « presque » sur n'importe quelle phrase
# Ce que Whisper écrit d'une salutation dite vite : « Hey » devient « Et », « High », « Hé ».
GREETING_LIKE = {"hey": {"hey", "he", "hei", "ey", "eh", "hi", "hay", "high", "bye", "et", "e", "a", "ai"},
                 "he": {"hey", "he", "hei", "ey", "eh", "et", "e"},
                 "ok": {"ok", "okay", "oke", "okey", "oh", "o", "ho"}, "okay": {"ok", "okay", "oh", "o"},
                 "dis": {"dis", "di", "dit", "dites"}, "salut": {"salut", "salu", "salue"}}
GREETINGS = {"hey", "he", "hei", "eh", "ey", "hé", "ok", "okay", "dis", "salut", "allo", "yo", "hi", "hello", "bonjour"}


def soft(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def sound(word: str) -> str:
    """Forme phonétique grossière du français : ce que Whisper écrit d'un nom inconnu varie, le son beaucoup moins."""
    w = soft(word).replace(" ", "")
    for pattern, replacement in ((r"ph", "f"), (r"qu", "k"), (r"c(?=[eiy])", "s"), (r"[cq]", "k"), (r"w", "v"),
                                 (r"y", "i"), (r"z", "s"), (r"x", "ks"), (r"gn", "n"), (r"h", ""),
                                 (r"(?:eau|au)", "o"), (r"(?:ai|ei|et|er|ez)", "e"), (r"ou", "u"), (r"ea|ee", "i"),
                                 (r"(\w)\1+", r"\1"), (r"(?<=\w)[stdxe]$", "")):
        w = re.sub(pattern, replacement, w)
    return w


def core_words(phrase: str) -> list[str]:
    words = soft(phrase).split()
    core = [w for w in words if w not in GREETINGS]
    return core or words


def validate(phrase: str) -> str:
    """Le mot d'activation normalisé, ou ValueError avec la raison."""
    words = core_words(phrase)
    if not words:
        raise ValueError("écris le mot qui réveillera Jarvis, par exemple « Hey Friday »")
    if len("".join(words)) < 4:
        raise ValueError(f"« {phrase} » est trop court : il serait reconnu dans n'importe quelle phrase. "
                         "Prends un nom d'au moins deux syllabes")
    if len(words) > 3:
        raise ValueError(f"« {phrase} » est trop long pour un mot d'activation : trois mots au plus")
    return " ".join(soft(phrase).split())


def is_builtin(phrase: str) -> bool:
    """« Hey Jarvis » garde le modèle dédié : plus rapide, et il reconnaît sans transcrire."""
    return core_words(phrase) == ["jarvis"]


def similarity(heard: str, phrase: str) -> float:
    """Ressemblance (0..1) entre le début de la transcription et le mot choisi."""
    core = core_words(phrase)
    words = soft(heard).split()
    while words and words[0] in GREETINGS:        # « Hey Friday » comme « Friday »
        words = words[1:]
    if not words:
        return 0.0
    wanted = " ".join(core)
    best = 0.0
    for size in {len(core), max(1, len(core) - 1), len(core) + 1}:
        for start in range(0, min(2, len(words))):
            window = " ".join(words[start:start + size])
            if not window:
                continue
            spelled = difflib.SequenceMatcher(None, wanted, window).ratio()
            heard_sound, wanted_sound = sound(window), sound(wanted)
            sounded = difflib.SequenceMatcher(None, wanted_sound, heard_sound).ratio() if wanted_sound else 0.0
            best = max(best, spelled, sounded)
    return best


def match(heard: str, phrase: str) -> float:
    """Score de réveil : la ressemblance, à condition que la phrase entendue soit le mot seul (« Hey Friday »,
    pas « Hey Friday, ouvre Spotify » ni « ça va Friday ») et commence par la salutation choisie s'il y en a une."""
    words, wanted = soft(heard).split(), soft(phrase).split()
    if not words or not wanted or len(words) > len(wanted) + 1:
        return 0.0
    if wanted[0] in GREETINGS and wanted[0] not in core_words(phrase):
        if words[0] not in GREETING_LIKE.get(wanted[0], {wanted[0]}):
            return 0.0
    return similarity(heard, phrase)


class SpokenWakeWord:
    """Même interface que WakeWord : process(trame int16) → score, threshold, score, reset()."""

    def __init__(self, phrase: str, vad, transcribe: Callable[[np.ndarray, str], str], threshold: float = THRESHOLD):
        self.phrase = validate(phrase)
        self.hint = " ".join(phrase.split()).strip(" .!?,") + "."    # amorce : le mot tel qu'il est écrit
        self.vad = vad
        self.transcribe = transcribe
        self.threshold = threshold
        self.heard = ""                 # dernière transcription, pour le journal
        self.transcriptions = 0         # combien de fois Whisper a été appelé (coût)
        frames_per_s = SAMPLE_RATE / CHUNK
        self._preroll = deque(maxlen=max(1, round(PREROLL_S * frames_per_s)))
        self._end_frames = max(1, round(END_SILENCE_S * frames_per_s))
        self._max_frames = round(MAX_SPEECH_S * frames_per_s)
        self._min_frames = max(1, round(MIN_SPEECH_S * frames_per_s))
        self.reset()

    def reset(self) -> None:
        self.vad.reset()
        self._preroll.clear()
        self._speech: list[np.ndarray] = []
        self._run = self._silence = self._voiced = 0
        self._too_long = False
        self.score = 0.0

    def process(self, samples: np.ndarray) -> float:
        frame = samples.astype(np.float32) / 32768.0 if samples.dtype == np.int16 else samples.astype(np.float32)
        if len(frame) != CHUNK:
            return self.score
        self.score = 0.0
        probability = self.vad(frame)
        if not self._speech:
            self._preroll.append(frame)
            self._run = self._run + 1 if probability >= ON else 0
            if self._run >= START_FRAMES:
                self._speech = list(self._preroll)
                self._silence, self._voiced = 0, self._run
            return self.score
        self._speech.append(frame)
        voiced = probability >= OFF
        self._silence = 0 if voiced else self._silence + 1
        self._voiced += voiced
        if len(self._speech) > self._max_frames:
            self._too_long = True       # une conversation, pas un appel : on attend qu'elle se taise
        if self._silence < self._end_frames:
            return self.score
        spoken = self._voiced             # le pré-roulement et les silences ne comptent pas : un clic reste un clic
        audio, too_long = np.concatenate(self._speech), self._too_long
        self._speech, self._run, self._too_long = [], 0, False
        self._preroll.clear()
        if too_long or spoken < self._min_frames:
            return self.score
        self.transcriptions += 1
        try:
            self.heard = self.transcribe(audio, self.hint)
        except Exception as exc:  # noqa: BLE001 - une transcription ratée ne doit pas couper l'écoute
            LOG.debug("Transcription du mot d'activation impossible : %s", exc)
            return self.score
        value = match(self.heard, self.phrase)
        self.score = value if value >= NEAR else 0.0
        if value >= self.threshold:
            LOG.info("Mot d'activation « %s » reconnu dans « %s » (%.2f)", self.phrase, self.heard.strip(), value)
        return self.score
