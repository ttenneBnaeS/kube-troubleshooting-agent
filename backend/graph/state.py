"""AgentState and the plan→execute loop guard.

See docs/architecture.md §6-7 for the design this implements: a single
typed state threaded through the graph, fact-gathering nodes producing
normalized structured data, and judgment nodes (LLM) reasoning over it.
"""

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

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
    """Structured output from the `diagnose` node.

    Every field has a default on purpose. `with_structured_output` does
    not guarantee the model fills each one, and a missing field on a
    required schema raises `ValidationError` *inside the graph* — killing
    a run that had already gathered all the evidence it needed, at the
    last step. Defaulting is strictly better than crashing: a diagnosis
    that arrives without a stated confidence is still a diagnosis, and
    `confidence` defaults to "low" rather than "high" so an omission can
    never read as certainty.
    """

    root_cause: str = Field(default="", description="The underlying cause, not the symptom.")
    confidence: str = Field(default="low", description='One of "high", "medium", or "low".')
    citations: list[str] = Field(
        default_factory=list, description="Specific evidence from the investigation supporting the root cause."
    )


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
