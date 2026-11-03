"""Environment: .env loading, model-profile selection and per-provider credential checks."""

from __future__ import annotations

import os
from pathlib import Path

from .scoring.config import RubricConfig, load_config, with_assignment_text


class MissingCredentials(Exception):
    pass


def load_env(root: Path | None = None) -> None:
    from dotenv import load_dotenv

    # A gitignored .env; real environment variables take precedence.
    load_dotenv((root or Path.cwd()) / ".env", override=False)


def resolve_config(path: Path, profile: str | None = None, assignment_text: str | None = None) -> RubricConfig:
    load_env()
    config = load_config(path, profile or os.environ.get("EVALUATOR_MODELS") or None)
    if assignment_text:
        config = with_assignment_text(config, assignment_text)
    return config


def providers(config: RubricConfig) -> set[str]:
    from .providers import provider_of

    models = {c.model for c in config.criteria} | set(config.models.values())
    return {provider_of(m) for m in models}


def available_providers() -> dict[str, bool]:
    load_env()
    anthropic = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE",
                                                  "ANTHROPIC_FEDERATION_RULE_ID")) \
        or (Path.home() / ".config" / "anthropic").exists()
    return {"anthropic": bool(anthropic), "openai": bool(os.environ.get("OPENAI_API_KEY"))}


def check_credentials(config: RubricConfig) -> None:
    """Every provider the config uses needs credentials; fail before any work starts."""
    have = available_providers()
    missing = []
    need = providers(config)
    if "anthropic" in need and not have["anthropic"]:
        missing.append("ANTHROPIC_API_KEY (Claude models)")
    if "openai" in need and not have["openai"]:
        missing.append("OPENAI_API_KEY (OpenAI models)")
    if missing:
        raise MissingCredentials("missing credentials: " + ", ".join(missing)
                                 + ". Add them to .env, or pass --models openai to use only OpenAI.")
