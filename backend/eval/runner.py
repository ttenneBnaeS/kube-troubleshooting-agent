"""Run one scenario end to end and produce a scored record.

Per scenario: create a throwaway namespace, apply the manifest, wait for
the failure to actually manifest, run the agent graph against it, score
the result, tear the namespace down.

Runs are sequential on purpose. Scenarios that share a cluster contend for
node resources (the OOM scenario in particular), and the RAG docs-search
tool sits behind Voyage's free-tier rate limits — parallel runs would turn
both into flaky failures that look like agent errors.
"""

import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field

from langgraph.checkpoint.memory import InMemorySaver

from graph import build_graph, new_turn_input
from graph.state import AgentState, checkpoint_serializer

from . import cluster
from .scenarios import FollowUp, Scenario
from .scorers import DiagnosisScore, cited_urls, corpus_urls, missing_signals, score_diagnosis
from .tracing import run_config

# The initial sweep is a fixed deterministic node, not a planner decision,
# so it is excluded from the tool-efficiency number.
INITIAL_SWEEP = "initial_sweep"

# ...but it *is* a pod-status call and an events call (`gather_context`
# invokes exactly those two functions), so evidence they provide has
# genuinely been gathered. Crediting the sweep for them keeps the
# "expected evidence not gathered" column honest: without this, every
# scenario reports get_pod_status/get_recent_events as missed even when
# the sweep put that evidence in front of the model on turn one.
SWEEP_EQUIVALENT_TOOLS = frozenset({"get_pod_status_tool", "get_recent_events_tool"})

# Same checkpointed graph the API runs, minus the SQLite file: each run
# gets its own thread, so scenarios never see each other's conversation.
_graph = build_graph(InMemorySaver(serde=checkpoint_serializer()))


@dataclass
class ToolCallSummary:
    tool_name: str
    args: dict
    result_chars: int
    namespace_filled: bool = False


@dataclass
class FollowUpRecord:
    request: str
    expected_route: str
    route: str = ""
    planner_tool_calls: int = 0
    reply: str = ""
    missing_signals: list[list[str]] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str = ""

    @property
    def route_correct(self) -> bool:
        return self.route == self.expected_route

    @property
    def answered(self) -> bool:
        return not self.error and not self.missing_signals

    @property
    def passed(self) -> bool:
        return self.route_correct and self.answered

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "route_correct": self.route_correct,
            "answered": self.answered,
            "passed": self.passed,
        }


@dataclass
class RunRecord:
    scenario_id: str
    namespace: str
    user_request: str
    status: str  # "scored" | "setup_failed" | "agent_failed" | "scoring_failed"
    difficulty: str = "medium"
    duration_seconds: float = 0.0

    diagnosis_root_cause: str = ""
    diagnosis_confidence: str = ""
    diagnosis_citations: list[str] = field(default_factory=list)
    recommendation: str = ""

    # `intake` can end a run early by asking for clarification. That still
    # counts as a failed diagnosis — the scenario was answerable and the
    # agent declined to investigate — but it is a different failure from a
    # wrong root cause, so it is recorded separately rather than being
    # dropped from the denominator and flattering the accuracy number.
    scope_namespace: str = ""
    clarification_requested: bool = False
    clarifying_question: str = ""

    tool_calls: list[ToolCallSummary] = field(default_factory=list)
    planner_tool_calls: int = 0
    # Planner calls that omitted `namespace` and had the scope's filled in.
    namespace_fills: int = 0
    loop_guard_triggered: bool = False
    # Grounding: doc pages `ground` retrieved, the URLs the recommendation
    # cited, and any cited URL that isn't in the indexed corpus (made up).
    reference_doc_urls: list[str] = field(default_factory=list)
    grounding_error: str = ""
    cited_urls: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    expected_tools_used: list[str] = field(default_factory=list)
    expected_tools_missed: list[str] = field(default_factory=list)

    score: DiagnosisScore | None = None
    error: str = ""
    setup_detail: str = ""
    follow_ups: list[FollowUpRecord] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        return bool(self.score and self.score.correct)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score"] = self.score.to_dict() if self.score else None
        data["correct"] = self.correct
        data["follow_ups"] = [f.to_dict() for f in self.follow_ups]
        return data


