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
- The MCP server (§12) inherits all of this unchanged: it exposes the
  agent, which can only reach the cluster through these tools, and
  annotates its one tool `readOnlyHint`.

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

**As built (Week 6):** the fields split into *conversation* state
(`messages`, plus `turns: list[TurnRecord]` archiving each finished turn's
scope, evidence, diagnosis and recommendation), persisted by the
checkpointer through append-only reducers, and *run* state (everything
else), reset to defaults at the start of every turn by `new_turn_input()`.

## 7. Graph: nodes and edges

```
intake → gather_context → plan
plan ──(need more evidence)──▶ execute_tool ──▶ plan     # bounded loop
plan ──(enough evidence)─────▶ diagnose ──▶ ground ──▶ recommend ──▶ finalize ──▶ END
intake ──(needs clarification)──▶ finalize
intake ──(follow-up, earlier evidence exists)──▶ answer_followup ──▶ finalize
```

| Node | Type | Responsibility |
|---|---|---|
| `intake` | LLM, fast tier | Parse the complaint, resolve `scope`. Ask a clarifying question if ambiguous. |
| `gather_context` | deterministic | Fixed initial sweep: pod list/status, recent events, resource state → first `context_snapshot`. |
| `plan` | LLM, reasoning tier | Decide: call a specific tool for more evidence, or diagnose now. Emits a structured decision. |
| `execute_tool` | deterministic | Run the chosen read-only tool, append normalized result to `investigation_log`. |
| `diagnose` | LLM, reasoning tier | Synthesize root cause, confidence level, cite supporting evidence. |
| `ground` | deterministic | Retrieve doc pages for the diagnosed root cause (§8), for `recommend` to cite. |
| `recommend` | LLM, reasoning tier | Produce the suggested fix as text, explicitly framed as human-run, citing retrieved docs where they support it. |
| `answer_followup` | LLM, reasoning tier | Answer a follow-up from earlier turns' archived evidence, no tools; say so when that evidence doesn't contain the answer. |
| `finalize` | deterministic | Archive the turn: append the Human/AI messages and a `TurnRecord` to conversation state. |

### Loop guard

`step_count` is capped at 8 plan→execute iterations. On hitting the cap,
force a transition to `diagnose` with whatever evidence exists and a
lowered confidence score, rather than looping indefinitely.

### Checkpointing & memory

LangGraph's checkpointer persists state across turns, enabling follow-ups
("what about the other pod?") without re-running the full investigation,
and doubles as the foundation for human-in-the-loop approval gates if added
later.

As built: `AsyncSqliteSaver` in the API, one thread per client-generated
`thread_id`; the client sends only the new message. SQLite over the
in-memory saver so threads survive a server restart at the cost of one
file; Postgres would be overkill for a single-process demo. Follow-ups
are routed by `intake` (`Scope.is_followup`): a question answerable from
earlier turns' evidence goes to `answer_followup`; one about current
cluster state re-investigates. The route requires an earlier
investigation to exist, enforced in code.

## 8. RAG

- **Corpus:** official Kubernetes docs, `kubectl` reference, Helm docs.
  Chunked and embedded into Qdrant.
