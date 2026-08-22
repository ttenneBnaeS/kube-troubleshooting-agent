"""LangChain tool-calling adapters over the plain-function tool catalog.

Kept separate from the tool implementations themselves: the functions in
pods.py/events.py/etc. return typed Pydantic results and have no LangChain
dependency, so the eval harness (Week 5) and the future LangGraph nodes
(Week 4) can call them directly. This module only exists to translate
call/return shapes for Anthropic tool calling in the chat endpoint.
"""

import json

from langchain_core.tools import tool

from .describe import DescribableKind, describe_resource
from .events import get_recent_events
from .logs import get_container_logs
from .nodes import get_node_status
from .pods import get_pod_status
from .services import get_service_endpoints


@tool
def get_pod_status_tool(namespace: str | None = None, pod_name: str | None = None) -> str:
    """Get status of pods in a namespace, or one pod by name: phase, container states (running/waiting/terminated + reason like CrashLoopBackOff/OOMKilled/ImagePullBackOff), restart counts, readiness, age."""
    results = get_pod_status(namespace=namespace, pod_name=pod_name)
    return json.dumps([r.model_dump() for r in results])


@tool
def describe_resource_tool(kind: DescribableKind, name: str, namespace: str | None = None) -> str:
    """Describe a Kubernetes resource: structured status/conditions plus its recent related events. `kind` must be one of: pod, deployment, service, node."""
    result = describe_resource(kind=kind, name=name, namespace=namespace)
    return json.dumps(result.model_dump())


@tool
def get_container_logs_tool(
    pod_name: str,
    namespace: str | None = None,
    container_name: str | None = None,
    tail_lines: int = 100,
    previous: bool = False,
) -> str:
    """Get recent logs for a container in a pod. Set previous=true to read logs from the container's last crashed instance (needed for CrashLoopBackOff root-causing)."""
    result = get_container_logs(
        pod_name=pod_name,
        namespace=namespace,
        container_name=container_name,
        tail_lines=tail_lines,
        previous=previous,
    )
    return json.dumps(result.model_dump())


@tool
def get_recent_events_tool(
    namespace: str | None = None,
    involved_object_name: str | None = None,
    limit: int = 20,
) -> str:
    """Get recent Kubernetes events for a namespace, optionally filtered to one object's name, sorted newest first and deduplicated."""
    results = get_recent_events(namespace=namespace, involved_object_name=involved_object_name, limit=limit)
    return json.dumps([r.model_dump() for r in results])


@tool
def get_node_status_tool(node_name: str | None = None) -> str:
    """Get status of cluster nodes, or one node by name: readiness, conditions (memory/disk/PID pressure), capacity vs allocatable resources."""
    results = get_node_status(node_name=node_name)
    return json.dumps([r.model_dump() for r in results])


@tool
def get_service_endpoints_tool(service_name: str, namespace: str | None = None) -> str:
    """Get a Service's selector/ports and its Endpoints readiness: which backing pod IPs are currently ready to receive traffic vs not ready."""
    result = get_service_endpoints(service_name=service_name, namespace=namespace)
    return json.dumps(result.model_dump())


TOOLS = [
    get_pod_status_tool,
    describe_resource_tool,
    get_container_logs_tool,
    get_recent_events_tool,
    get_node_status_tool,
    get_service_endpoints_tool,
]
