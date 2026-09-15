"""Modèles de langage. Étape 1 : Ollama (local). Étape 2 : abonnement Claude via Claude Code."""
from __future__ import annotations

from ..config import LlmConfig
from .base import LanguageModel


def load_llm(cfg: LlmConfig) -> LanguageModel:
    if cfg.backend == "ollama":
        from .ollama_backend import OllamaLLM
        return OllamaLLM(cfg)
    raise ValueError(f"Backend LLM inconnu : {cfg.backend} (disponible : ollama)")
