Node functions for the graph in `backend/graph/build.py`, per
`docs/architecture.md` §7.

- `intake` (LLM, fast tier) — resolves `Scope` (namespace/resource) via
  structured output; sets `needs_clarification` instead of guessing when
  the request has no resource, namespace, or symptom to go on anywhere in
  the conversation.
- `gather_context` (deterministic) — fixed initial sweep (`get_pod_status`,
  `get_recent_events`) into `context_snapshot`.
- `plan` (LLM, reasoning tier) — bound to the full tool set
  (cluster tools + `search_k8s_docs_tool`); either calls one tool or
  returns plain text, which `route_after_plan` reads to decide
  `execute_tool` vs. `diagnose`. Loop-guarded by `LOOP_GUARD_MAX`.
- `execute_tool` (deterministic) — runs the tool `plan` chose, appends a
  normalized `ToolCallRecord` to `investigation_log`.
- `diagnose` (LLM, reasoning tier) — structured-output `Diagnosis`
  (root cause, confidence, citations); told to lower confidence when the
  loop guard cut the investigation short.
- `recommend` (LLM, reasoning tier) — plain-text suggested fix, framed as
  human-run only. This is the only node whose output streams
  token-by-token to the frontend (`backend/api/main.py` filters
  `stream_mode="messages"` chunks to `langgraph_node == "recommend"`).
