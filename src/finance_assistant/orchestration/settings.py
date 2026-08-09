"""Environment-driven configuration for the orchestration layer.

Shared by `interpreter.py` (model name, credential detection, confidence
threshold) and `orchestrator.py` (the same, plus the ceilings) so this
logic exists exactly once -- `config.py` stays pure static constants (no
env reading), matching its existing convention.
"""

import os
from dataclasses import dataclass
from typing import Mapping

import dotenv

_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    global _DOTENV_LOADED
    if not _DOTENV_LOADED:
        dotenv.load_dotenv(override=False)  # no-op if no .env; never clobbers a real env var (e.g. CI secrets)
        _DOTENV_LOADED = True


@dataclass(frozen=True)
class Settings:
    llm_model: str
    max_calls_per_question: int
    max_cost_usd_per_question: float
    min_confidence: float

    def provider(self) -> str:
        return self.llm_model.split("/", 1)[0].lower()

    def credential_env_var(self) -> str:
        return f"{self.provider().upper()}_API_KEY"

    def has_credential(self, env: Mapping[str, str] | None = None) -> bool:
        env = env if env is not None else os.environ
        return bool(env.get(self.credential_env_var(), "").strip())


def load_settings(*, model: str | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """`model` is an explicit override parameter -- never a hidden global
    read inside a function body -- so a later "compare providers" phase can
    thread a different model through per call. `env` is injectable for
    tests; production/CLI/evals callers omit it and get live `os.environ`
    (tests instead use monkeypatch.setenv/delenv, matching this repo's
    existing style elsewhere)."""
    _ensure_dotenv_loaded()
    env = env if env is not None else os.environ
    return Settings(
        llm_model=model or env.get("LLM_MODEL", "anthropic/claude-sonnet-4-5"),
        max_calls_per_question=int(env.get("LLM_MAX_CALLS_PER_QUESTION", "5")),
        max_cost_usd_per_question=float(env.get("LLM_MAX_COST_USD_PER_QUESTION", "0.50")),
        min_confidence=float(env.get("LLM_MIN_CONFIDENCE", "0.5")),
    )
