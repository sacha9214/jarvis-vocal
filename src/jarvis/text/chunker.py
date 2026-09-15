"""Découpe le flux du LLM en morceaux prononçables, le plus tôt possible.

Le premier morceau part dès la première ponctuation forte, ou dès une virgule passé
FIRST_MIN_CHARS : c'est lui qui fixe le silence avant la première syllabe. Les suivants
attendent une fin de phrase, pour garder une intonation naturelle.
"""
from __future__ import annotations

import re

FIRST_MIN_CHARS = 20
MAX_CHARS = 200      # garde-fou si le modèle n'emploie aucune ponctuation

_STRONG = re.compile(r"[.!?…;:]+[\"»”)]*(?=\s)|\n+")
_WEAK = re.compile(r",(?=\s)")
_ABBREVIATIONS = {"m", "mm", "mme", "mmes", "mlle", "dr", "pr", "st", "ste", "cf", "ex", "p", "vol", "n°", "no"}
_MARKDOWN = re.compile(r"[*_#`>|~]+")
_BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_SPACES = re.compile(r"\s+")
_WORD = re.compile(r"\w")


def clean_for_speech(text: str) -> str:
    text = _BULLET.sub("", text)
    text = _MARKDOWN.sub(" ", text)
    text = _EMOJI.sub("", text)
    return _SPACES.sub(" ", text).strip()


class SpeechChunker:
    def __init__(self) -> None:
        self._buffer = ""
        self._first = True

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        pieces = []
        while (cut := self._find_cut()) is not None:
            piece, self._buffer = self._buffer[:cut], self._buffer[cut:]
            if piece := self._speakable(piece):
                pieces.append(piece)
                self._first = False
        return pieces

    def flush(self) -> list[str]:
        piece, self._buffer = self._speakable(self._buffer), ""
        return [piece] if piece else []

    @staticmethod
    def _speakable(text: str) -> str:
        text = clean_for_speech(text)
        return text if _WORD.search(text) else ""

    def _find_cut(self) -> int | None:
        for match in _STRONG.finditer(self._buffer):
            if not self._is_abbreviation(match.start()):
                return match.end()
        if self._first:
            for match in _WEAK.finditer(self._buffer):
                if match.end() >= FIRST_MIN_CHARS:
                    return match.end()
        if len(self._buffer) > MAX_CHARS:
            space = self._buffer.rfind(" ", 0, MAX_CHARS)
            return space if space > 0 else MAX_CHARS
        return None

    def _is_abbreviation(self, index: int) -> bool:
        if self._buffer[index] != ".":
            return False
        word = re.search(r"(\S+)$", self._buffer[:index])
        return bool(word) and word.group(1).lower().lstrip("(«\"") in _ABBREVIATIONS
