from typing import Literal

from .client import get_apps_v1_api, get_core_v1_api
from .config import settings
from .events import get_recent_events
from .models import DescribeResult

DescribableKind = Literal["pod", "deployment", "service", "node", "configmap", "secret"]


def describe_resource(
    kind: DescribableKind,
    name: str,
    namespace: str | None = None,
) -> DescribeResult:
    """Read-only: structured status/conditions for a resource plus its recent related events.

    Unlike `kubectl describe`, there's no text to parse — this composes the
    object's own status fields with a filtered event lookup. Supports pod,
    deployment, service, node, configmap, and secret; other kinds can be
    added the same way.
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
        "labels": dict(pod.metadata.labels or {}),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (pod.status.conditions or [])
        ],
        # Init statuses are a separate list on the API object; omitting
        # them here would leave describe as blind to an init failure as
        # get_pod_status was.
        "init_container_statuses": [_status_summary(cs) for cs in (pod.status.init_container_statuses or [])],
        "container_statuses": [_status_summary(cs) for cs in (pod.status.container_statuses or [])],
        "node_name": pod.spec.node_name,
    }


def _status_summary(cs) -> dict:
    return {
        "name": cs.name,
        "ready": cs.ready,
        "restart_count": cs.restart_count,
        "state": _container_state_summary(cs),
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


def _summarize_configmap(name: str, namespace: str) -> dict:
    cm = get_core_v1_api().read_namespaced_config_map(name=name, namespace=namespace)
    data = cm.data or {}
    return {
        # Keys, not values: the failure mode this exists for is a pod
        # referencing a key that isn't there (a typo or a case mismatch),
        # which the key list answers directly. Whole config bodies would
        # be unbounded token cost for no diagnostic gain.
        "keys": sorted(data),
        "binary_keys": sorted(cm.binary_data or {}),
        "value_sizes_bytes": {k: len(v) for k, v in sorted(data.items())},
    }


def _summarize_secret(name: str, namespace: str) -> dict:
    secret = get_core_v1_api().read_namespaced_secret(name=name, namespace=namespace)
    data = secret.data or {}
    return {
        # Key names and sizes only — never the values, not even
        # base64-encoded. A read-only boundary that still hands secret
        # material to an LLM isn't a boundary; "which keys exist" is all
        # a missing-key diagnosis needs.
        "type": secret.type,
        "keys": sorted(data),
        "value_sizes_bytes": {k: len(v) for k, v in sorted(data.items())},
    }


_SUMMARIZERS = {
    "pod": _summarize_pod,
    "deployment": _summarize_deployment,
    "service": _summarize_service,
    "node": _summarize_node,
    "configmap": _summarize_configmap,
    "secret": _summarize_secret,
}
