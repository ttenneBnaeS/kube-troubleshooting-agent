"""LangSmith tracing, env-gated, for the API and the eval harness.

LangChain reads tracing config from process environment variables, but
backend config goes through pydantic-settings, which doesn't export to
`os.environ` — so `.env` is loaded into the environment here, before any
LangChain call can need it. Tracing stays optional: with the vars unset,
everything runs untraced.

Each surface traces to its own project so eval runs never mix with real
traffic: the API uses `LANGSMITH_PROJECT`, the eval harness
`LANGSMITH_EVAL_PROJECT`, each with a default.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]


def init_tracing(project_var: str, default_project: str, env_file: Path | None = None) -> str | None:
    """Load `.env`, pick this surface's project, and return it if tracing is on (else None)."""
    load_dotenv(env_file or BACKEND_DIR / ".env")

    # langchain-core honours both the current LANGSMITH_* names and the
    # older LANGCHAIN_* ones; accept either so an existing .env keeps working.
    enabled = _truthy(os.getenv("LANGSMITH_TRACING")) or _truthy(os.getenv("LANGCHAIN_TRACING_V2"))
    has_key = bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))
    if not (enabled and has_key):
        return None

    project = os.getenv(project_var) or default_project
    # Set, not setdefault: a project from .env must not pull eval runs into
    # the production project. Both names, so either resolution path agrees.
    os.environ["LANGSMITH_PROJECT"] = project
    os.environ["LANGCHAIN_PROJECT"] = project
    return project


def flush_traces() -> None:
    """Block until queued traces are sent; called at shutdown so the last turns aren't lost."""
    from langchain_core.tracers.langchain import wait_for_all_tracers

    wait_for_all_tracers()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}
