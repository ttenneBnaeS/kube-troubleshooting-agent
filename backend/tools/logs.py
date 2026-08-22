from .client import get_core_v1_api
from .config import settings
from .models import LogResult


def get_container_logs(
    pod_name: str,
    namespace: str | None = None,
    container_name: str | None = None,
    tail_lines: int = 100,
    previous: bool = False,
) -> LogResult:
    """Read-only: tail of a container's logs. `previous=True` reads the last crashed instance (CrashLoopBackOff investigation)."""
    ns = namespace or settings.namespace
    api = get_core_v1_api()

    # _preload_content=False + manual decode: the client's default string
    # deserialization for this endpoint sometimes runs response bytes
    # through str() instead of .decode(), yielding a literal "b'...'"
    # string instead of the actual log text.
    response = api.read_namespaced_pod_log(
        name=pod_name,
        namespace=ns,
        container=container_name,
        tail_lines=tail_lines,
        previous=previous,
        _preload_content=False,
    )
    raw = response.data.decode("utf-8")

    return LogResult(
        pod_name=pod_name,
        namespace=ns,
        container_name=container_name or "",
        previous=previous,
        lines=raw.splitlines(),
    )
