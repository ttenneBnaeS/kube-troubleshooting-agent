from kubernetes import client as k8s

from .client import get_core_v1_api
from .models import NodeCondition, NodeStatusResult


def get_node_status(node_name: str | None = None) -> list[NodeStatusResult]:
    """Read-only: node conditions (pressure flags, readiness) and capacity vs allocatable resources.

    Live usage (`kubectl top`) needs metrics-server, which Kind doesn't ship
    by default, so this reports capacity/allocatable/conditions only.
    """
    api = get_core_v1_api()
    nodes = [api.read_node(name=node_name)] if node_name else api.list_node().items
    return [_normalize_node(n) for n in nodes]


def _normalize_node(node: k8s.V1Node) -> NodeStatusResult:
    conditions = node.status.conditions or []
    ready = any(c.type == "Ready" and c.status == "True" for c in conditions)

    return NodeStatusResult(
        name=node.metadata.name,
        ready=ready,
        unschedulable=bool(node.spec.unschedulable),
        conditions=[
            NodeCondition(type=c.type, status=c.status, reason=c.reason, message=c.message)
            for c in conditions
        ],
        capacity=dict(node.status.capacity or {}),
        allocatable=dict(node.status.allocatable or {}),
    )
