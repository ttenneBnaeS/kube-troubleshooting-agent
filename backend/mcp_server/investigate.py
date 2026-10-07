"""One investigation, run end to end for an MCP client.

The same graph the chat API runs, on a fresh thread per call: the calling
assistant keeps its own conversation, so server-side memory would only
duplicate it. Instead the result carries the evidence trail, which lets
the client answer follow-up questions about it without another run.
"""

import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from api import trail
from graph import build_graph, new_turn_input
from graph.state import TurnRecord, checkpoint_serializer
from observability import bind, get_logger

log = get_logger(__name__)

# Shown to the client as each node finishes. The graph's own node names
# mean nothing to a user watching a progress bar.
NODE_PROGRESS = {
    "intake": "Resolving what to investigate",
    "gather_context": "Swept pods and events",
    "plan": "Planning the next check",
    "execute_tool": "Gathered evidence",
    "diagnose": "Diagnosed the root cause",
    "ground": "Retrieved reference docs",
    "recommend": "Wrote the suggested fix",
}

ProgressFn = Callable[[int, str], Awaitable[None]]


class Diagnosis(BaseModel):
    root_cause: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[str] = Field(description="The specific observations the diagnosis rests on.")


class Investigation(BaseModel):
    """Result of a read-only investigation of a Kubernetes failure."""

    status: Literal["diagnosed", "needs_clarification"]
    clarifying_question: str | None = None
    diagnosis: Diagnosis | None = None
    recommendation: str | None = Field(
        default=None,
        description="Suggested fix in markdown. Nothing has been applied; the commands are for a human to run.",
    )
    namespace: str | None = None
    tool_calls: int = Field(default=0, description="Planner tool calls after the initial sweep.")
    hit_step_limit: bool = Field(
        default=False, description="True if the investigation stopped at its step cap; treat the diagnosis as partial."
    )
    trail: list[dict] = Field(
        default_factory=list,
        description="What was checked, in order: the initial sweep, each tool call with its (truncated) result, "
        "the diagnosis, and the docs retrieved.",
    )


@lru_cache(maxsize=1)
def _graph():
    return build_graph(InMemorySaver(serde=checkpoint_serializer()))


async def run_investigation(request: str, namespace: str | None, progress: ProgressFn | None = None) -> Investigation:
    # A namespace argument is folded into the request rather than forced
    # into the scope: `intake` already resolves one from text, and this
    # keeps the MCP path and the chat path going through the same node.
    text = f"{request}\n\n(Namespace: {namespace})" if namespace else request
    thread_id = f"mcp-{uuid.uuid4().hex[:12]}"
    config = {
        "configurable": {"thread_id": thread_id},
        "run_name": "mcp_investigate",
        "tags": ["mcp"],
        "metadata": {"thread_id": thread_id},
    }

    with bind(thread_id=thread_id, surface="mcp"):
        log.info("mcp.investigate.start", extra={"request_chars": len(request), "namespace": namespace})
        final_state = None
        step = 0
        async for mode, chunk in _graph().astream(new_turn_input(text), config=config, stream_mode=["updates", "values"]):
            if mode == "updates" and progress:
                for node in chunk:
                    if node in NODE_PROGRESS:
                        step += 1
                        await progress(step, NODE_PROGRESS[node])
            elif mode == "values":
                final_state = chunk

        turn = TurnRecord.model_validate(final_state["turns"][-1])
        result = _to_result(turn)
        log.info(
            "mcp.investigate.end",
            extra={"status": result.status, "tool_calls": result.tool_calls, "confidence": result.diagnosis and result.diagnosis.confidence},
        )
        return result


def _to_result(turn: TurnRecord) -> Investigation:
    namespace = turn.scope.namespace if turn.scope else None
    if turn.route == "clarify":
        return Investigation(status="needs_clarification", clarifying_question=turn.reply, namespace=namespace)
    return Investigation(
        status="diagnosed",
        diagnosis=(
            Diagnosis(
                root_cause=turn.diagnosis.root_cause,
                confidence=turn.diagnosis.confidence,
                evidence=turn.diagnosis.citations,
            )
            if turn.diagnosis
            else None
        ),
        recommendation=turn.recommendation,
        namespace=namespace,
        tool_calls=sum(1 for r in turn.investigation_log if r.tool_name != trail.INITIAL_SWEEP),
        hit_step_limit=turn.loop_guard_triggered,
        trail=trail.turn_trail(turn),
    )
