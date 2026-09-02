"""Normalized, structured tool outputs.

Every tool returns one of these instead of a raw kubectl-shaped API object
or text dump — see docs/architecture.md §4. Fields like `reason` on
ContainerState (CrashLoopBackOff, OOMKilled, ImagePullBackOff, ...) come
straight off the Kubernetes API's own container status, just flattened
out of the nested waiting/running/terminated union the API returns.
"""

from pydantic import BaseModel


class ContainerState(BaseModel):
    name: str
    state: str  # "running" | "waiting" | "terminated" | "unknown"
    reason: str | None = None
    message: str | None = None
    restart_count: int
    ready: bool


class PodStatusResult(BaseModel):
    name: str
    namespace: str
    phase: str
    pod_ready: bool
    # Needed to diagnose a Service whose selector matches nothing: the
    # Service reports its selector and an empty endpoint list, but without
    # the pods' own labels there's no way to say what the selector should
    # have been.
    labels: dict[str, str] = {}
    # Kept separate from `containers` rather than merged, because the
    # distinction is the diagnosis: init containers run to completion
    # before app containers start, so a failing init container is the
    # cause of the app container's state rather than one more failure
    # alongside it. A pod blocked in init reports phase=Pending with its
    # app containers waiting on `PodInitializing` — which says the pod is
    # stuck behind init, but names neither the failing init container nor
    # the reason. That lives here.
    init_containers: list[ContainerState] = []
    containers: list[ContainerState]
    age_seconds: float
    node_name: str | None = None


class EventRecord(BaseModel):
    type: str  # "Normal" | "Warning"
    reason: str
    message: str
    involved_object: str  # "<Kind>/<name>"
    count: int
    last_seen: str | None = None


class LogResult(BaseModel):
    pod_name: str
    namespace: str
    container_name: str
    previous: bool
    lines: list[str]


class NodeCondition(BaseModel):
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


class NodeStatusResult(BaseModel):
    name: str
    ready: bool
    unschedulable: bool
    conditions: list[NodeCondition]
    capacity: dict[str, str]
    allocatable: dict[str, str]


class ServicePort(BaseModel):
    port: int
    target_port: str
    protocol: str


class ServiceEndpointsResult(BaseModel):
    service_name: str
    namespace: str
    type: str
    cluster_ip: str | None = None
    selector: dict[str, str]
    ports: list[ServicePort]
    ready_addresses: list[str]
    not_ready_addresses: list[str]


class DescribeResult(BaseModel):
    kind: str
    name: str
    namespace: str | None = None
    summary: dict
    recent_events: list[EventRecord]


class NetworkPolicyPeer(BaseModel):
    """One `from`/`to` entry in a policy rule, flattened out of the API's
    union of pod/namespace selectors and IP blocks."""

    pod_selector: str | None = None
    namespace_selector: str | None = None
    ip_block: str | None = None


class NetworkPolicyRule(BaseModel):
    direction: str  # "ingress" | "egress"
    # An empty `peers` list is the API's "allow from anywhere" form, which
    # reads identically to "no peers allowed" if you only look at the
    # length — hence the explicit flag rather than making callers know that.
    allows_all_peers: bool
    peers: list[NetworkPolicyPeer] = []
    ports: list[str] = []


class NetworkPolicyResult(BaseModel):
    name: str
    namespace: str
    # Rendered selector string; "" means the policy selects every pod in
    # the namespace (the default-deny form).
    pod_selector: str
    selects_all_pods: bool
    policy_types: list[str]
    rules: list[NetworkPolicyRule] = []
    # Resolved server-side: which pods the selector actually matches right
    # now. Evaluating a label selector by eye is exactly the kind of work
    # that belongs in the deterministic layer (docs/architecture.md §4).
    selected_pods: list[str] = []
