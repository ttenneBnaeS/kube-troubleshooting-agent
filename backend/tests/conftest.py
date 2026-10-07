"""Shared fixtures. Tests cover the deterministic layer only: no cluster, no model calls."""

import os

# Before anything imports `api.main`, which loads `.env` into the process
# environment for tracing. load_dotenv never overrides a set variable, so
# this keeps a developer's LANGSMITH_TRACING=true from sending test runs
# to LangSmith.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import json

import pytest
from builders import FakeCoreV1
from langchain_core.tools import tool

import agent.nodes
import tools.describe
import tools.events
import tools.logs
import tools.nodes
import tools.pods
import tools.policies
import tools.services


@pytest.fixture
def core(monkeypatch) -> FakeCoreV1:
    """A FakeCoreV1 wired into every tool module that imported the client getter."""
    fake = FakeCoreV1()
    for module in (tools.pods, tools.events, tools.logs, tools.services, tools.describe, tools.nodes, tools.policies):
        if hasattr(module, "get_core_v1_api"):
            monkeypatch.setattr(module, "get_core_v1_api", lambda: fake)
    return fake


@pytest.fixture
def stub_nodes(monkeypatch):
    """Replace the model- and cluster-backed nodes in `graph.build` with stubs.

    Routing, the plan/execute loop, `execute_tool`, `finalize` and the
    checkpointer all run for real; only what would call a model or the
    cluster is canned. Requests steer the stubs: "vague" asks for
    clarification, "again" is classified as a follow-up, and the planner
    makes two tool calls per investigation.
    """
    import graph.build as build
    from graph.state import Diagnosis, ReferenceDoc, Scope, ToolCallRecord

    async def intake(state):
        request = state.user_request
        return {
            "scope": Scope(
                namespace="ns1",
                is_followup="again" in request,
                needs_clarification="vague" in request,
                clarifying_question="Which namespace?" if "vague" in request else None,
            )
        }

    async def gather_context(state):
        record = ToolCallRecord(tool_name="initial_sweep", args={"namespace": "ns1"}, result='{"pods": [], "events": []}')
        return {"context_snapshot": {"pods": [], "events": []}, "investigation_log": [record]}

    async def plan(state, config):
        if state.step_count < 2:
            call = {"name": "get_pod_status_tool", "args": {"pod_name": f"p{state.step_count}"}, "id": "c", "type": "tool_call"}
            return {"pending_tool_call": call, "plan_rationale": f"look at p{state.step_count}"}
        return {"pending_tool_call": None, "plan_rationale": ""}

    async def diagnose(state):
        return {"diagnosis": Diagnosis(root_cause=f"cause of {state.user_request}", confidence="high", citations=["e"])}

    async def ground(state, config):
        return {"reference_docs": [ReferenceDoc(title="Doc", source_url="https://kubernetes.io/x", content="c", score=0.9)]}

    async def recommend(state):
        return {"recommendation": f"fix for {state.user_request}"}

    async def answer_followup(state):
        return {"followup_answer": f"from {len(state.turns)} earlier turn(s)"}

    for fn in (intake, gather_context, plan, diagnose, ground, recommend, answer_followup):
        monkeypatch.setattr(build, fn.__name__, fn)
    # `execute_tool` runs for real against a fake tool.
    @tool
    def get_pod_status_tool(namespace: str | None = None, pod_name: str | None = None) -> str:
        """Fake pod status."""
        return json.dumps([{"name": pod_name}])

    monkeypatch.setattr(agent.nodes, "TOOLS_BY_NAME", {"get_pod_status_tool": get_pod_status_tool})
