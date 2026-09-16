"""Pont entre l'interface et Jarvis : état, réglages appliqués en direct, actions."""
from __future__ import annotations

import copy
import dataclasses
import logging
import os
import sys
import threading
from typing import TYPE_CHECKING, Any

from .. import __version__, paths
from .. import config as config_module
from ..config import Config
from ..events import EventBus
from ..llm.router import CLAUDE, LOCAL
from ..system import status
from ..tools import REGISTRY
from ..tools import builtin as _builtin  # noqa: F401 - enregistre les outils listés dans l'interface
from ..tools.builtin import TIMERS
from . import schema

if TYPE_CHECKING:
    from ..app import Components
    from ..pipeline import Assistant

LOG = logging.getLogger("jarvis.ui")


def restart_process() -> None:
    LOG.info("Redémarrage de Jarvis…")
    os.execv(sys.executable, [sys.executable, *sys.orig_argv[1:]])


class Controller:
    def __init__(self, cfg: Config, bus: EventBus):
        self.cfg = cfg
        self.bus = bus
        self.parts: Components | None = None
        self.assistant: Assistant | None = None
        self._lock = threading.Lock()

    def attach(self, parts: Components, assistant: Assistant) -> None:
        self.parts, self.assistant = parts, assistant

    # -- lecture

    def state(self) -> dict[str, Any]:
        engine = {"active": self.parts.llm.active, "model": self.parts.llm.model} if self.parts else None
        return {
            "version": __version__,
            "config": dataclasses.asdict(self.cfg),
            "config_path": str(config_module.LOADED_PATH or paths.config_path()),
            "sections": schema.sections(),
            "tools": [{"name": t.name, "description": t.description, "level": t.level} for t in REGISTRY.values()],
            "engine": engine,
            "ready": self.assistant is not None,
        }

    def status(self) -> dict[str, Any]:
        return {**status.snapshot(), "timers": TIMERS.snapshot()}

    # -- réglages

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(updates, dict) or not updates:
            raise ValueError("Aucun réglage à enregistrer.")
        flat = schema.flatten(updates)
        schema.validate(flat, set(REGISTRY))
        with self._lock:
            config_module._apply(copy.deepcopy(self.cfg), updates)     # clés et sections valides ?
            path = config_module.update_file(updates)
            config_module._apply(self.cfg, updates)
            applied, restart = [], []
            for key, value in flat.items():
                if schema.is_live(key):
                    applied.append(key)
                    self._apply_live(key, value)
                else:
                    restart.append(key)
        LOG.info("Réglages enregistrés dans %s : %s", path, ", ".join(flat))
        self.bus.publish("config", keys=list(flat))
        return {"state": self.state(), "applied": applied, "restart": restart}

    def _apply_live(self, key: str, value: Any) -> None:
        parts = self.parts
        if parts is None:
            return
        llm = parts.llm
        if key == "wakeword.threshold":
            parts.wakeword.threshold = float(value)
        elif key == "llm.backend":
            llm.switch(CLAUDE if value == "claude" else LOCAL)
            self._publish_engine()
        elif key == "llm.host":
            local = llm.backends[LOCAL]
            local.set_host(str(value))
            local.model = self.cfg.llm.model = "auto" if self.cfg.llm.remote else self.cfg.llm.model
            local.check()                                      # modèle choisi ou erreur claire tout de suite
            threading.Thread(target=local.warmup, args=(llm._system or "", llm.tools), daemon=True).start()
            self._publish_engine()
        elif key == "llm.model":
            local = llm.backends[LOCAL]
            local.model = str(value)
            threading.Thread(target=local.warmup, args=(llm._system or "", llm.tools), daemon=True).start()
            self._publish_engine()
        elif key in ("claude.model", "claude.effort"):
            claude = llm.backends[CLAUDE]
            claude.model = self.cfg.claude.model
            close = getattr(claude, "close", None)
            if close:
                close()                   # relancé avec le nouveau réglage à la prochaine question
            self._publish_engine()
        elif key == "claude.fallback_to_local":
            llm.fallback = LOCAL if value else None
        elif key == "tts.voice":
            setter = getattr(parts.tts, "set_voice", None)
            if setter:
                setter(str(value))
        elif key.startswith("screen.") and parts.screen is not None:
            parts.screen.refresh()
        elif key == "tts.length_scale":
            setter = getattr(parts.tts, "set_length_scale", None)
            if setter:
                setter(float(value))

    def _publish_engine(self) -> None:
        if self.parts:
            self.bus.publish("engine", active=self.parts.llm.active, model=self.parts.llm.model)

    # -- actions

    def action(self, name: str, value: Any = None) -> dict[str, Any]:
        if name == "restart":
            threading.Timer(0.6, restart_process).start()
            return {"ok": True, "message": "Redémarrage…"}
        if self.assistant is None or self.parts is None:
            raise RuntimeError("Jarvis est encore en train de charger.")
        if name == "wake":
            self.assistant.wake()
        elif name == "stop":
            self.assistant.stop()
        elif name == "decide":
            self.assistant.decide(value if value in ("yes", "no", "always") else "no")
        elif name == "switch":
            message = self.parts.llm.switch(str(value))
            self._publish_engine()
            ok = self.parts.llm.active == value
            detail = None if ok else getattr(self.parts.llm, "last_error", None)
            return {"ok": ok, "message": detail or message}
        else:
            raise ValueError(f"Action inconnue : {name}")
        return {"ok": True}
