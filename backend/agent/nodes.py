"""Node functions for the troubleshooting graph.

See docs/architecture.md §7 for the responsibility of each node. Fact
gathering (`gather_context`, `execute_tool`) is plain Python against the
existing tool catalog; judgment (`intake`, `plan`, `diagnose`,
`recommend`, `answer_followup`) is LLM-driven, tier-routed through
`models.config`.
`finalize` is the only node that writes conversation state.
"""

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from graph.state import (
    DEFAULT_CLARIFYING_QUESTION,
    LOOP_GUARD_MAX,
    AgentState,
    Diagnosis,
    ReferenceDoc,
    Scope,
    ToolCallRecord,
    TurnRecord,
    TurnRoute,
)
from models.config import ModelTier, get_chat_model
from prompts import load_prompt
from rag import search_docs, search_k8s_docs_tool
from tools import TOOLS, get_pod_status, get_recent_events
from tools.errors import describe_tool_error

ALL_TOOLS = [*TOOLS, search_k8s_docs_tool]
TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

# Distinct doc pages handed to `recommend`. Retrieval returns chunks, and
# several can come from one page, so more chunks are fetched than this.
GROUNDING_DOCS = 3
_GROUNDING_CHUNKS = 8


def _rag_enabled(config: RunnableConfig | None) -> bool:
    """Docs retrieval on unless the caller opts out (`eval --no-rag` A/B)."""
    return (config or {}).get("configurable", {}).get("rag", True)


def _investigation_summary(state: AgentState) -> str:
    parts = [
        f"User request: {state.user_request}",
        f"Scope: {state.scope.model_dump_json() if state.scope else '{}'}",
        f"Initial context snapshot: {json.dumps(state.context_snapshot)}",
    ]
    for record in state.investigation_log:
        parts.append(f"Tool `{record.tool_name}` called with {record.args} -> {record.result}")
    return "\n\n".join(parts)


# Earlier investigations handed to `answer_followup`, newest last. Each
# carries its full tool output, so the whole conversation would grow
# without bound; the most recent few are what a follow-up is about.
FOLLOWUP_TURNS = 3


def _investigated_turns(state: AgentState) -> list[TurnRecord]:
    return [t for t in state.turns if t.route == "investigate" and t.investigation_log]


def _prior_findings(turns: list[TurnRecord]) -> str:
    """One line per earlier investigation, so `intake` knows what evidence exists."""
    if not turns:
        return "Earlier investigations in this conversation: none."
    lines = []
    for t in turns:
        tools = ", ".join(r.tool_name for r in t.investigation_log)
        cause = t.diagnosis.root_cause if t.diagnosis else "no diagnosis"
        lines.append(f"- {t.user_request!r} (scope {t.scope.model_dump_json() if t.scope else '{}'}): "
                     f"tools run: {tools}; diagnosis: {cause}")
    return "Earlier investigations in this conversation:\n" + "\n".join(lines)


async def intake(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.FAST).with_structured_output(Scope, method="json_schema")
    messages = [
        SystemMessage(content=load_prompt("intake_v3")),
        *state.messages,
        HumanMessage(content=f"{_prior_findings(_investigated_turns(state))}\n\nNew message: {state.user_request}"),
    ]
    scope = await model.ainvoke(messages)
    return {"scope": scope}


def route_after_intake(state: AgentState) -> str:
    scope = state.scope
    # Answering from earlier evidence beats both a clarifying question and
    # a re-run, but only if there *is* earlier evidence: on a first turn a
    # misclassified follow-up would otherwise skip the investigation and
    # answer from nothing.
    if scope and scope.is_followup and _investigated_turns(state):
        return "answer_followup"
    if scope and scope.needs_clarification:
        return "finalize"
    return "gather_context"


async def _sweep(fn, **kwargs):
    """Run one initial-sweep tool, returning its error as data if it fails.

    The sweep is deterministic and runs before any planning, so an
    unreachable cluster or a namespace that doesn't exist would otherwise
    abort the run with a raw exception instead of letting `plan` and
    `diagnose` say so in terms the user can act on.
    """
    try:
        results = await asyncio.to_thread(fn, **kwargs)
    except Exception as exc:
        return describe_tool_error(exc)
    return [r.model_dump() for r in results]