async def run_scenario(
    scenario: Scenario,
    *,
    use_judge: bool = True,
    rag: bool = True,
    keep_namespace: bool = False,
    setup_timeout: int = cluster.DEFAULT_TIMEOUT_SECONDS,
    follow_ups: bool = True,
) -> RunRecord:
    namespace = scenario.namespace
    record = RunRecord(
        scenario_id=scenario.id,
        namespace=namespace,
        user_request=scenario.request_for(namespace),
        status="setup_failed",
        difficulty=scenario.difficulty,
    )

    try:
        cluster.setup_scenario(scenario)
        ready = cluster.wait_until_broken(scenario, namespace, timeout_seconds=setup_timeout)
        record.setup_detail = ready.detail
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        if not keep_namespace:
            cluster.teardown_all(scenario)
        return record

    try:
        started = time.monotonic()
        config = run_config(scenario.id, namespace, rag=rag)
        config["configurable"]["thread_id"] = f"eval-{scenario.id}-{uuid.uuid4().hex[:8]}"
        final_state = await _graph.ainvoke(new_turn_input(record.user_request), config=config)
        record.duration_seconds = round(time.monotonic() - started, 1)
        _populate_from_state(record, final_state, scenario)
        record.status = "scored"
        # Before teardown: an "is it fixed yet?" turn needs the scenario
        # still in its broken state.
        if follow_ups:
            for follow_up in scenario.follow_ups:
                record.follow_ups.append(await _run_follow_up(follow_up, namespace, config))
    except Exception as exc:
        record.status = "agent_failed"
        record.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        if not keep_namespace:
            cluster.teardown_all(scenario)
        return record

    try:
        record.score = await score_diagnosis(
            scenario.golden,
            record.diagnosis_root_cause,
            record.recommendation,
            use_judge=use_judge,
        )
    except Exception as exc:
        # The agent finished; only grading broke. Don't count it against the agent.
        record.status = "scoring_failed"
        record.error = f"scoring failed: {type(exc).__name__}: {exc}"
    finally:
        if not keep_namespace:
            cluster.teardown_all(scenario)

    return record


async def _run_follow_up(follow_up: FollowUp, namespace: str, config: dict) -> FollowUpRecord:
    """Run one later turn on the scenario's thread. Failures are recorded, not raised."""
    record = FollowUpRecord(request=follow_up.request.format(namespace=namespace), expected_route=follow_up.expected_route)
    try:
        started = time.monotonic()
        final_state = await _graph.ainvoke(new_turn_input(record.request), config=config)
        record.duration_seconds = round(time.monotonic() - started, 1)
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        return record
    state = final_state if isinstance(final_state, AgentState) else AgentState(**final_state)
    turn = state.turns[-1]
    record.route = turn.route
    record.reply = turn.reply
    record.planner_tool_calls = sum(1 for c in turn.investigation_log if c.tool_name != INITIAL_SWEEP)
    record.missing_signals = missing_signals(follow_up.required_signals, turn.reply)
    return record


def _populate_from_state(record: RunRecord, final_state, scenario: Scenario) -> None:
    state = final_state if isinstance(final_state, AgentState) else AgentState(**final_state)

    if state.diagnosis:
        record.diagnosis_root_cause = state.diagnosis.root_cause
        record.diagnosis_confidence = state.diagnosis.confidence
        record.diagnosis_citations = list(state.diagnosis.citations)
    record.recommendation = state.recommendation or ""
    record.reference_doc_urls = [d.source_url for d in state.reference_docs]
    record.grounding_error = state.grounding_error or ""
    record.cited_urls = cited_urls(record.recommendation)
    record.invalid_citations = [u for u in record.cited_urls if u not in corpus_urls()]
    record.loop_guard_triggered = state.loop_guard_triggered

    if state.scope:
        record.scope_namespace = state.scope.namespace or ""
        record.clarification_requested = state.scope.needs_clarification
        record.clarifying_question = state.scope.clarifying_question or ""

    record.tool_calls = [
        ToolCallSummary(
            tool_name=c.tool_name,
            args=c.args,
            result_chars=len(c.result),
            namespace_filled=c.namespace_filled,
        )
        for c in state.investigation_log
    ]
    record.namespace_fills = sum(1 for c in record.tool_calls if c.namespace_filled)
    planner_calls = [c for c in record.tool_calls if c.tool_name != INITIAL_SWEEP]
    record.planner_tool_calls = len(planner_calls)
    record.tools_used = sorted({c.tool_name for c in planner_calls})

    expected = set(scenario.golden.expected_evidence_tools)
    gathered = set(record.tools_used)
    if any(c.tool_name == INITIAL_SWEEP for c in record.tool_calls):
        gathered |= SWEEP_EQUIVALENT_TOOLS
    record.expected_tools_used = sorted(expected & gathered)
    record.expected_tools_missed = sorted(expected - gathered)
