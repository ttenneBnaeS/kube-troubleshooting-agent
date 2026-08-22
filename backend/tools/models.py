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
