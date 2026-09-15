"""Interface commune des LLM : un flux d'événements, quel que soit le fournisseur."""
from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Done:
    prompt_tokens: int = 0
    prompt_ms: float = 0.0
    output_tokens: int = 0
    output_ms: float = 0.0
    load_ms: float = 0.0

    @property
    def tokens_per_s(self) -> float:
        return self.output_tokens / (self.output_ms / 1000) if self.output_ms else 0.0


Event = Delta | ToolCall | Done
Message = dict[str, Any]


class LanguageModel(Protocol):
    name: str
    model: str

    def check(self) -> None:
        """Lève RuntimeError avec un message actionnable si le modèle est inutilisable."""

    def warmup(self, system_prompt: str) -> None: ...

    def stream(self, messages: Sequence[Message], cancel: threading.Event | None = None) -> Iterator[Event]: ...
