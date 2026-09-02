"""LangSmith tracing, env-gated.

LangChain picks tracing up from process environment variables, but the
backend reads its config through pydantic-settings (which does *not*
export into `os.environ`), so the harness loads `.env` itself before any
LangChain import can capture the values. Tracing stays entirely optional:
with the vars unset, runs still happen and the local JSON run records in
`results/` remain the record of what occurred.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "kube-troubleshooting-agent-eval"


def init_tracing() -> bool:
    """Load `.env` and report whether LangSmith tracing ended up enabled."""
    load_dotenv(BACKEND_DIR / ".env")

    # langchain-core honours both the current LANGSMITH_* names and the
    # older LANGCHAIN_* ones; accept either so an existing .env keeps working.
    enabled = _truthy(os.getenv("LANGSMITH_TRACING")) or _truthy(os.getenv("LANGCHAIN_TRACING_V2"))
    has_key = bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))

    if enabled and has_key:
        os.environ.setdefault("LANGSMITH_PROJECT", DEFAULT_PROJECT)
        # Keep the legacy name in sync so either resolution path lands in
        # the same project rather than splitting runs across two.
        os.environ.setdefault("LANGCHAIN_PROJECT", os.environ["LANGSMITH_PROJECT"])
        return True
    return False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def run_config(scenario_id: str, namespace: str) -> dict:
    """Per-run LangChain config: names and tags the trace by scenario.

    Harmless when tracing is off — it is just metadata on the invocation —
    so the runner passes it unconditionally rather than branching.
    """
    return {
        "run_name": f"eval:{scenario_id}",
        "tags": ["eval", f"scenario:{scenario_id}"],
        "metadata": {"scenario_id": scenario_id, "namespace": namespace},
    }
