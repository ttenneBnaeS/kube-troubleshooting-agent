"""Node functions for the troubleshooting graph.

See docs/architecture.md §7 for the responsibility of each node. Fact
gathering (`gather_context`, `execute_tool`) is plain Python against the
existing tool catalog; judgment (`intake`, `plan`, `diagnose`,
`recommend`) is LLM-driven, tier-routed through `models.config`.
"""

import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage

from graph.state import LOOP_GUARD_MAX, AgentState, Diagnosis, Scope, ToolCallRecord
from models.config import ModelTier, get_chat_model
from prompts import load_prompt
from rag import search_k8s_docs_tool
from tools import TOOLS, get_pod_status, get_recent_events

ALL_TOOLS = [*TOOLS, search_k8s_docs_tool]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}


def _investigation_summary(state: AgentState) -> str:
    parts = [
        f"User request: {state.user_request}",
        f"Scope: {state.scope.model_dump_json() if state.scope else '{}'}",
        f"Initial context snapshot: {json.dumps(state.context_snapshot)}",
    ]
    for record in state.investigation_log:
        parts.append(f"Tool `{record.tool_name}` called with {record.args} -> {record.result}")
    return "\n\n".join(parts)


async def intake(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.FAST).with_structured_output(Scope)
    messages = [
        SystemMessage(content=load_prompt("intake_v1")),
        *state.messages,
        HumanMessage(content=state.user_request),
    ]
    scope = await model.ainvoke(messages)
    return {"scope": scope}


def route_after_intake(state: AgentState) -> str:
    if state.scope and state.scope.needs_clarification:
        return "end"
    return "gather_context"


async def gather_context(state: AgentState) -> dict:
    namespace = state.scope.namespace if state.scope else None
    pods = await asyncio.to_thread(get_pod_status, namespace=namespace)
    events = await asyncio.to_thread(get_recent_events, namespace=namespace)
    snapshot = {
        "pods": [p.model_dump() for p in pods],
        "events": [e.model_dump() for e in events],
    }
    record = ToolCallRecord(
        tool_name="initial_sweep",
        args={"namespace": namespace},
        result=json.dumps(snapshot),
    )
    return {
        "context_snapshot": snapshot,
        "investigation_log": [*state.investigation_log, record],
    }


async def plan(state: AgentState) -> dict:
    if state.step_count >= LOOP_GUARD_MAX:
        return {"loop_guard_triggered": True, "pending_tool_call": None}

    model = get_chat_model(ModelTier.REASONING).bind_tools(ALL_TOOLS)
    messages = [
        SystemMessage(content=load_prompt("plan_v1")),
        *state.messages,
        HumanMessage(content=_investigation_summary(state)),
    ]
    response = await model.ainvoke(messages)
    tool_call = response.tool_calls[0] if response.tool_calls else None
    hypothesis = response.content if isinstance(response.content, str) and response.content else state.hypothesis
    return {"hypothesis": hypothesis, "pending_tool_call": tool_call}


def route_after_plan(state: AgentState) -> str:
    if state.loop_guard_triggered or not state.pending_tool_call:
        return "diagnose"
    return "execute_tool"


async def execute_tool(state: AgentState) -> dict:
    call = state.pending_tool_call
    tool = TOOLS_BY_NAME[call["name"]]
    result = await tool.ainvoke(call["args"])
    record = ToolCallRecord(tool_name=call["name"], args=call["args"], result=result)
    return {
        "investigation_log": [*state.investigation_log, record],
        "pending_tool_call": None,
        "step_count": state.step_count + 1,
    }


async def diagnose(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.REASONING).with_structured_output(Diagnosis)
    guard_note = (
        "\n\nNote: the investigation hit its step cap before the planner "
        "found enough evidence on its own. Diagnose from what's been "
        "gathered so far and lower `confidence` accordingly."
        if state.loop_guard_triggered
        else ""
    )
    messages = [
        SystemMessage(content=load_prompt("diagnose_v1") + guard_note),
        *state.messages,
        HumanMessage(content=_investigation_summary(state)),
    ]
    diagnosis = await model.ainvoke(messages)
    return {"diagnosis": diagnosis}


async def recommend(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.REASONING)
    messages = [
        SystemMessage(content=load_prompt("recommend_v1")),
        *state.messages,
        HumanMessage(
            content=f"{_investigation_summary(state)}\n\nDiagnosis: {state.diagnosis.model_dump_json() if state.diagnosis else '{}'}"
        ),
    ]
    response = await model.ainvoke(messages)
    return {"recommendation": response.content}
