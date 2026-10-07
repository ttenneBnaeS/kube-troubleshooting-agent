"""The deterministic graph nodes and routing functions (docs/architecture.md §7).

None of these call a model. The rule they encode — nothing in a node may
raise on a recoverable condition — exists because eval caught four
crashes that killed runs which had already gathered their evidence.
"""

import json

import pytest
from langchain_core.tools import tool

from agent import nodes
from graph.state import LOOP_GUARD_MAX, AgentState, Diagnosis, Scope, ToolCallRecord, TurnRecord


@tool
def fake_pod_tool(namespace: str | None = None, pod_name: str | None = None) -> str:
    """Fake namespaced tool."""
    return json.dumps({"namespace": namespace, "pod_name": pod_name})


@tool
def fake_node_tool(node_name: str | None = None) -> str:
    """Fake cluster-scoped tool."""
    return json.dumps({"node_name": node_name})


@tool
def failing_tool(name: str, namespace: str | None = None) -> str:
    """Fake tool that hits a 404."""
    from builders import _not_found

    raise _not_found("secrets", name)


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    monkeypatch.setattr(
        nodes, "TOOLS_BY_NAME", {t.name: t for t in (fake_pod_tool, fake_node_tool, failing_tool)}
    )


def state(**kwargs) -> AgentState:
    return AgentState(user_request=kwargs.pop("user_request", "q"), **kwargs)


def investigated_turn(user_request="earlier", **kwargs) -> TurnRecord:
    return TurnRecord(
        user_request=user_request,
        route="investigate",
        investigation_log=[ToolCallRecord(tool_name="initial_sweep", args={}, result="{}")],
        **kwargs,
    )


class TestExecuteTool:
    async def test_fills_scope_namespace_when_planner_omits_it(self):
        s = state(scope=Scope(namespace="eval-x"), pending_tool_call={"name": "fake_pod_tool", "args": {}})
        out = await nodes.execute_tool(s)
        [record] = out["investigation_log"]
        assert record.args["namespace"] == "eval-x"
        assert record.namespace_filled is True
        assert json.loads(record.result)["namespace"] == "eval-x"

    async def test_explicit_namespace_wins(self):
        s = state(
            scope=Scope(namespace="eval-x"),
            pending_tool_call={"name": "fake_pod_tool", "args": {"namespace": "other"}},
        )
        [record] = (await nodes.execute_tool(s))["investigation_log"]
        assert record.args["namespace"] == "other" and record.namespace_filled is False

    async def test_cluster_scoped_tool_gets_no_namespace(self):
        s = state(scope=Scope(namespace="eval-x"), pending_tool_call={"name": "fake_node_tool", "args": {}})
        [record] = (await nodes.execute_tool(s))["investigation_log"]
        assert "namespace" not in record.args

    async def test_tool_failure_is_recorded_as_evidence(self):
        # The 404 *is* the answer for a missing Secret; raising would kill the run.
        s = state(pending_tool_call={"name": "failing_tool", "args": {"name": "db-credentials"}})
        out = await nodes.execute_tool(s)
        assert json.loads(out["investigation_log"][0].result) == {
            "error": "not_found",
            "status": 404,
            "message": 'secrets "db-credentials" not found',
        }

    async def test_unknown_tool_is_recorded_not_raised(self):
        out = await nodes.execute_tool(state(pending_tool_call={"name": "nope", "args": {}}))
        assert json.loads(out["investigation_log"][0].result)["error"] == "unknown_tool"

    async def test_advances_loop_and_records_rationale(self):
        s = state(
            step_count=3,
            plan_rationale="checking the pod spec",
            pending_tool_call={"name": "fake_pod_tool", "args": {}},
        )
        out = await nodes.execute_tool(s)
        assert out["step_count"] == 4
        assert out["pending_tool_call"] is None
        assert out["investigation_log"][0].rationale == "checking the pod spec"