async def gather_context(state: AgentState) -> dict:
    namespace = state.scope.namespace if state.scope else None
    snapshot = {
        "pods": await _sweep(get_pod_status, namespace=namespace),
        "events": await _sweep(get_recent_events, namespace=namespace),
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


async def plan(state: AgentState, config: RunnableConfig) -> dict:
    if state.step_count >= LOOP_GUARD_MAX:
        return {"loop_guard_triggered": True, "pending_tool_call": None}

    tools = ALL_TOOLS if _rag_enabled(config) else TOOLS
    model = get_chat_model(ModelTier.REASONING).bind_tools(tools)
    messages = [
        SystemMessage(content=load_prompt("plan_v1")),
        *state.messages,
        HumanMessage(content=_investigation_summary(state)),
    ]
    response = await model.ainvoke(messages)
    tool_call = response.tool_calls[0] if response.tool_calls else None
    hypothesis = response.text or state.hypothesis
    return {"hypothesis": hypothesis, "pending_tool_call": tool_call}


def route_after_plan(state: AgentState) -> str:
    if state.loop_guard_triggered or not state.pending_tool_call:
        return "diagnose"
    return "execute_tool"


async def execute_tool(state: AgentState) -> dict:
    call = state.pending_tool_call
    tool = TOOLS_BY_NAME.get(call["name"])
    args = dict(call["args"])

    # Every namespaced tool takes `namespace` as optional, and an omitted
    # one falls back to the configured default (usually `default`) — not
    # the namespace the user asked about. The planner drops it often enough
    # that eval caught it misrouting calls: a 404 from the wrong namespace
    # reads as "that resource doesn't exist". So fill in the scope's
    # namespace here; an explicit one (e.g. a cross-namespace lookup) wins.
    scope_ns = state.scope.namespace if state.scope else None
    namespace_filled = False
    if tool is not None and "namespace" in tool.args and not args.get("namespace") and scope_ns:
        args["namespace"] = scope_ns
        namespace_filled = True

    # A tool call that fails is usually evidence, not an accident — asking
    # for a Secret and getting 404 is how "the referenced Secret doesn't
    # exist" gets confirmed. Letting the exception escape would kill the
    # whole run at the exact moment the answer arrived, so failures are
    # recorded into the investigation log and the planner decides what
    # they mean.
    if tool is None:
        result = json.dumps({"error": "unknown_tool", "message": f"no tool named {call['name']!r}"})
    else:
        try:
            result = await tool.ainvoke(args)
        except Exception as exc:
            result = json.dumps(describe_tool_error(exc))

    # Log the args actually used, so the planner sees which namespace it
    # really queried.
    record = ToolCallRecord(
        tool_name=call["name"], args=args, result=result, namespace_filled=namespace_filled
    )
    return {
        "investigation_log": [*state.investigation_log, record],
        "pending_tool_call": None,
        "step_count": state.step_count + 1,
    }


async def diagnose(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.REASONING).with_structured_output(Diagnosis, method="json_schema")
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


async def ground(state: AgentState, config: RunnableConfig) -> dict:
    """Retrieve docs for the diagnosed cause, for `recommend` to cite.

    Deterministic, like `gather_context`: the planner almost never chose to
    search the docs (eval measured zero calls), because the evidence alone
    settles the diagnosis. Where docs earn their place is the fix, so they
    are fetched here for every diagnosis rather than left to a model's
    choice. A failed search is recorded and the run continues without docs.
    """
    root_cause = state.diagnosis.root_cause if state.diagnosis else ""
    if not _rag_enabled(config) or not root_cause.strip():
        return {"reference_docs": []}
    try:
        chunks = await asyncio.to_thread(search_docs, root_cause, _GROUNDING_CHUNKS)
    except Exception as exc:
        return {"reference_docs": [], "grounding_error": f"{type(exc).__name__}: {exc}"}

    # One excerpt per page (the best-scoring chunk), so three citations can
    # point at three different pages.
    docs: dict[str, ReferenceDoc] = {}
    for chunk in chunks:
        url = chunk.get("source_url")
        if url and url not in docs:
            docs[url] = ReferenceDoc(
                title=chunk.get("title") or url,
                source_url=url,
                content=chunk["content"],
                score=chunk["score"],
            )
        if len(docs) == GROUNDING_DOCS:
            break
    return {"reference_docs": list(docs.values())}


def _reference_docs_section(docs: list[ReferenceDoc]) -> str:
    if not docs:
        return "Reference documentation: none retrieved. Cite no documentation."
    entries = [f"[{i}] {d.title} — {d.source_url}\n{d.content}" for i, d in enumerate(docs, start=1)]
    return "Reference documentation (the only sources you may cite):\n\n" + "\n\n".join(entries)


async def recommend(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.REASONING)
    messages = [
        SystemMessage(content=load_prompt("recommend_v2")),
        *state.messages,
        HumanMessage(
            content=(
                f"{_investigation_summary(state)}\n\n"
                f"Diagnosis: {state.diagnosis.model_dump_json() if state.diagnosis else '{}'}\n\n"
                f"{_reference_docs_section(state.reference_docs)}"
            )
        ),
    ]
    response = await model.ainvoke(messages)
    # `.text`, not `.content`: when the model emits a thinking block,
    # `content` is a list of blocks rather than a string, which fails
    # AgentState validation after the run has already finished. Eval
    # caught this on `logtail`; it doesn't happen on every response.
    return {"recommendation": response.text}


def _turn_evidence(turn: TurnRecord) -> str:
    parts = [f"Request: {turn.user_request}", f"Scope: {turn.scope.model_dump_json() if turn.scope else '{}'}"]
    for record in turn.investigation_log:
        parts.append(f"Tool `{record.tool_name}` called with {record.args} -> {record.result}")
    parts.append(f"Diagnosis: {turn.diagnosis.model_dump_json() if turn.diagnosis else '{}'}")
    if turn.reference_docs:
        parts.append(_reference_docs_section(turn.reference_docs))
    return "\n\n".join(parts)


async def answer_followup(state: AgentState) -> dict:
    model = get_chat_model(ModelTier.REASONING)
    turns = _investigated_turns(state)[-FOLLOWUP_TURNS:]
    evidence = "\n\n---\n\n".join(
        f"Earlier investigation {i}:\n\n{_turn_evidence(t)}" for i, t in enumerate(turns, start=1)
    )
    messages = [
        SystemMessage(content=load_prompt("followup_v1")),
        *state.messages,
        HumanMessage(content=f"{evidence}\n\n---\n\nFollow-up question: {state.user_request}"),
    ]
    response = await model.ainvoke(messages)
    return {"followup_answer": response.text}


def _turn_route(state: AgentState) -> TurnRoute:
    if state.followup_answer is not None:
        return "followup"
    if state.recommendation is not None:
        return "investigate"
    return "clarify"


def _reply_text(state: AgentState) -> str:
    """What the user was shown this turn."""
    if state.followup_answer is not None:
        return state.followup_answer
    if state.recommendation is not None:
        return state.recommendation
    question = state.scope.clarifying_question if state.scope else None
    return question or DEFAULT_CLARIFYING_QUESTION


async def finalize(state: AgentState) -> dict:
    # Every turn ends here, including one `intake` ended early with a
    # clarifying question: recording that question in `messages` is what
    # lets the user's answer to it make sense on the next turn.
    reply = _reply_text(state)
    turn = TurnRecord(
        user_request=state.user_request,
        route=_turn_route(state),
        reply=reply,
        scope=state.scope,
        investigation_log=state.investigation_log,
        diagnosis=state.diagnosis,
        reference_docs=state.reference_docs,
        recommendation=state.recommendation,
        loop_guard_triggered=state.loop_guard_triggered,
    )
    return {
        "messages": [HumanMessage(content=state.user_request), AIMessage(content=reply)],
        "turns": [turn],
    }
