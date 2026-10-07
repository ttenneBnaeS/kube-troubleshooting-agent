"""The investigation-trail serializer shared by streaming and thread rehydration."""

import json

from api import trail
from graph.state import Diagnosis, ReferenceDoc, ToolCallRecord, TurnRecord


def sweep(pods, events):
    return ToolCallRecord(tool_name="initial_sweep", args={"namespace": "ns"}, result=json.dumps({"pods": pods, "events": events}))


def pod(name, *, ready, phase="Running", containers=(), init=()):
    return {"name": name, "pod_ready": ready, "phase": phase, "containers": list(containers), "init_containers": list(init)}


def test_sweep_summarizes_unhealthy_pods_and_warnings():
    pods = [
        pod("ok", ready=True, containers=[{"state": "running", "reason": None}]),
        pod("crash", ready=False, containers=[{"state": "waiting", "reason": "CrashLoopBackOff"}]),
        # Blocked in init: the init container's reason is the real one,
        # not the app container's PodInitializing.
        pod("init", ready=False, phase="Pending",
            containers=[{"state": "waiting", "reason": "PodInitializing"}],
            init=[{"state": "waiting", "reason": "CrashLoopBackOff"}]),
        # Running but failing readiness: no container reason at all.
        pod("unready", ready=False, containers=[{"state": "running", "reason": None}]),
    ]
    events = [
        {"type": "Warning", "reason": "BackOff", "message": "m", "involved_object": "Pod/crash"},
        {"type": "Normal", "reason": "Pulled", "message": "m", "involved_object": "Pod/ok"},
    ]
    step = trail.sweep_step(sweep(pods, events))
    assert step["pod_count"] == 4
    assert step["unhealthy_pods"] == [
        {"name": "crash", "reason": "CrashLoopBackOff"},
        {"name": "init", "reason": "CrashLoopBackOff"},
        {"name": "unready", "reason": "NotReady"},
    ]
    assert step["warning_count"] == 1
    assert step["warnings"][0]["object"] == "Pod/crash"


def test_sweep_reports_failed_listings():
    step = trail.sweep_step(sweep({"error": "forbidden", "message": "nope"}, []))
    assert step["pods_error"] == "nope"
    assert "unhealthy_pods" not in step


def test_tool_step_marks_errors_and_truncates():
    error = ToolCallRecord(tool_name="describe_resource_tool", args={}, result='{"error": "not_found", "message": "gone"}')
    assert trail.tool_step(error)["error"] == "gone"

    big = ToolCallRecord(tool_name="get_container_logs_tool", args={}, result="x" * (trail.RESULT_PREVIEW_CHARS + 10))
    step = trail.tool_step(big)
    assert step["truncated"] is True and len(step["result"]) == trail.RESULT_PREVIEW_CHARS
    assert step["error"] is None


def test_turn_trail_order_matches_streaming():
    turn = TurnRecord(
        user_request="q",
        route="investigate",
        investigation_log=[sweep([], []), ToolCallRecord(tool_name="t", args={}, result="[]")],
        diagnosis=Diagnosis(root_cause="c", confidence="medium"),
        reference_docs=[ReferenceDoc(title="T", source_url="https://u", content="c", score=1.0)],
    )
    assert [s["type"] for s in trail.turn_trail(turn)] == ["sweep", "tool", "diagnosis", "docs"]


def test_followup_and_clarify_trails():
    assert trail.turn_trail(TurnRecord(user_request="q", route="followup")) == [{"type": "followup"}]
    assert trail.turn_trail(TurnRecord(user_request="q", route="clarify")) == []
