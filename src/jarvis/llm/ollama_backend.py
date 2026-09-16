"""LLM local via Ollama, réglé pour le temps de première réponse."""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import ollama

from ..config import LlmConfig
from .base import Delta, Done, Event, Message, ToolCall

LOG = logging.getLogger("jarvis.llm")


def pick_model(installed: list[str]) -> str:
    """Sur un serveur distant sans modèle imposé : le plus gros qwen3.5 présent, sinon le premier modèle."""
    if not installed:
        raise RuntimeError("Aucun modèle sur ce serveur Ollama : `ollama pull qwen3.5:4b` dessus.")
    qwen = [m for m in installed if m.lower().startswith("qwen3.5")]
    if not qwen:
        return installed[0]
    def size(name: str) -> float:
        match = re.search(r":(\d+(?:\.\d+)?)b", name.lower())
        return float(match.group(1)) if match else 0.0
    return max(qwen, key=size)


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

    def set_host(self, host: str) -> None:
        self.cfg.host = host
        self._client = ollama.Client(host=host, timeout=httpx.Timeout(120.0, connect=3.0))

    def check(self) -> None:
        try:
            installed = [m.model for m in self._client.list().models]
        except (httpx.HTTPError, ConnectionError) as exc:
            if self.cfg.remote:
                raise RuntimeError(
                    f"Ollama ne répond pas sur {self.cfg.host} ({exc}). Sur cette machine, Ollama doit écouter sur "
                    "le réseau : variable OLLAMA_HOST=0.0.0.0, puis relance-le (et ouvre le port 11434 "
                    "dans son pare-feu).") from exc
            raise RuntimeError(
                f"Ollama ne répond pas sur {self.cfg.host} ({exc}). Lance l'application Ollama "
                "ou `ollama serve`.") from exc
        if self.model == "auto":
            self.model = self.cfg.model = pick_model(installed)
            LOG.info("Modèle choisi sur %s : %s", self.cfg.host, self.model)
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in installed:
            where = f" sur {self.cfg.host}" if self.cfg.remote else ""
            available = f" Modèles présents{where} : {', '.join(installed)}." if installed else ""
            raise RuntimeError(f"Modèle {self.model} absent{where}.{available} Installe-le : "
                               f"`ollama pull {self.model}` (ou `jarvis setup`).")

    def unload(self) -> None:
        """Rend la mémoire du modèle au système (mesuré : 3,6 Go). Il se recharge en ~1,3 s au besoin."""
        try:
            self._client.chat(model=self.model, messages=[{"role": "user", "content": "."}],
                              options={"num_predict": 1}, keep_alive=0)
        except Exception as exc:  # noqa: BLE001 - Ollama absent ou déjà déchargé : sans importance
            LOG.debug("Déchargement du modèle local impossible : %s", exc)

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
