"""Builders for real `kubernetes.client` model objects, and a fake API.

Tests cover the deterministic layer only — no cluster, no model calls.
Kubernetes objects are built from the client's own model classes rather
than hand-rolled stand-ins, so normalization is tested against the
attribute shapes the real API returns.
"""

from datetime import UTC, datetime, timedelta

from kubernetes import client as k8s


def container_status(
    name="main",
    *,
    running=False,
    waiting_reason=None,
    terminated_reason=None,
    message=None,
    restarts=0,
    ready=False,
    exit_code=1,
):
    state = k8s.V1ContainerState()
    if running:
        state.running = k8s.V1ContainerStateRunning(started_at=datetime.now(UTC))
    elif waiting_reason:
        state.waiting = k8s.V1ContainerStateWaiting(reason=waiting_reason, message=message)
    elif terminated_reason:
        state.terminated = k8s.V1ContainerStateTerminated(
            reason=terminated_reason, exit_code=exit_code, message=message
        )
    return k8s.V1ContainerStatus(
        name=name,
        image="busybox",
        image_id="",
        ready=ready,
        restart_count=restarts,
        state=state,
    )


def pod(
    name="web",
    namespace="default",
    *,
    phase="Running",
    ready=True,
    labels=None,
    containers=None,
    init_containers=None,
    spec_containers=None,
    spec_init_containers=None,
    volumes=None,
    age=timedelta(minutes=5),
):
    return k8s.V1Pod(
        metadata=k8s.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels,
            creation_timestamp=datetime.now(UTC) - age,
        ),
        spec=k8s.V1PodSpec(
            containers=spec_containers or [k8s.V1Container(name="main", image="busybox")],
            init_containers=spec_init_containers,
            volumes=volumes,
            node_name="kind-control-plane",
        ),
        status=k8s.V1PodStatus(
            phase=phase,
            conditions=[k8s.V1PodCondition(type="Ready", status="True" if ready else "False")],
            container_statuses=containers if containers is not None else [container_status(running=True, ready=ready)],
            init_container_statuses=init_containers,
        ),
    )


def event(name, *, kind="Pod", type_="Warning", reason="Failed", message="", count=1, last_seen=None):
    return k8s.CoreV1Event(
        metadata=k8s.V1ObjectMeta(name=f"{name}.evt"),
        involved_object=k8s.V1ObjectReference(kind=kind, name=name),
        type=type_,
        reason=reason,
        message=message,
        count=count,
        last_timestamp=last_seen,
    )


class FakeCoreV1:
    """The subset of CoreV1Api the tools call, serving canned objects.

    Only read/list methods exist, mirroring the real tools' read-only
    contract: a tool reaching for anything else fails with AttributeError.
    """

    def __init__(self):
        self.pods: list[k8s.V1Pod] = []
        self.events: list[k8s.CoreV1Event] = []
        self.services: dict[str, k8s.V1Service] = {}
        self.endpoints: dict[str, k8s.V1Endpoints] = {}
        self.configmaps: dict[str, k8s.V1ConfigMap] = {}
        self.secrets: dict[str, k8s.V1Secret] = {}
        self.logs: dict[str, str] = {}
        self.label_selector_calls: list[str] = []
        self.pods_by_selector: dict[str, list[k8s.V1Pod]] = {}

    def list_namespaced_pod(self, namespace, label_selector=None):
        if label_selector is not None:
            self.label_selector_calls.append(label_selector)
            return k8s.V1PodList(items=self.pods_by_selector.get(label_selector, []))
        return k8s.V1PodList(items=[p for p in self.pods if p.metadata.namespace == namespace])

    def read_namespaced_pod(self, name, namespace):
        for p in self.pods:
            if p.metadata.name == name and p.metadata.namespace == namespace:
                return p
        raise _not_found("pods", name)

    def list_namespaced_event(self, namespace):
        return k8s.CoreV1EventList(items=self.events)

    def read_namespaced_service(self, name, namespace):
        return self.services[name]

    def read_namespaced_endpoints(self, name, namespace):
        return self.endpoints[name]

    def read_namespaced_config_map(self, name, namespace):
        if name not in self.configmaps:
            raise _not_found("configmaps", name)
        return self.configmaps[name]

    def read_namespaced_secret(self, name, namespace):
        if name not in self.secrets:
            raise _not_found("secrets", name)
        return self.secrets[name]

    def read_namespaced_pod_log(self, name, namespace, container, tail_lines, previous, _preload_content):
        class _Response:
            data = self.logs[name].encode()

        return _Response()


def _not_found(resource, name):
    exc = k8s.ApiException(status=404, reason="Not Found")
    exc.body = f'{{"kind":"Status","message":"{resource} \\"{name}\\" not found","reason":"NotFound","code":404}}'
    return exc
