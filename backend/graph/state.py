"""AgentState and the plan→execute loop guard.

See docs/architecture.md §6-7 for the design this implements: a single
typed state threaded through the graph, fact-gathering nodes producing
normalized structured data, and judgment nodes (LLM) reasoning over it.
"""

from typing import Literal

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ConfigDict, Field

# Cap on plan->execute_tool iterations (docs/architecture.md §7 "Loop
# guard"). On hitting this, `plan` routes straight to `diagnose` instead
# of requesting another tool call, so a run always terminates.
LOOP_GUARD_MAX = 8


def _require_every_field(schema: dict) -> None:
    """Mark every property required in the schema sent to the model.

    Structured-output models keep Python defaults as a backstop (see
    `Diagnosis`), but a defaulted field is optional in the generated JSON
    schema, so constrained decoding (`method="json_schema"`) would let the
    model skip it. Requiring every field there makes the model fill each
    one, while the defaults still cover a response that somehow lacks one.
    """
    schema["required"] = list(schema.get("properties", {}))


# Applied to every model passed to `with_structured_output`.
STRUCTURED_OUTPUT_CONFIG = ConfigDict(json_schema_extra=_require_every_field)


class Scope(BaseModel):
    """The resource the user is asking about, or a clarifying question if it can't be resolved."""

    model_config = STRUCTURED_OUTPUT_CONFIG

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
    # True when `execute_tool` supplied the scope's namespace because the
    # planner left it out. Kept so eval can still count how often the
    # model omits it, even though the omission no longer misroutes the call.
    namespace_filled: bool = False


# Structured output from the `diagnose` node. (A comment, not a docstring:
# a docstring becomes the schema description sent to the model.)
#
# Every field has a Python default on purpose. A missing field on a
# required model raises `ValidationError` *inside the graph*, killing a
# run that had already gathered all the evidence it needed, at the last
# step. Defaulting is strictly better than crashing: a diagnosis that
# arrives without a stated confidence is still a diagnosis, and
# `confidence` defaults to "low" rather than "high" so an omission can
# never read as certainty. Constrained decoding plus
# `STRUCTURED_OUTPUT_CONFIG` should make omissions impossible; the
# defaults stay as the backstop.
class Diagnosis(BaseModel):
    """Root-cause diagnosis of the investigated failure."""

    model_config = STRUCTURED_OUTPUT_CONFIG

    root_cause: str = Field(default="", description="The underlying cause, not the symptom.")
    confidence: Literal["high", "medium", "low"] = Field(
        default="low", description="How strongly the gathered evidence supports the root cause."
    )
    citations: list[str] = Field(
        default_factory=list, description="Specific evidence from the investigation supporting the root cause."
    )


class ReferenceDoc(BaseModel):
    """A doc excerpt retrieved by `ground` for `recommend` to cite."""

    title: str
    source_url: str
    content: str
    score: float


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
    # Set by `ground`: doc excerpts retrieved for the diagnosed cause, and
    # the retrieval error if that failed (the run continues without docs).
    reference_docs: list[ReferenceDoc] = []
    grounding_error: str | None = None
    recommendation: str | None = None

    step_count: int = 0
    loop_guard_triggered: bool = False
    # Set by `plan`, consumed and cleared by `execute_tool`. Plumbing
    # between the two nodes, not part of the architecture doc's state
    # shape, but the loop can't pass a decision otherwise.
    pending_tool_call: dict | None = None
