"""Outils : ce que Jarvis peut faire sur l'ordinateur, chacun avec un niveau de permission.

N1 : sans confirmation (ouvrir une appli, le volume, un minuteur…).
N2 : confirmation vocale, mémorisable en répondant « toujours » (fermer une appli, verrouiller).
N3 : confirmation à chaque fois, jamais mémorisée (éteindre, redémarrer).

Un outil « parlant » renvoie une phrase dite telle quelle ; un outil de données (lire une page,
une application) renvoie un contenu que le modèle lit avant de répondre. Un contexte (« browser »,
« code ») réserve un outil aux moments où il sert : un petit modèle choisit mieux parmi moins d'outils.
"""
from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ToolsConfig

LOG = logging.getLogger("jarvis.tools")
N1, N2, N3 = "N1", "N2", "N3"
YES, ALWAYS, NO = "yes", "always", "no"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    level: str
    handler: Callable[..., str]
    confirm: Callable[[dict[str, Any]], str] | None = None
    context: str = ""          # "" : toujours proposé ; sinon seulement dans ce contexte
    speaks: bool = True        # False : le résultat retourne au modèle au lieu d'être dit

    def schema(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def question(self, arguments: dict[str, Any]) -> str:
        return self.confirm(arguments) if self.confirm else f"Je lance l'action {self.name} ?"


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict[str, Any] | None = None, required: tuple[str, ...] = (),
         level: str = N1, confirm: Callable[[dict[str, Any]], str] | None = None, context: str = "",
         speaks: bool = True):
    parameters = {"type": "object", "properties": properties or {}, "required": list(required)}

    def register(handler: Callable[..., str]) -> Callable[..., str]:
        REGISTRY[name] = Tool(name, description, parameters, level, handler, confirm, context, speaks)
        return handler
    return register


@dataclass(frozen=True)
class ToolResult:
    name: str
    arguments: dict[str, Any]
    level: str
    text: str
    allowed: bool
    ms: float


Confirm = Callable[[str, bool], str]   # (question, « toujours » possible) → yes | always | no


def _clean_arguments(tool_: Tool, arguments: dict[str, Any]) -> dict[str, Any]:
    """Les petits modèles envoient « "30" » pour 30 ou des champs en trop : on corrige."""
    accepted = inspect.signature(tool_.handler).parameters
    clean: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in accepted or value is None:
            continue
        kind = tool_.parameters["properties"].get(key, {}).get("type")
        try:
            if kind == "integer":
                value = int(float(value))
            elif kind == "number":
                value = float(value)
            elif kind == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "oui", "yes")
            elif kind == "string":
                value = str(value)
        except (TypeError, ValueError):
            continue
        clean[key] = value
    return clean


class ToolExecutor:
    def __init__(self, cfg: ToolsConfig, confirm: Confirm | None = None,
                 on_result: Callable[[ToolResult], None] | None = None,
                 on_always: Callable[[str], None] | None = None):
        from . import builtin  # noqa: F401 - enregistre les outils intégrés
        self.cfg = cfg
        self.confirm = confirm
        self.on_result = on_result
        self.on_always = on_always

    def tools(self, context: str | None = None) -> list[Tool]:
        """Outils disponibles ; avec un contexte, seulement les généraux et ceux de ce contexte."""
        if not self.cfg.enabled:
            return []
        return [t for name, t in REGISTRY.items()
                if name not in self.cfg.disabled and (context is None or not t.context or t.context == context)]

    def schemas(self, context: str | None = None) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools(context)]

    def speaks(self, name: str) -> bool:
        tool_ = REGISTRY.get(name)
        return tool_ is None or tool_.speaks

    def run(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        tool_ = REGISTRY.get(name)
        if tool_ is None or not self.cfg.enabled or name in self.cfg.disabled:
            return f"L'action {name} n'est pas disponible."
        clean = _clean_arguments(tool_, dict(arguments or {}))
        start = time.perf_counter()
        allowed = True
        if tool_.level == N3 or (tool_.level == N2 and name not in self.cfg.always_allow):
            decision = self.confirm(tool_.question(clean), tool_.level == N2) if self.confirm else NO
            if decision == ALWAYS and tool_.level == N2:
                self.cfg.always_allow.append(name)
                if self.on_always:
                    self.on_always(name)
            allowed = decision in (YES, ALWAYS)
        if not allowed:
            text = "D'accord, j'annule."
        else:
            try:
                text = tool_.handler(**clean)
            except TypeError as exc:
                LOG.warning("Paramètres invalides pour %s %s : %s", name, clean, exc)
                text = f"Il me manque des précisions pour l'action {name}."
            except Exception as exc:  # noqa: BLE001 - l'échec est dit à voix haute
                LOG.warning("Outil %s en échec : %s", name, exc)
                text = f"L'action a échoué : {exc}"
        result = ToolResult(name, clean, tool_.level, text, allowed, (time.perf_counter() - start) * 1000)
        LOG.info("🛠  %s %s → %.160s", name, clean, text)
        if self.on_result:
            self.on_result(result)
        return text
