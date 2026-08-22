from kubernetes import client as k8s

from .client import get_core_v1_api
from .config import settings
from .models import EventRecord


def get_recent_events(
    namespace: str | None = None,
    involved_object_name: str | None = None,
    limit: int = 20,
) -> list[EventRecord]:
    """Read-only: recent events for a namespace, optionally filtered to one object, sorted newest first."""
    ns = namespace or settings.namespace
    api = get_core_v1_api()
    events = api.list_namespaced_event(namespace=ns).items

    if involved_object_name:
        events = [e for e in events if e.involved_object.name == involved_object_name]

    records = [_normalize_event(e) for e in events]
    records.sort(key=lambda r: r.last_seen or "", reverse=True)
    return records[:limit]


def _normalize_event(event: k8s.CoreV1Event) -> EventRecord:
    last_seen = event.last_timestamp or event.event_time or event.first_timestamp
    return EventRecord(
        type=event.type or "Normal",
        reason=event.reason or "",
        message=event.message or "",
        involved_object=f"{event.involved_object.kind}/{event.involved_object.name}",
        count=event.count or 1,
        last_seen=last_seen.isoformat() if last_seen else None,
    )
