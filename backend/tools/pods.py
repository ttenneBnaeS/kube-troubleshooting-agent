from datetime import UTC, datetime

from kubernetes import client as k8s

from .client import get_core_v1_api
from .config import settings
from .models import ContainerState, PodStatusResult


def get_pod_status(namespace: str | None = None, pod_name: str | None = None) -> list[PodStatusResult]:
    """Read-only: list or get pods, normalized to phase/container-state/restarts/age."""
    ns = namespace or settings.namespace
    api = get_core_v1_api()

    if pod_name:
        pods = [api.read_namespaced_pod(name=pod_name, namespace=ns)]
    else:
        pods = api.list_namespaced_pod(namespace=ns).items

    return [_normalize_pod(pod) for pod in pods]


def _normalize_pod(pod: k8s.V1Pod) -> PodStatusResult:
    statuses = pod.status.container_statuses or []
    # Init container statuses live in a separate list on the API object.
    # Reading only `container_statuses` left an init failure undiagnosable:
    # the app container reports waiting/PodInitializing, which says the pod
    # is blocked behind init but names neither the failing init container
    # nor its reason, and nothing else in the result did either.
    init_statuses = pod.status.init_container_statuses or []
    created = pod.metadata.creation_timestamp
    age_seconds = (datetime.now(UTC) - created).total_seconds() if created else 0.0

    return PodStatusResult(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace,
        phase=pod.status.phase or "Unknown",
        pod_ready=_pod_ready(pod),
        labels=dict(pod.metadata.labels or {}),
        init_containers=[_normalize_container(cs) for cs in init_statuses],
        containers=[_normalize_container(cs) for cs in statuses],
        age_seconds=age_seconds,
        node_name=pod.spec.node_name,
    )


def _normalize_container(cs: k8s.V1ContainerStatus) -> ContainerState:
    state = cs.state
    if state.running:
        kind, reason, message = "running", None, None
    elif state.waiting:
        kind, reason, message = "waiting", state.waiting.reason, state.waiting.message
    elif state.terminated:
        kind, reason, message = "terminated", state.terminated.reason, state.terminated.message
    else:
        kind, reason, message = "unknown", None, None

    return ContainerState(
        name=cs.name,
        state=kind,
        reason=reason,
        message=message,
        restart_count=cs.restart_count,
        ready=cs.ready,
    )


def _pod_ready(pod: k8s.V1Pod) -> bool:
    conditions = pod.status.conditions or []
    return any(c.type == "Ready" and c.status == "True" for c in conditions)
