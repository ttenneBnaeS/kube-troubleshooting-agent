Read-only Kubernetes tools, per `docs/architecture.md` §4-5.

- `pods.py`, `events.py`, `logs.py`, `nodes.py`, `services.py`, `describe.py`
  — plain functions against the official `kubernetes` Python client,
  returning normalized Pydantic models from `models.py`. No LangChain
  dependency, so the Week 4 LangGraph nodes and Week 5 eval harness can
  call them directly.
- `langchain_tools.py` — `@tool`-wrapped adapters (JSON-string return) for
  Anthropic tool calling, exported as `TOOLS`.
- `client.py` / `config.py` — cluster access. Tries in-cluster config
  first, falls back to kubeconfig (`KUBE_CONTEXT`, `KUBE_KUBECONFIG_PATH`
  env vars); `KUBE_NAMESPACE` sets the default namespace.

Every function only ever calls `list_*`/`read_*`/`get_*` API methods —
read-only is enforced by which client methods this code calls, not by
prompting.

Known gap: cluster credentials here are whatever the ambient
kubeconfig/service-account grants, not yet scoped to a read-only
ClusterRole. `docs/architecture.md` §5 calls for RBAC-scoped credentials
as defense in depth; that's still open.
