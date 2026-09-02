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
from dataclasses import asdict, dataclass, field

from graph import troubleshooting_graph
from graph.state import AgentState

from . import cluster
from .scenarios import Scenario
from .scorers import DiagnosisScore, score_diagnosis
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


@dataclass
class ToolCallSummary:
    tool_name: str
    args: dict
    result_chars: int


@dataclass
class RunRecord:
    scenario_id: str
    namespace: str
    user_request: str
    status: str  # "scored" | "setup_failed" | "agent_failed"
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
    loop_guard_triggered: bool = False
    tools_used: list[str] = field(default_factory=list)
    expected_tools_used: list[str] = field(default_factory=list)
    expected_tools_missed: list[str] = field(default_factory=list)

    score: DiagnosisScore | None = None
    error: str = ""
    setup_detail: str = ""

    @property
    def correct(self) -> bool:
        return bool(self.score and self.score.correct)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["score"] = self.score.to_dict() if self.score else None
        data["correct"] = self.correct
        return data


async def run_scenario(
    scenario: Scenario,
    *,
    use_judge: bool = True,
    keep_namespace: bool = False,
    setup_timeout: int = cluster.DEFAULT_TIMEOUT_SECONDS,
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
        final_state = await troubleshooting_graph.ainvoke(
            {"user_request": record.user_request, "messages": []},
            config=run_config(scenario.id, namespace),
        )
        record.duration_seconds = round(time.monotonic() - started, 1)
        _populate_from_state(record, final_state, scenario)
        record.status = "scored"
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
        record.status = "agent_failed"
        record.error = f"scoring failed: {type(exc).__name__}: {exc}"
    finally:
        if not keep_namespace:
            cluster.teardown_all(scenario)

    return record


def _populate_from_state(record: RunRecord, final_state, scenario: Scenario) -> None:
    state = final_state if isinstance(final_state, AgentState) else AgentState(**final_state)

    if state.diagnosis:
        record.diagnosis_root_cause = state.diagnosis.root_cause
        record.diagnosis_confidence = state.diagnosis.confidence
        record.diagnosis_citations = list(state.diagnosis.citations)
    record.recommendation = state.recommendation or ""
    record.loop_guard_triggered = state.loop_guard_triggered

    if state.scope:
        record.scope_namespace = state.scope.namespace or ""
        record.clarification_requested = state.scope.needs_clarification
        record.clarifying_question = state.scope.clarifying_question or ""

    record.tool_calls = [
        ToolCallSummary(tool_name=c.tool_name, args=c.args, result_chars=len(c.result))
        for c in state.investigation_log
    ]
    planner_calls = [c for c in record.tool_calls if c.tool_name != INITIAL_SWEEP]
    record.planner_tool_calls = len(planner_calls)
    record.tools_used = sorted({c.tool_name for c in planner_calls})

    expected = set(scenario.golden.expected_evidence_tools)
    gathered = set(record.tools_used)
    if any(c.tool_name == INITIAL_SWEEP for c in record.tool_calls):
        gathered |= SWEEP_EQUIVALENT_TOOLS
    record.expected_tools_used = sorted(expected & gathered)
    record.expected_tools_missed = sorted(expected - gathered)
