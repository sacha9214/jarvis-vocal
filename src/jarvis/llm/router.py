"""Choix du moteur de réponse : local (Ollama) ou Claude.

Bascule à la voix (« passe sur Claude », « passe en local ») et repli automatique : si
Claude échoue avant d'avoir dit un mot (hors ligne, limite de l'abonnement atteinte,
connexion expirée), Jarvis le dit, répond en local et y reste jusqu'à ce que tu
redemandes Claude. Pas de nouvel essai silencieux, et donc de seconde perdue, à chaque question.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from typing import Any

from .base import Delta, Event, LanguageModel, Message, Notice

LOG = logging.getLogger("jarvis.llm")
LOCAL = "local"
CLAUDE = "claude"
_LABELS = {LOCAL: "le modèle local", CLAUDE: "Claude"}


def _label(name: str) -> str:
    return _LABELS.get(name, name)


class Router:
    name = "router"

    def __init__(self, backends: dict[str, LanguageModel], active: str, fallback: str | None = None):
        if active not in backends:
            raise ValueError(f"Moteur actif inconnu : {active}")
        self.backends = backends
        self.active = active
        self.fallback = fallback if fallback in backends else None
        self.tools: list[dict[str, Any]] | None = None
        self._system: str | None = None

    @property
    def model(self) -> str:
        return self.backends[self.active].model

    def check(self) -> None:
        """Le moteur actif doit marcher ; sinon on démarre sur le repli, s'il marche lui."""
        try:
            self.backends[self.active].check()
        except RuntimeError as exc:
            if self.fallback is None or self.fallback == self.active:
                raise
            try:
                self.backends[self.fallback].check()
            except RuntimeError:
                raise exc from None
            LOG.warning("%s indisponible (%s) : démarrage sur %s.", _label(self.active), exc, _label(self.fallback))
            self.active = self.fallback

    def warmup(self, system_prompt: str) -> None:
        self._system = system_prompt
        self.backends[self.active].warmup(system_prompt, self.tools)

    def describe(self) -> str:
        if self.active == CLAUDE:
            return f"J'utilise Claude, modèle {self.model}."
        return "J'utilise le modèle local."

    def switch(self, target: str) -> str:
        label = _label(target)
        if target not in self.backends:
            return f"{label.capitalize()} n'est pas configuré."
        if target == self.active:
            return f"J'utilise déjà {label}."
        backend = self.backends[target]
        try:
            backend.check()
        except RuntimeError as exc:
            LOG.warning("Bascule vers %s impossible : %s", label, exc)
            self.last_error = str(exc)      # affiché tel quel par l'interface
            return f"Je ne peux pas passer sur {label}, le détail est dans le terminal."
        self.active = target
        if self._system:   # chauffe en arrière-plan : la prochaine question ne paie pas le chargement
            threading.Thread(target=backend.warmup, args=(self._system, self.tools), name="warmup",
                             daemon=True).start()
        return f"C'est fait, je passe sur {label}."

    def stream(self, messages: Sequence[Message], cancel: threading.Event | None = None,
               tools: list[dict[str, Any]] | None = None) -> Iterator[Event]:
        tools = self.tools if tools is None else tools
        if messages and messages[0].get("role") == "system":
            self._system = messages[0]["content"]
        name = self.active
        spoke = False
        try:
            for event in self.backends[name].stream(messages, cancel=cancel, tools=tools):
                spoke = spoke or isinstance(event, Delta)
                yield event
            return
        except Exception as exc:
            fallback = self.fallback
            if spoke or fallback is None or fallback == name or (cancel is not None and cancel.is_set()):
                raise
            LOG.warning("%s en échec (%s) : repli sur %s.", _label(name), exc, _label(fallback))
            self.active = fallback
        yield Notice(f"{_label(name).capitalize()} ne répond pas, je réponds en local. "
                     "Dis « passe sur Claude » pour réessayer.")
        yield from self.backends[fallback].stream(messages, cancel=cancel, tools=tools)
