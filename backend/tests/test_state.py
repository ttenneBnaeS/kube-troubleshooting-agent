"""AgentState contracts: per-turn reset, structured-output schemas, checkpoint serialization."""

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from graph.state import (
    CONVERSATION_FIELDS,
    AgentState,
    Diagnosis,
    ReferenceDoc,
    Scope,
    ToolCallRecord,
    TurnRecord,
    checkpoint_serializer,
    new_turn_input,
)


def test_new_turn_input_resets_every_run_field():
    turn = new_turn_input("why is it broken?")
    run_fields = set(AgentState.model_fields) - CONVERSATION_FIELDS
    # Every run field is present, so a checkpointed thread can't carry one over.
    assert set(turn) == run_fields
    assert turn["user_request"] == "why is it broken?"
    assert turn["step_count"] == 0
    assert turn["investigation_log"] == []
    assert turn["diagnosis"] is None
    assert not CONVERSATION_FIELDS & set(turn)


def test_new_turn_input_defaults_are_fresh_objects():
    a, b = new_turn_input("a"), new_turn_input("b")
    a["investigation_log"].append("x")
    a["context_snapshot"]["k"] = "v"
    assert b["investigation_log"] == [] and b["context_snapshot"] == {}


def test_structured_output_schemas_require_every_field():
    # Python defaults are the backstop; the schema sent to the model must
    # still require every field so constrained decoding fills them.
    for model in (Scope, Diagnosis):
        schema = model.model_json_schema()
        assert set(schema["required"]) == set(schema["properties"]), model.__name__


def test_structured_output_models_never_raise_on_omission():
    # An omitted field must default, not raise ValidationError mid-graph.
    assert Diagnosis().confidence == "low"
    assert Scope().needs_clarification is False and Scope().is_followup is False


def test_diagnosis_has_no_docstring_leaking_dev_notes():
    # The docstring becomes the schema description the model sees.
    assert Diagnosis.model_json_schema()["description"] == "Root-cause diagnosis of the investigated failure."


def test_checkpoint_serializer_round_trips_state_models():
    serde = checkpoint_serializer()
    turn = TurnRecord(
        user_request="q",
        route="investigate",
        reply="r",
        scope=Scope(namespace="ns"),
        investigation_log=[ToolCallRecord(tool_name="t", args={"a": 1}, result="{}", rationale="why")],
        diagnosis=Diagnosis(root_cause="c", confidence="high", citations=["e"]),
        reference_docs=[ReferenceDoc(title="T", source_url="https://x", content="c", score=0.5)],
    )
    value = {"turns": [turn], "messages": [HumanMessage("q"), AIMessage("r")]}
    restored = serde.loads_typed(serde.dumps_typed(value))
    assert restored["turns"][0] == turn
    assert [m.content for m in restored["messages"]] == ["q", "r"]


def test_checkpoint_serializer_is_strict(recwarn):
    # A strict allowlist means no "unregistered type" deserialization
    # warning for our own models — the warning is what precedes LangGraph
    # blocking them outright.
    serde = checkpoint_serializer()
    serde.loads_typed(serde.dumps_typed({"d": Diagnosis(root_cause="c")}))
    assert not [w for w in recwarn if "unregistered type" in str(w.message)]
    assert isinstance(InMemorySaver(serde=serde).serde, type(serde))
