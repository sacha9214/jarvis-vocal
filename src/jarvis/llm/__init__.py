"""Moteurs de réponse : Ollama (local) et Claude via le binaire officiel `claude`."""
from __future__ import annotations

from ..config import Config
from .base import LanguageModel
from .router import CLAUDE, LOCAL, Router

BACKENDS = ("ollama", CLAUDE)


def load_backend(cfg: Config, name: str) -> LanguageModel:
    if name in ("ollama", LOCAL):
        from .ollama_backend import OllamaLLM
        return OllamaLLM(cfg.llm)
    if name == CLAUDE:
        from .claude_code import ClaudeCodeLLM
        return ClaudeCodeLLM(cfg.claude)
    raise ValueError(f"Moteur inconnu : {name} ({', '.join(BACKENDS)})")


def load_llm(cfg: Config) -> Router:
    """Les deux moteurs sont toujours déclarés : on bascule de l'un à l'autre à la voix."""
    if cfg.llm.backend not in BACKENDS:
        raise ValueError(f"llm.backend inconnu : {cfg.llm.backend} ({', '.join(BACKENDS)})")
    backends = {LOCAL: load_backend(cfg, LOCAL), CLAUDE: load_backend(cfg, CLAUDE)}
    active = CLAUDE if cfg.llm.backend == CLAUDE else LOCAL
    return Router(backends, active, LOCAL if cfg.claude.fallback_to_local else None)
