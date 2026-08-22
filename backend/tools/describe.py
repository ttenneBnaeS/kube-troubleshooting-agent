from typing import Literal

from .client import get_apps_v1_api, get_core_v1_api
from .config import settings
from .events import get_recent_events
from .models import DescribeResult

DescribableKind = Literal["pod", "deployment", "service", "node"]


def describe_resource(
    kind: DescribableKind,
    name: str,
    namespace: str | None = None,
) -> DescribeResult:
    """Read-only: structured status/conditions for a resource plus its recent related events.

    Unlike `kubectl describe`, there's no text to parse — this composes the
    object's own status fields with a filtered event lookup. Supports pod,
    deployment, service, and node; other kinds can be added the same way.
    """
    ns = namespace or settings.namespace
    summary = _SUMMARIZERS[kind](name, ns)
    events = get_recent_events(namespace=ns, involved_object_name=name)

    return DescribeResult(
        kind=kind,
        name=name,
        namespace=None if kind == "node" else ns,
        summary=summary,
        recent_events=events,
    )


def _summarize_pod(name: str, namespace: str) -> dict:
    pod = get_core_v1_api().read_namespaced_pod(name=name, namespace=namespace)
    return {
        "phase": pod.status.phase,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (pod.status.conditions or [])
        ],
        "container_statuses": [
            {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "state": _container_state_summary(cs),
            }
            for cs in (pod.status.container_statuses or [])
        ],
        "node_name": pod.spec.node_name,
    }


def _container_state_summary(cs) -> dict:
    state = cs.state
    if state.waiting:
        return {"waiting": {"reason": state.waiting.reason, "message": state.waiting.message}}
    if state.terminated:
        return {
            "terminated": {
                "reason": state.terminated.reason,
                "exit_code": state.terminated.exit_code,
                "message": state.terminated.message,
            }
        }
    if state.running:
        return {"running": {"started_at": str(state.running.started_at)}}
    return {}


def _summarize_deployment(name: str, namespace: str) -> dict:
    dep = get_apps_v1_api().read_namespaced_deployment(name=name, namespace=namespace)
    status = dep.status
    return {
        "desired_replicas": dep.spec.replicas,
        "ready_replicas": status.ready_replicas or 0,
        "available_replicas": status.available_replicas or 0,
        "updated_replicas": status.updated_replicas or 0,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ],
    }


def _summarize_service(name: str, namespace: str) -> dict:
    svc = get_core_v1_api().read_namespaced_service(name=name, namespace=namespace)
    return {
        "type": svc.spec.type,
        "cluster_ip": svc.spec.cluster_ip,
        "selector": dict(svc.spec.selector or {}),
        "ports": [
            {"port": p.port, "target_port": str(p.target_port), "protocol": p.protocol}
            for p in (svc.spec.ports or [])
        ],
    }


def _summarize_node(name: str, _namespace: str) -> dict:
    node = get_core_v1_api().read_node(name=name)
    return {
        "unschedulable": bool(node.spec.unschedulable),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (node.status.conditions or [])
        ],
        "capacity": dict(node.status.capacity or {}),
        "allocatable": dict(node.status.allocatable or {}),
    }


_SUMMARIZERS = {
    "pod": _summarize_pod,
    "deployment": _summarize_deployment,
    "service": _summarize_service,
    "node": _summarize_node,
}
