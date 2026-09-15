"""LLM local via Ollama, réglé pour le temps de première réponse."""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import ollama

from ..config import LlmConfig
from .base import Delta, Done, Event, Message, ToolCall

LOG = logging.getLogger("jarvis.llm")


class OllamaLLM:
    name = "ollama"

    def __init__(self, cfg: LlmConfig):
        self.cfg = cfg
        self.model = cfg.model
        self._client = ollama.Client(host=cfg.host, timeout=httpx.Timeout(120.0, connect=3.0))
        # Pas de « réflexion » à voix haute : un modèle qui pense 300 tokens avant de parler
        # ajoute des secondes de silence.
        self._think: bool | None = False

    def _options(self, **overrides) -> dict:
        return {"num_ctx": self.cfg.num_ctx, "num_predict": self.cfg.max_tokens,
                "temperature": self.cfg.temperature, **overrides}

    def check(self) -> None:
        try:
            installed = {m.model for m in self._client.list().models}
        except (httpx.HTTPError, ConnectionError) as exc:
            raise RuntimeError(
                f"Ollama ne répond pas sur {self.cfg.host} ({exc}). Lance l'application Ollama "
                "ou `ollama serve`.") from exc
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in installed:
            raise RuntimeError(f"Modèle {self.model} absent. Installe-le : `ollama pull {self.model}` "
                               "(ou `jarvis setup`).")

    def warmup(self, system_prompt: str, tools: list[dict[str, Any]] | None = None) -> None:
        """Charge le modèle en mémoire et pré-remplit le cache KV du prompt système et des
        outils : les questions suivantes ne recalculent que leurs propres tokens."""
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "Bonjour"}]
        for _ in self.stream(messages, tools=tools, max_tokens=1):
            pass

    def stream(self, messages: Sequence[Message], cancel: threading.Event | None = None,
               tools: list[dict[str, Any]] | None = None, max_tokens: int | None = None) -> Iterator[Event]:
        options = self._options(**({"num_predict": max_tokens} if max_tokens else {}))
        chunks = self._client.chat(model=self.model, messages=list(messages), tools=tools or None, stream=True,
                                   think=self._think, options=options, keep_alive=self.cfg.keep_alive)
        try:
            for chunk in chunks:
                if cancel is not None and cancel.is_set():
                    return   # fermer le flux HTTP arrête la génération côté Ollama
                message = chunk.message
                if message.content:
                    yield Delta(message.content)
                for call in message.tool_calls or []:
                    yield ToolCall(call.function.name, dict(call.function.arguments or {}))
                if chunk.done:
                    yield Done(
                        prompt_tokens=chunk.prompt_eval_count or 0,
                        prompt_ms=(chunk.prompt_eval_duration or 0) / 1e6,
                        output_tokens=chunk.eval_count or 0,
                        output_ms=(chunk.eval_duration or 0) / 1e6,
                        load_ms=(chunk.load_duration or 0) / 1e6,
                    )
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
