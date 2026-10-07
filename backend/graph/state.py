"""AgentState and the plan→execute loop guard.

See docs/architecture.md §6-7 for the design this implements: a single
typed state threaded through the graph, fact-gathering nodes producing
normalized structured data, and judgment nodes (LLM) reasoning over it.
"""

import operator
from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph.message import add_messages
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
    # Set when the request can be answered from an earlier turn's evidence
    # without re-investigating. Only honoured when an earlier turn actually
    # investigated — `route_after_intake` enforces that, not the prompt.
    is_followup: bool = False


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


TurnRoute = Literal["investigate", "followup", "clarify"]


class TurnRecord(BaseModel):
    """One finished conversation turn, archived by `finalize`.

    The run fields on `AgentState` are reset at the start of every turn,
    so this is where a turn's evidence survives for later turns to use.
    """

    user_request: str
    route: TurnRoute = "investigate"
    # What the user was shown: the recommendation, the follow-up answer,
    # or the clarifying question.
    reply: str = ""
    scope: Scope | None = None
    investigation_log: list[ToolCallRecord] = []
    diagnosis: Diagnosis | None = None
    reference_docs: list[ReferenceDoc] = []
    recommendation: str | None = None
    loop_guard_triggered: bool = False


# What a turn says when `intake` asked for clarification but produced no
# question text. Shared with the API's streaming fallback so the reply the
# user sees and the one recorded in `messages` can't drift apart.
DEFAULT_CLARIFYING_QUESTION = "Could you say more about what's going wrong?"


class AgentState(BaseModel):
    # BaseMessage subclasses aren't plain pydantic models in every
    # langchain-core version, so state validation needs this relaxed.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_request: str

    # --- Conversation state: persisted across turns by the checkpointer.
    # Both are append-only via reducers, and only `finalize` writes them.
    # Prior Human/AI turns, so follow-ups have context.
    messages: Annotated[list[AnyMessage], add_messages] = []
    turns: Annotated[list[TurnRecord], operator.add] = []

    # --- Run state: everything below is reset at the start of each turn
    # (see `new_turn_input`). With a checkpointer, a turn's input is merged
    # into the saved state rather than replacing it, so without the reset
    # `step_count` would accumulate until the loop guard tripped before
    # the first tool call, and turn 2 would diagnose over turn 1's log.
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
    # Set by `answer_followup` instead of the investigation path's
    # `recommendation`, on a turn answered from earlier evidence.
    followup_answer: str | None = None

    step_count: int = 0
    loop_guard_triggered: bool = False
    # Set by `plan`, consumed and cleared by `execute_tool`. Plumbing
    # between the two nodes, not part of the architecture doc's state
    # shape, but the loop can't pass a decision otherwise.
    pending_tool_call: dict | None = None


CONVERSATION_FIELDS = frozenset({"messages", "turns"})


def new_turn_input(user_request: str) -> dict:
    """Graph input for a new turn: the request plus every run field at its default.

    Derived from the model rather than listed by hand, so a run field added
    later is reset automatically instead of silently leaking across turns.
    """
    reset = {
        name: info.get_default(call_default_factory=True)
        for name, info in AgentState.model_fields.items()
        if name not in CONVERSATION_FIELDS and name != "user_request"
    }
    return {**reset, "user_request": user_request}


def checkpoint_serializer() -> JsonPlusSerializer:
    """Serializer for checkpointers: strict, allowing only our state models.

    By default LangGraph deserializes any class named in a checkpoint (with
    a warning, slated to become an error). An explicit allowlist restricts
    it to LangGraph's built-in safe types plus these, so a tampered
    checkpoint DB can't instantiate arbitrary classes. A new model nested
    in state has to be added here, or loading a thread that contains it
    fails.
    """
    return JsonPlusSerializer(
        allowed_msgpack_modules=[Scope, ToolCallRecord, Diagnosis, ReferenceDoc, TurnRecord]
    )
