"""The investigation trail: what the UI shows of how an answer was reached.

One step dict per thing that happened — the initial sweep, each tool
call, the diagnosis, the docs retrieved, or a follow-up answered from
earlier evidence. Built from the same records whether streamed live
(`event: step`, as each node finishes) or rebuilt from a stored
`TurnRecord` when the client reloads a thread, so the two can't drift.
"""

import json

from graph.state import Diagnosis, ReferenceDoc, ToolCallRecord, TurnRecord

INITIAL_SWEEP = "initial_sweep"
# Tool output can be a full log tail. The model saw all of it; the trail
# shows enough to recognise the evidence and says when it cut the rest.
RESULT_PREVIEW_CHARS = 4000
SWEEP_WARNINGS_SHOWN = 5


def _parse(result: str):
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return None


def _error_message(parsed) -> str | None:
    if isinstance(parsed, dict) and "error" in parsed:
        return parsed.get("message") or parsed["error"]
    return None


def _pod_problem(pod: dict) -> str | None:
    """The reason a pod isn't healthy, init containers first (they block the rest)."""
    for c in [*pod.get("init_containers", []), *pod.get("containers", [])]:
        if c.get("state") != "running" and c.get("reason") not in (None, "Completed", "PodInitializing"):
            return c["reason"]
    if pod.get("pod_ready"):
        return None
    # Containers running but the pod not Ready (a failing readiness probe)
    # has no container reason to report; the phase would just say Running.
    return "NotReady" if pod.get("phase") == "Running" else pod.get("phase") or "NotReady"


def sweep_step(record: ToolCallRecord) -> dict:
    snapshot = _parse(record.result) or {}
    pods, events = snapshot.get("pods"), snapshot.get("events")
    step: dict = {"type": "sweep", "namespace": record.args.get("namespace")}
    if isinstance(pods, list):
        step["pod_count"] = len(pods)
        step["unhealthy_pods"] = [
            {"name": p["name"], "reason": reason} for p in pods if (reason := _pod_problem(p))
        ]
    else:
        step["pods_error"] = _error_message(pods) or "pod listing failed"
    if isinstance(events, list):
        warnings = [e for e in events if e.get("type") == "Warning"]
        step["warning_count"] = len(warnings)
        step["warnings"] = [
            {"object": e["involved_object"], "reason": e["reason"], "message": e["message"]}
            for e in warnings[:SWEEP_WARNINGS_SHOWN]
        ]
    else:
        step["events_error"] = _error_message(events) or "event listing failed"
    return step


def tool_step(record: ToolCallRecord) -> dict:
    return {
        "type": "tool",
        "tool": record.tool_name,
        "args": record.args,
        "rationale": record.rationale,
        "result": record.result[:RESULT_PREVIEW_CHARS],
        "truncated": len(record.result) > RESULT_PREVIEW_CHARS,
        # A failed call is still evidence (a 404 often *is* the answer);
        # the UI marks it rather than hiding it.
        "error": _error_message(_parse(record.result)),
        "namespace_filled": record.namespace_filled,
    }


def log_step(record: ToolCallRecord) -> dict:
    return sweep_step(record) if record.tool_name == INITIAL_SWEEP else tool_step(record)


def diagnosis_step(diagnosis: Diagnosis) -> dict:
    return {"type": "diagnosis", **diagnosis.model_dump()}


def docs_step(docs: list[ReferenceDoc], error: str | None) -> dict:
    return {
        "type": "docs",
        "docs": [{"title": d.title, "url": d.source_url} for d in docs],
        "error": error,
    }


def followup_step() -> dict:
    return {"type": "followup"}


def turn_trail(turn: TurnRecord) -> list[dict]:
    """The full trail for a stored turn, in the order it would have streamed."""
    if turn.route == "followup":
        return [followup_step()]
    steps = [log_step(r) for r in turn.investigation_log]
    if turn.diagnosis:
        steps.append(diagnosis_step(turn.diagnosis))
    if turn.reference_docs:
        steps.append(docs_step(turn.reference_docs, None))
    return steps