class TestGatherContext:
    async def test_unreachable_cluster_becomes_evidence(self, monkeypatch):
        def unreachable(**_):
            raise ConnectionError("cluster down")

        monkeypatch.setattr(nodes, "get_pod_status", unreachable)
        monkeypatch.setattr(nodes, "get_recent_events", lambda **_: [])
        out = await nodes.gather_context(state(scope=Scope(namespace="ns")))
        assert out["context_snapshot"]["pods"] == {"error": "tool_failed", "message": "ConnectionError: cluster down"}
        assert out["context_snapshot"]["events"] == []
        assert out["investigation_log"][0].tool_name == "initial_sweep"


class TestPlanLoopGuard:
    async def test_guard_stops_without_calling_a_model(self, monkeypatch):
        def no_model(*_args, **_kwargs):
            raise AssertionError("plan must not call the model once the guard trips")

        monkeypatch.setattr(nodes, "get_chat_model", no_model)
        out = await nodes.plan(state(step_count=LOOP_GUARD_MAX), config={})
        assert out == {"loop_guard_triggered": True, "pending_tool_call": None}


class TestRouting:
    def test_after_plan(self):
        assert nodes.route_after_plan(state(pending_tool_call={"name": "x", "args": {}})) == "execute_tool"
        assert nodes.route_after_plan(state()) == "diagnose"
        assert (
            nodes.route_after_plan(state(loop_guard_triggered=True, pending_tool_call={"name": "x", "args": {}}))
            == "diagnose"
        )

    def test_after_intake_investigates_by_default(self):
        assert nodes.route_after_intake(state(scope=Scope(namespace="ns"))) == "gather_context"

    def test_after_intake_clarifies(self):
        assert nodes.route_after_intake(state(scope=Scope(needs_clarification=True))) == "finalize"

    def test_followup_with_earlier_investigation(self):
        s = state(scope=Scope(is_followup=True), turns=[investigated_turn()])
        assert nodes.route_after_intake(s) == "answer_followup"

    def test_followup_on_first_turn_still_investigates(self):
        # A misclassified first turn must not skip the investigation and
        # answer from nothing.
        assert nodes.route_after_intake(state(scope=Scope(is_followup=True))) == "gather_context"

    def test_followup_after_only_a_clarification_still_investigates(self):
        clarify = TurnRecord(user_request="help", route="clarify")
        s = state(scope=Scope(is_followup=True), turns=[clarify])
        assert nodes.route_after_intake(s) == "gather_context"


class TestFinalize:
    async def test_investigation_turn(self):
        s = state(
            user_request="why?",
            scope=Scope(namespace="ns"),
            diagnosis=Diagnosis(root_cause="c"),
            recommendation="fix it",
            investigation_log=[ToolCallRecord(tool_name="t", args={}, result="r")],
        )
        out = await nodes.finalize(s)
        [turn] = out["turns"]
        assert (turn.route, turn.reply) == ("investigate", "fix it")
        assert turn.investigation_log == s.investigation_log
        assert [m.content for m in out["messages"]] == ["why?", "fix it"]

    async def test_clarify_turn_records_the_question(self):
        s = state(scope=Scope(needs_clarification=True, clarifying_question="Which namespace?"))
        out = await nodes.finalize(s)
        assert out["turns"][0].route == "clarify"
        assert out["messages"][1].content == "Which namespace?"

    async def test_clarify_without_question_uses_default(self):
        out = await nodes.finalize(state(scope=Scope(needs_clarification=True)))
        assert out["turns"][0].reply == "Could you say more about what's going wrong?"

    async def test_followup_turn(self):
        out = await nodes.finalize(state(followup_answer="It was `password`."))
        assert (out["turns"][0].route, out["turns"][0].reply) == ("followup", "It was `password`.")


def test_followup_context_is_bounded():
    turns = [investigated_turn(user_request=f"q{i}") for i in range(nodes.FOLLOWUP_TURNS + 2)]
    s = state(turns=[*turns, TurnRecord(user_request="c", route="clarify")])
    assert len(nodes._investigated_turns(s)) == nodes.FOLLOWUP_TURNS + 2
    # answer_followup slices to the most recent FOLLOWUP_TURNS.
    assert nodes._investigated_turns(s)[-nodes.FOLLOWUP_TURNS :][0].user_request == "q2"
