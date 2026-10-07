"""Eval-side tracing config: its own LangSmith project, scenario-tagged runs.

See `observability.tracing` for why `.env` has to be loaded into the
process environment before LangChain is imported.
"""

from observability.tracing import init_tracing as _init_tracing

DEFAULT_PROJECT = "kube-troubleshooting-agent-eval"


def init_tracing() -> str | None:
    """The LangSmith project eval runs trace to, or None if tracing is off."""
    return _init_tracing("LANGSMITH_EVAL_PROJECT", DEFAULT_PROJECT)


def run_config(scenario_id: str, namespace: str, rag: bool = True) -> dict:
    """Per-run LangChain config: names and tags the trace by scenario.

    Harmless when tracing is off — it is just metadata on the invocation —
    so the runner passes it unconditionally rather than branching.
    """
    return {
        "run_name": f"eval:{scenario_id}",
        "tags": ["eval", f"scenario:{scenario_id}", "rag" if rag else "no-rag"],
        "metadata": {"scenario_id": scenario_id, "namespace": namespace, "rag": rag},
        "configurable": {"rag": rag},
    }
