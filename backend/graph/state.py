"""AgentState and the plan→execute loop guard.

See docs/architecture.md §6-7 for the design this implements: a single
typed state threaded through the graph, fact-gathering nodes producing
normalized structured data, and judgment nodes (LLM) reasoning over it.
"""

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict

# Cap on plan->execute_tool iterations (docs/architecture.md §7 "Loop
# guard"). On hitting this, `plan` routes straight to `diagnose` instead
# of requesting another tool call, so a run always terminates.
LOOP_GUARD_MAX = 8


class Scope(BaseModel):
    namespace: str | None = None
    resource_type: str | None = None
    resource_name: str | None = None
    # `intake` sets these when the request is too ambiguous to resolve a
    # scope from (e.g. no namespace/pod mentioned and more than one
    # candidate exists) — the graph short-circuits to END with the
    # question as the response instead of guessing.
    needs_clarification: bool = False
    clarifying_question: str | None = None


class ToolCallRecord(BaseModel):
    tool_name: str
    args: dict
    result: str


class Diagnosis(BaseModel):
    root_cause: str
    confidence: str  # "high" | "medium" | "low"
    citations: list[str] = []


class AgentState(BaseModel):
    # BaseMessage subclasses aren't plain pydantic models in every
    # langchain-core version, so state validation needs this relaxed.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_request: str
    # Prior conversation turns (Human/AI), passed in from the API layer so
    # follow-ups have context. Real checkpointed memory is Week 6 — see
    # docs/architecture.md §3.5/§7; this is the same "client resends
    # history" approach Weeks 1-3 already used.
    messages: list[BaseMessage] = []

    scope: Scope | None = None
    context_snapshot: dict = {}
    investigation_log: list[ToolCallRecord] = []
    hypothesis: str | None = None
    diagnosis: Diagnosis | None = None
    recommendation: str | None = None

    step_count: int = 0
    loop_guard_triggered: bool = False
    # Set by `plan`, consumed and cleared by `execute_tool`. Plumbing
    # between the two nodes, not part of the architecture doc's state
    # shape, but the loop can't pass a decision otherwise.
    pending_tool_call: dict | None = None
