"""Normalizing tool failures into facts the planner can reason about.

A failed tool call is often evidence rather than an accident: asking for a
Secret and getting 404 back is exactly how "the Secret the pod references
doesn't exist" gets confirmed. Raw `ApiException` strings carry the full
HTTP header dump, so they get normalized here the same way successful
results do (docs/architecture.md §4) — the model should see
`{"error": "not_found", "message": "secrets \\"db-credentials\\" not found"}`,
not four lines of `X-Kubernetes-Pf-Flowschema-Uid`.
"""

import json

from kubernetes.client.exceptions import ApiException

_REASON_BY_STATUS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
}


def describe_tool_error(exc: Exception) -> dict:
    """Reduce an exception to a compact, model-readable fact."""
    if isinstance(exc, ApiException):
        return {
            "error": _REASON_BY_STATUS.get(exc.status, "api_error"),
            "status": exc.status,
            "message": _api_message(exc),
        }
    return {"error": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}


def _api_message(exc: ApiException) -> str:
    """Pull the API's own `message` out of the JSON body, falling back to its reason."""
    try:
        return json.loads(exc.body).get("message") or exc.reason or ""
    except (TypeError, ValueError, AttributeError):
        return exc.reason or str(exc)