- **Retrieval is a tool**, callable by the `plan` node (e.g. "look up what
  readiness probe failures mean") — not always-on retrieval every turn.
- **Revised after eval (Week 5):** the planner made zero docs searches in
  every eval run. The live evidence settles the diagnosis, and the public
  K8s docs largely restate what the model already knows. So retrieval also
  runs once, deterministically, in a `ground` node after `diagnose`,
  keyed on the diagnosed root cause, and `recommend` cites what comes
  back. That puts retrieval where this section always said its value
  was: grounding the fix, not finding the cause.
- **Role:** grounds the `diagnose`/`recommend` output in canonical docs so
  recommendations cite real flags/fields instead of hallucinating them. RAG
  does not do the diagnosis; the reasoning-tier model does, using tool
  evidence. RAG is a supporting actor.

## 9. Eval

Diagnosis correctness is measured against injected failure scenarios with
golden labels (true root cause + expected remediation category), run
against a Kind cluster. Scored on: diagnosis correctness (primary),
tool-call efficiency (catches thrashing), and grounding (did the
recommendation cite the right remediation). LangSmith tracing is
env-gated so failed runs are debuggable per-node, not just pass/fail.

Implemented in `backend/eval/` (see its README for the mechanics). The
decisions worth recording here:

- **Isolation.** Each scenario is applied into a throwaway `eval-<id>`
  namespace and torn down afterwards, rather than run against the shared
  demo pods in `default`. Co-resident scenarios would mean the initial
  sweep returns eight broken pods every time, which conflates "can it
  diagnose" with "can it stay on scope."
- **Readiness gating.** The harness waits until a scenario's failure is
  genuinely observable (container reason, not-Ready pod, or an expected
  log line) before invoking the agent. Otherwise the run grades the
  harness's timing rather than the agent.
- **Two scorers, one verdict.** A deterministic signal check (synonym
  groups over the golden label, word-boundary matched) runs alongside an
  LLM judge. The judge owns correctness, because keyword matching can't
  distinguish a paraphrase from a miss; the signal check exists to name
  *which* expected signal was absent. `forbidden_terms` are advisory and
  never flip a verdict — an agent correctly writing "this is not a
  NetworkPolicy issue" would otherwise be scored as claiming the wrong
  cause. Scorer disagreements are reported, not smoothed away.
- **Trust domains.** The harness mutates the cluster (`kubectl
  apply`/`delete`); the agent under test cannot. The read-only boundary in
  §5 constrains the agent, not its test rig.
- **Multi-turn follow-ups (Week 6).** Memory adds a decision the agent can
  get wrong: answer from earlier evidence or investigate again. Follow-up
  turns run on the scenario's thread and are scored deterministically on
  route and on required reply signals, so a correct route that answers
  from evidence lacking the answer still fails. The first baseline found a
  tool gap that way (pod `describe` returned no env references), not a
  memory bug.

### Failed tool calls are evidence

The first eval run surfaced a real defect: the agent called
`describe_resource(kind="secret", ...)` to confirm a referenced Secret was
missing, the API returned 404 — which *is* the answer — and the raw
exception propagated out of `execute_tool` and killed the entire graph
run. Tool failures are now normalized (`tools/errors.py`) and appended to
the investigation log so the planner can reason about them, and the
deterministic initial sweep is guarded the same way. A tool that fails is
frequently the fact the diagnosis turns on, not an accident.

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
  observability/  # structured logging, LangSmith tracing setup
  mcp_server/   # the `investigate` MCP tool (§12)
  eval/         # scenarios, golden labels, harness, scorers
  tests/        # pytest, deterministic layer only
frontend/
infra/
  docker/       # Dockerfiles, compose stack, Kind kubeconfig script
  kubernetes/   # scenario manifests live here too
docs/
  architecture.md
```

`eval/` sits under `backend/` rather than at the repo root as originally
sketched: backend modules are top-level (`graph`, `tools`, `models` — no
`backend` package prefix), so a root-level `eval/` could not import the
graph without `sys.path` manipulation. Under `backend/` it runs in the
same uv environment as the code it tests. The scenario *manifests* stay in
`infra/kubernetes/` as planned. `tests/` moved under `backend/` for the
same reason.

## 11. Status

As of Week 7: the FastAPI streaming skeleton, the Next.js chat UI, the
read-only tool catalog (§4), the RAG pipeline (§8), the LangGraph state
machine (§6-7), and the eval harness (§9) all exist and are wired
together. The failure-scenario catalog is at thirteen scenarios, each with a golden
label and an easy/medium/hard tier: CrashLoopBackOff, ImagePullBackOff,
OOMKilled, readiness probe, missing Secret, ConfigMap key mismatch,
DNS/service-name mismatch, NetworkPolicy, Service-selector mismatch, a
selector bug behind a loud unrelated distractor, init-container failure, a
cross-namespace NetworkPolicy, and an application-level failure hidden in
a noisy namespace.

The tool catalog grew two entries in Week 5, both driven by scenarios that
were otherwise unsolvable by construction rather than merely hard:
`get_network_policies` (a policy drop produces no unhealthy object state
anywhere) and configmap/secret support in `describe_resource` (a
wrong-key diagnosis depends on which keys the object really has).

Week 6: checkpointed conversational memory and follow-up routing (§7),
multi-turn eval cases (§9), and the investigation-trail UI: the chat
endpoint streams one structured step per sweep, tool call, diagnosis and
docs retrieval alongside the prose, and a reloaded thread rebuilds the
same trail from its stored turn records.

Week 7: a pytest suite over the deterministic layer, including a static
check that `tools/` only calls read verbs on the Kubernetes client (§5's
boundary, enforced by test as well as by construction); structured JSON
logging with per-turn request/thread context; LangSmith tracing for the
API, separate from eval's project; and Docker images plus a Compose
stack.

Week 8: the MCP server (§12), the README, and the demo walkthrough. The
RBAC-scoped read-only credentials called for in §5 remain open —
enforcement today is code-layer only, backed by a test.

## 12. MCP server

The plan called for an MCP server "exposing the read-only K8s
operations." What got built exposes the *agent* instead: one tool,
`investigate(request, namespace?)`, which runs the full graph and returns
a structured result: the diagnosis with confidence and evidence, the
fix, and the same trail steps the UI renders.

Why the change: a server mirroring the seven read-only tools would have
no consumer in this project (the agent calls its tools directly) and
would duplicate what existing Kubernetes MCP servers already offer. What
this project adds is the investigation itself, a bounded plan/execute
loop with a measured diagnosis rate, so that's what's exposed. It lets
the agent run from the assistant an engineer already uses.

Decisions:

- **Stateless per call.** Each call is a fresh checkpoint thread. The
  client assistant keeps the conversation, and the returned trail carries
  the evidence, so the client can answer follow-ups itself. Server-side
  memory would duplicate the client's.
- **Progress, not silence.** A run takes 20-60 seconds; the tool reports
  progress per graph node.
- **Clarification as data.** If `intake` can't resolve a scope, the
  result is `status: needs_clarification` with the question, for the
  client to put to the user.
- **stdio only.** It's a local tool against a local cluster; logs go to
  stderr because stdout is the protocol stream.
