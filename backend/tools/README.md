Read-only Kubernetes tools, per `docs/architecture.md` §4-5.

- `pods.py`, `events.py`, `logs.py`, `nodes.py`, `services.py`,
  `describe.py`, `policies.py` — plain functions against the official
  `kubernetes` Python client, returning normalized Pydantic models from
  `models.py`. No LangChain dependency, so the LangGraph nodes and the
  eval harness (`backend/eval/`) call them directly — the harness reuses
  `get_pod_status`/`get_container_logs` to decide when a scenario has
  finished breaking.
- `get_pod_status` reports `init_containers` separately from `containers`,
  and returns each pod's `labels`. Both fill real blind spots: code that
  read only `container_statuses` left an init failure undiagnosable (the
  app container just says `PodInitializing`, which names neither the
  failing init container nor its reason), and without pod labels there was
  no way to say what a Service selector matching nothing *should* have
  been. `describe.py`'s pod summary carries the same two fields, and
  `eval/cluster.py`'s readiness predicate scans both container lists.
- `describe.py` covers pod, deployment, service, node, configmap, and
  secret. For configmap/secret it returns **key names and value sizes,
  never values** — a missing-key diagnosis needs the key list and nothing
  more, and a read-only boundary that still hands secret material to an
  LLM isn't a boundary.
- `policies.py` (`get_network_policies`) exists because a NetworkPolicy
  drop is invisible to every other tool: pods stay Running and Ready, the
  Service keeps ready endpoints, and no Warning event is emitted. It
  resolves each policy's label selector server-side into the pods it
  actually selects, so the model isn't left evaluating selectors by eye.
- `errors.py` — normalizes a failed API call into a compact fact
  (`{"error": "not_found", "message": "secrets \"db-credentials\" not
  found"}`) instead of a raw `ApiException` header dump. A failed tool
  call is frequently *evidence*, so `agent/nodes.py` records it into the
  investigation log rather than letting it abort the run.
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
