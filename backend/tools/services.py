from kubernetes import client as k8s

from .client import get_core_v1_api
from .config import settings
from .models import ServiceEndpointsResult, ServicePort


def get_service_endpoints(service_name: str, namespace: str | None = None) -> ServiceEndpointsResult:
    """Read-only: a Service's selector/ports plus its Endpoints readiness (which pod IPs are ready vs not)."""
    ns = namespace or settings.namespace
    api = get_core_v1_api()

    svc = api.read_namespaced_service(name=service_name, namespace=ns)
    endpoints = api.read_namespaced_endpoints(name=service_name, namespace=ns)

    ready_addresses: list[str] = []
    not_ready_addresses: list[str] = []
    for subset in endpoints.subsets or []:
        ready_addresses.extend(addr.ip for addr in (subset.addresses or []))
        not_ready_addresses.extend(addr.ip for addr in (subset.not_ready_addresses or []))

    return ServiceEndpointsResult(
        service_name=service_name,
        namespace=ns,
        type=svc.spec.type or "ClusterIP",
        cluster_ip=svc.spec.cluster_ip,
        selector=dict(svc.spec.selector or {}),
        ports=[
            ServicePort(port=p.port, target_port=str(p.target_port), protocol=p.protocol or "TCP")
            for p in (svc.spec.ports or [])
        ],
        ready_addresses=ready_addresses,
        not_ready_addresses=not_ready_addresses,
    )
