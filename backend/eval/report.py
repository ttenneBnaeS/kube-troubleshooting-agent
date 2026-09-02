"""Turn run records into the number the project is actually claiming.

Writes a full JSON record per run to `results/` (every tool call, its
args, the judge's reasoning) so a failure can be picked apart afterwards,
and prints a summary whose headline line is the one the README and resume
quote: N of M correct at K planner tool calls on average.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .runner import RunRecord

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class Summary:
    total: int
    scored: int
    correct: int
    remediation_ok: int
    failed_setup: int
    failed_agent: int
    mean_tool_calls: float
    loop_guard_hits: int
    disagreements: int
    clarification_requests: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.scored if self.scored else 0.0

    @property
    def headline(self) -> str:
        return (
            f"correctly diagnosed {self.correct}/{self.scored} scenarios "
            f"({self.accuracy:.0%}) at an average of {self.mean_tool_calls:.1f} "
            f"planner tool calls per diagnosis"
        )


TIERS = ("easy", "medium", "hard")


@dataclass
class TierSummary:
    difficulty: str
    scored: int
    correct: int
    mean_tool_calls: float

    @property
    def line(self) -> str:
        pct = f"{self.correct / self.scored:.0%}" if self.scored else "n/a"
        return (
            f"{self.difficulty:<7} {self.correct}/{self.scored} ({pct})"
            f"  mean tools {self.mean_tool_calls:.1f}"
        )


def summarize_by_tier(records: list[RunRecord]) -> list[TierSummary]:
    """Per-difficulty breakdown.

    A single blended accuracy number stops being interpretable once the
    suite mixes trivial and adversarial cases — 11/14 doesn't say which
    kind failed. The easy tier is a regression guard whose mean tool count
    matters as much as its accuracy: inflation there means the agent has
    started over-investigating things it used to get right immediately.
    """
    out = []
    for tier in TIERS:
        scored = [r for r in records if r.status == "scored" and r.difficulty == tier]
        if not scored:
            continue
        counts = [r.planner_tool_calls for r in scored]
        out.append(
            TierSummary(
                difficulty=tier,
                scored=len(scored),
                correct=sum(1 for r in scored if r.correct),
                mean_tool_calls=sum(counts) / len(counts),
            )
        )
    return out


def summarize(records: list[RunRecord]) -> Summary:
    scored = [r for r in records if r.status == "scored"]
    tool_counts = [r.planner_tool_calls for r in scored]
    return Summary(
        total=len(records),
        scored=len(scored),
        correct=sum(1 for r in scored if r.correct),
        remediation_ok=sum(1 for r in scored if r.score and r.score.remediation_appropriate),
        failed_setup=sum(1 for r in records if r.status == "setup_failed"),
        failed_agent=sum(1 for r in records if r.status == "agent_failed"),
        mean_tool_calls=sum(tool_counts) / len(tool_counts) if tool_counts else 0.0,
        loop_guard_hits=sum(1 for r in scored if r.loop_guard_triggered),
        disagreements=sum(1 for r in scored if r.score and not r.score.scorers_agree),
        clarification_requests=sum(1 for r in scored if r.clarification_requested),
    )


def write_results(records: list[RunRecord], summary: Summary, tracing_on: bool) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"run-{stamp}.json"
    payload = {
        "timestamp": stamp,
        "tracing_enabled": tracing_on,
        "summary": {
            **summary.__dict__,
            "accuracy": summary.accuracy,
            "headline": summary.headline,
            "by_difficulty": [t.__dict__ for t in summarize_by_tier(records)],
        },
        "runs": [r.to_dict() for r in records],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def _status_mark(record: RunRecord) -> str:
    if record.status == "setup_failed":
        return "SETUP"
    if record.status == "agent_failed":
        return "ERROR"
    if record.clarification_requested:
        return "ASKED"
    return "PASS" if record.correct else "FAIL"


def print_report(records: list[RunRecord], summary: Summary, results_path: Path) -> None:
    rows = [
        (
            _status_mark(r),
            r.scenario_id,
            r.difficulty,
            str(r.planner_tool_calls) if r.status == "scored" else "-",
            r.diagnosis_confidence or "-",
            f"{r.duration_seconds:.0f}s" if r.duration_seconds else "-",
        )
        for r in records
    ]
    headers = ("RESULT", "SCENARIO", "TIER", "TOOLS", "CONF", "TIME")
    widths = (
        [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
        if rows
        else [len(h) for h in headers]
    )

    print()
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))

    print()
    for record in records:
        detail = _failure_detail(record)
        if detail:
            print(f"  {record.scenario_id}: {detail}")

    print()
    for tier in summarize_by_tier(records):
        print(f"  {tier.line}")

    print()
    print(summary.headline)
    print(
        f"remediation appropriate in {summary.remediation_ok}/{summary.scored}; "
        f"loop guard hit {summary.loop_guard_hits}x; "
        f"scorer disagreements {summary.disagreements}; "
        f"ended at intake {summary.clarification_requests}x"
    )
    if summary.failed_setup or summary.failed_agent:
        print(f"not scored: {summary.failed_setup} setup failure(s), {summary.failed_agent} agent error(s)")
    print(f"full records: {results_path}")


def _failure_detail(record: RunRecord) -> str:
    if record.status != "scored":
        return record.error.splitlines()[0] if record.error else record.status
    score = record.score
    if score is None:
        return ""

    notes = []
    if record.clarification_requested:
        return f"ended at intake with a clarifying question: {record.clarifying_question!r}"
    if not record.correct:
        if score.judge:
            notes.append(f"judge: {score.judge.reasoning}")
        if score.signal_check.missing_signals:
            missing = "; ".join("/".join(g) for g in score.signal_check.missing_signals)
            notes.append(f"missing signals: {missing}")
    else:
        # A correct verdict that the keyword check disagrees with means one
        # of the two scorers needs attention, so surface it either way.
        if not score.scorers_agree and score.signal_check.missing_signals:
            missing = "; ".join("/".join(g) for g in score.signal_check.missing_signals)
            notes.append(f"judge passed but signal check missed: {missing}")
        if record.expected_tools_missed:
            notes.append(f"expected-but-unused tools: {', '.join(record.expected_tools_missed)}")
    if score.signal_check.forbidden_hits:
        notes.append(f"forbidden terms present (advisory): {', '.join(score.signal_check.forbidden_hits)}")
    return " | ".join(notes)
