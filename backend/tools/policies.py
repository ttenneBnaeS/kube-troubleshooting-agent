"""Read-only NetworkPolicy inspection.

Connectivity failures caused by a NetworkPolicy are invisible to every
other tool in the catalog: the pods are Running and Ready, the Service has
ready endpoints, and no Warning event is ever emitted — the packets are
just dropped. Without this tool the agent can only report "the client
can't reach the backend and I don't know why," so the NetworkPolicy eval
scenario would be unsolvable by construction.

Read-only like the rest of the catalog: `list_*`/`read_*` only.
"""

from kubernetes import client as k8s

from .client import get_core_v1_api, get_networking_v1_api
from .config import settings
from .models import NetworkPolicyPeer, NetworkPolicyResult, NetworkPolicyRule


def get_network_policies(
    namespace: str | None = None,
    policy_name: str | None = None,
) -> list[NetworkPolicyResult]:
    """Read-only: NetworkPolicies in a namespace, or one by name, with the pods each one selects."""
    ns = namespace or settings.namespace
    api = get_networking_v1_api()

    if policy_name:
        policies = [api.read_namespaced_network_policy(name=policy_name, namespace=ns)]
    else:
        policies = api.list_namespaced_network_policy(namespace=ns).items

    return [_normalize_policy(p, ns) for p in policies]


def _normalize_policy(policy: k8s.V1NetworkPolicy, namespace: str) -> NetworkPolicyResult:
    selector = _render_selector(policy.spec.pod_selector)
    rules = [
        *[_normalize_rule(r, "ingress") for r in (policy.spec.ingress or [])],
        *[_normalize_rule(r, "egress") for r in (policy.spec.egress or [])],
    ]
    return NetworkPolicyResult(
        name=policy.metadata.name,
        namespace=namespace,
        pod_selector=selector,
        selects_all_pods=selector == "",
        policy_types=list(policy.spec.policy_types or []),
        rules=rules,
        selected_pods=_pods_matching(namespace, selector),
    )


def _normalize_rule(rule, direction: str) -> NetworkPolicyRule:
    peers = rule.to if direction == "egress" else rule._from
    peers = peers or []
    return NetworkPolicyRule(
        direction=direction,
        # The API models "allow from anywhere" as an omitted peer list, so
        # an empty list here is permissive, not restrictive.
        allows_all_peers=len(peers) == 0,
        peers=[_normalize_peer(p) for p in peers],
        ports=[_render_port(p) for p in (rule.ports or [])],
    )


def _normalize_peer(peer) -> NetworkPolicyPeer:
    ip_block = None
    if peer.ip_block:
        excepts = f" except {','.join(peer.ip_block._except)}" if peer.ip_block._except else ""
        ip_block = f"{peer.ip_block.cidr}{excepts}"
    return NetworkPolicyPeer(
        pod_selector=_render_selector(peer.pod_selector) if peer.pod_selector is not None else None,
        namespace_selector=(
            _render_selector(peer.namespace_selector) if peer.namespace_selector is not None else None
        ),
        ip_block=ip_block,
    )


def _render_port(port) -> str:
    protocol = port.protocol or "TCP"
    if port.end_port:
        return f"{protocol}/{port.port}-{port.end_port}"
    return f"{protocol}/{port.port}" if port.port is not None else f"{protocol}/*"


def _render_selector(selector) -> str:
    """Render a LabelSelector as the standard selector string.

    Returns "" for the empty selector, which in Kubernetes means "match
    everything" — the form that makes a policy a namespace-wide default
    deny. Callers get `selects_all_pods` rather than having to know that.
    """
    if selector is None:
        return ""

    parts = [f"{k}={v}" for k, v in sorted((selector.match_labels or {}).items())]

    for expr in selector.match_expressions or []:
        values = ",".join(expr.values or [])
        if expr.operator == "In":
            parts.append(f"{expr.key} in ({values})")
        elif expr.operator == "NotIn":
            parts.append(f"{expr.key} notin ({values})")
        elif expr.operator == "Exists":
            parts.append(expr.key)
        elif expr.operator == "DoesNotExist":
            parts.append(f"!{expr.key}")

    return ",".join(parts)


def _pods_matching(namespace: str, selector: str) -> list[str]:
    pods = get_core_v1_api().list_namespaced_pod(namespace=namespace, label_selector=selector).items
    return [p.metadata.name for p in pods]
