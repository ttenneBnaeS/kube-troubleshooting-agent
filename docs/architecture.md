# Architecture

## 1. Purpose

An agent that diagnoses Kubernetes failures (CrashLoopBackOff, OOMKilled,
ImagePullBackOff, etc.) by gathering evidence with read-only tools and
reasoning about root cause, with a suggested fix handed back to a human for
review — never applied automatically.

This document is the source of truth for the architectural decisions made
before implementation, so they don't get re-litigated mid-build. See the
project plan for the full rationale; this doc states the decisions.

## 2. LangChain vs LangGraph boundary

- **LangGraph** owns orchestration: the state machine, the plan/execute
  loop, conditional routing, checkpointing, conversational memory. All
  control flow lives in LangGraph nodes and edges.
- **LangChain** is scoped narrowly to RAG plumbing (document loaders, text
  splitters, the retriever interface over Qdrant) and the model wrapper.

The agent loop is **not** wrapped in a LangChain chain. Nodes call the model
and tools directly. This keeps the control flow legible in traces and gives
a clean answer to "why both libraries."

## 3. Model tier routing

Two tiers, no more:

| Tier | Model class | Used by |
|---|---|---|
| Reasoning | Claude Sonnet-class | `plan`, `diagnose`, `recommend` |
| Fast/cheap | Claude Haiku-class | `intake` (scope/intent classification) |

Model IDs and the tier→model mapping live in `backend/models/config.py`,
not hardcoded in node functions, so the mapping can change without touching
graph logic.

## 4. Tool design philosophy

Fact-gathering is deterministic; judgment is LLM-driven. Concretely:

- Tools return **normalized, structured output** — parsed pod status,
  decoded failure reasons, extracted container states — never raw `kubectl`
  text for the model to re-parse.
- Parsing/normalization happens in Python, deterministically, before the
  model sees anything.
- Tools are **purpose-built** (`get_pod_status`, `get_recent_events`,
  `get_container_logs`, ...) rather than one general `run_kubectl`
  passthrough. This controls output shape, enforces read-only at the
  function signature level, and keeps the transcript demoable.

## 5. Safety boundary: read-only by construction

- Every tool wraps a read-only verb: `get`, `describe`, `logs`, `events`,
  `top`. No tool exposes `apply`, `delete`, `edit`, `scale`, or `rollout`.
- The service account / kubeconfig context the agent runs under should also
  be scoped to read-only RBAC (`get`, `list`, `watch`) as defense in depth —
  the enforcement isn't only "the Python function happens not to call
  mutating verbs," it's "the credentials can't mutate either."
- Fixes are **suggested as text only** — the `recommend` node's output is a
  string describing the `kubectl`/manifest change for a human to run. There
  is no tool that applies a fix.
- This is enforced at the tool layer, not the prompt layer. The model is
  never given a mutating tool to be told not to use.

## 6. State object

A single typed state (Pydantic model) threaded through the graph:

```python
class AgentState(BaseModel):
    user_request: str
    scope: Scope | None = None              # namespace, resource_type, resource_name
    context_snapshot: dict = {}              # structured facts gathered so far
    investigation_log: list[ToolCall] = []   # tools called + normalized results
    hypothesis: str | None = None
    diagnosis: Diagnosis | None = None       # root cause + confidence + citations
    recommendation: str | None = None        # suggested fix, text only
    messages: list[BaseMessage] = []         # conversational history
    step_count: int = 0                      # loop guard
```

## 7. Graph: nodes and edges

```
intake → gather_context → plan
plan ──(need more evidence)──▶ execute_tool ──▶ plan     # bounded loop
plan ──(enough evidence)─────▶ diagnose ──▶ recommend ──▶ END
```

| Node | Type | Responsibility |
|---|---|---|
| `intake` | LLM, fast tier | Parse the complaint, resolve `scope`. Ask a clarifying question if ambiguous. |
| `gather_context` | deterministic | Fixed initial sweep: pod list/status, recent events, resource state → first `context_snapshot`. |
| `plan` | LLM, reasoning tier | Decide: call a specific tool for more evidence, or diagnose now. Emits a structured decision. |
| `execute_tool` | deterministic | Run the chosen read-only tool, append normalized result to `investigation_log`. |
| `diagnose` | LLM, reasoning tier | Synthesize root cause, confidence level, cite supporting evidence. |
| `recommend` | LLM, reasoning tier | Produce the suggested fix as text, explicitly framed as human-run. |

### Loop guard

`step_count` is capped at 8 plan→execute iterations. On hitting the cap,
force a transition to `diagnose` with whatever evidence exists and a
lowered confidence score, rather than looping indefinitely.

### Checkpointing & memory

LangGraph's checkpointer persists state across turns, enabling follow-ups
("what about the other pod?") without re-running the full investigation,
and doubles as the foundation for human-in-the-loop approval gates if added
later.

## 8. RAG

- **Corpus:** official Kubernetes docs, `kubectl` reference, Helm docs.
  Chunked and embedded into Qdrant.
- **Retrieval is a tool**, callable by the `plan` node (e.g. "look up what
  readiness probe failures mean") — not always-on retrieval every turn.
- **Role:** grounds the `diagnose`/`recommend` output in canonical docs so
  recommendations cite real flags/fields instead of hallucinating them. RAG
  does not do the diagnosis; the reasoning-tier model does, using tool
  evidence. RAG is a supporting actor.

## 9. Eval

Diagnosis correctness is measured against injected failure scenarios with
golden labels (true root cause + expected remediation category), run
against a Kind cluster. Scored on: diagnosis correctness (primary),
tool-call efficiency (catches thrashing), and grounding (did the
recommendation cite the right remediation). LangSmith tracing is wired in
from the start so failed runs are debuggable per-node, not just pass/fail.

## 10. Repository layout

```text
backend/
  api/          # FastAPI, streaming endpoint
  agent/        # graph assembly, node functions
  graph/        # state definition, edges, loop control
  tools/        # read-only kubectl tools + normalization
  rag/          # corpus loading, embedding, retriever
  prompts/      # versioned prompt templates
  models/       # model config, tier routing
eval/           # scenarios, golden labels, harness, scorers
frontend/
infra/
  docker/
  kubernetes/   # scenario manifests live here too
tests/
docs/
  architecture.md
demo/
```

## 11. Status

As of Week 4: the FastAPI streaming skeleton, the Next.js chat UI, the
read-only tool catalog (§4), the RAG pipeline (§8), and the LangGraph
state machine (§6-7) all exist and are wired together — `backend/agent/`
and `backend/graph/` are no longer placeholders. Not yet built: the eval
harness (§9, Week 5), checkpointed conversational memory (§7's
"Checkpointing & memory" — Week 6; today's multi-turn context is the
client resending full history into `AgentState.messages`), and the MCP
server (Week 8).
