# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A LangGraph agent that diagnoses Kubernetes failures using read-only tool
calling and RAG over official docs, suggesting fixes as text for a human
to run rather than applying them. It's a portfolio project targeting AI
Engineer roles — the point is demoable fluency with the current agentic
stack, not the Kubernetes domain knowledge itself. Full design lives in
`docs/architecture.md`; the phased build-out is in
`Kubernetes_Troubleshooting_Agent_Plan_v2.md` (8-week roadmap; local only,
gitignored and removed from history, so it may not exist in a fresh clone).

**Read `docs/architecture.md` before making any structural change** — it
records decisions (LangChain/LangGraph split, model tier routing, tool
design philosophy, the read-only safety boundary) that were deliberately
locked in up front so they wouldn't get re-litigated mid-build.

## Current state vs. planned state

The repo is scaffolded for the full 8-week plan; Weeks 1-7 are
implemented. `backend/tools/` has a real read-only Kubernetes tool
catalog (pod status, describe, logs, events, node status, service
endpoints, network policies) against the official `kubernetes` Python
client. `backend/rag/` has a real RAG pipeline (curated K8s/kubectl doc
corpus → Voyage AI embeddings → Qdrant) exposed as another tool.
`backend/graph/` and `backend/agent/` hold a real LangGraph state machine
(`intake` → `gather_context` → `plan`/`execute_tool` loop → `diagnose` →
`ground` → `recommend` → `finalize`) that `backend/api/main.py`'s chat endpoint drives directly —
the Week 1-3 bounded probe/execute round-trip in `main.py` is gone.
`backend/eval/` holds the Week 5 eval harness, and
`backend/eval/scenarios.py` has twenty golden-labelled failure scenarios
(tiered easy/medium/hard) plus one healthy control; the last seven were
built specifically to make the agent fail (see `backend/eval/README.md`,
"Scenarios built to fail").

Week 6 is done: conversational memory is a LangGraph checkpointer
(SQLite, keyed by a client-generated `thread_id`, rehydrated on page
reload); follow-ups route to `answer_followup`, which answers from prior
turns' evidence without re-investigating; eval has multi-turn follow-up
cases; and the UI shows each answer's investigation trail.
Week 7 is done: pytest + ruff (backend), `node:test` (frontend),
structured logging, API tracing, and Docker/Compose (`infra/docker/`).
Basic auth was skipped deliberately — the plan lists public deployment
under "cut first" and a local demo is the target. Week 8 is done: the MCP server (`backend/mcp_server/`), the README
rewrite (its Results table is from eval run F, 2026-10-06), and `demo/`
(local only, gitignored):
a walkthrough script plus `record.mjs`, which drives the real UI with
Playwright, records video to `demo/out/` (gitignored), and refreshes
`docs/images/trail.png`. The walkthrough describes what recordings
actually showed, including the variance in scenario 3 (whether turn 1
describes the pod or the Secret). Keep it that way: if a re-recording
contradicts it, fix the text, don't rig the scenario.

Note the layout deviation: the plan and `docs/architecture.md` §10 sketch
`eval/` at the repo root, but it lives at `backend/eval/` because backend
modules are top-level (`graph`, `tools`, `models` — there is no `backend`
package prefix), so a root-level `eval/` can't import the graph without
`sys.path` surgery. Scenario manifests stay in `infra/kubernetes/`.

## Commands

### Backend (`backend/`, uv-managed Python 3.13)

```bash
cd backend
cp .env.example .env        # fill in ANTHROPIC_API_KEY
uv run uvicorn api.main:app --reload --port 8000
```

- Add a dependency: `uv add <package>` (run from `backend/`)
- Tests: `uv run pytest` (~1s). They cover the deterministic layer only —
  no cluster, no model calls, no network: tool normalization against real
  `kubernetes.client` model objects served by `tests/builders.py`'s
  `FakeCoreV1`, the read-only boundary (`test_read_only.py` statically
  scans `tools/` for any non-`list_`/`read_`/`get_` client call — editing
  that test is the only way a mutating call lands), state reset and
  checkpoint serialization, routing, the trail, eval scoring, and the API
  over the real compiled graph with model/cluster nodes stubbed
  (`stub_nodes` in `conftest.py`). Agent *quality* is the eval harness's
  job, not pytest's.
- Lint: `uv run ruff check .` (`--fix` for import order). `E501` is off
  on purpose: tool docstrings are the model-facing descriptions, kept on
  one line so wrapping can't change them. No formatter is enforced.
- Tests live in `backend/tests/`, not the root `tests/` the plan sketches,
  for the same reason as `eval/` (below).

### Eval harness (`backend/eval/`)

Needs a reachable Kind cluster with the demo scenarios' images pullable,
and `kubectl` on PATH. A full run is ~5-8 minutes, mostly spent waiting
for scenarios to break rather than on agent latency.

```bash
cd backend
uv run python -m eval                   # all scenarios
uv run python -m eval -s dns -s secret  # a subset
uv run python -m eval --list            # what's available
uv run python -m eval --no-judge        # deterministic scoring only (no LLM judge calls)
uv run python -m eval --keep            # leave eval-* namespaces up to inspect
uv run python -m eval --no-followups    # first turn only, skip multi-turn follow-ups
```

Runs are sequential deliberately — scenarios contend for node memory (the
OOM case especially) and the RAG tool sits behind Voyage's free-tier rate
limits, so parallelism produces flake that reads as agent error. Don't
"optimize" that into a `gather()`.

Each run writes a full JSON record to `backend/eval/results/`
(gitignored). LangSmith tracing is optional, env-gated via `LANGSMITH_*`
in `backend/.env`; `observability/tracing.py` has to load `.env` into
`os.environ` itself, because backend config goes through
pydantic-settings (which doesn't export there) while LangChain reads the
environment directly. The API traces to `LANGSMITH_PROJECT` and eval to
`LANGSMITH_EVAL_PROJECT` (set, not defaulted, so they never mix). Each
API turn is a `chat_turn` root run whose `run_id` is minted in
`api.main.turn_config` and logged as `trace_id`, with `thread_id` in
metadata so LangSmith groups a conversation as one thread. Because
importing `api.main` loads `.env`, `tests/conftest.py` forces
`LANGSMITH_TRACING=false` first — otherwise a developer's `.env` sends
every test run to LangSmith (`test_the_test_suite_never_traces`).

### Kubernetes cluster (required for the tool layer)

`/api/health` and tool-free chat work without a cluster, but any
question that makes the model call a tool needs one reachable. Local dev
target is Kind:

```bash
kind create cluster --name kube-troubleshoot
kubectl apply -f infra/kubernetes/   # demo failure scenarios, see below
```

`infra/kubernetes/` holds the scenario manifests, which serve double duty
as demo cases and eval cases: `crashloop-demo.yaml` (CrashLoopBackOff),
`imagepull-demo.yaml` (ImagePullBackOff), `oomkilled-demo.yaml`
(OOMKilled — `polinux/stress` sized to blow a 50Mi limit),
`readiness-demo.yaml` (readiness probe hits a 404 path, so the pod runs
fine but never joins its Service), `secret-demo.yaml` (`secretKeyRef` to a
nonexistent Secret → CreateContainerConfigError), `configmap-demo.yaml`
(ConfigMap exists but the referenced key is the wrong case),
`dns-demo.yaml` (client calls `payments-api`; the Service is named
`payments`), `networkpolicy-demo.yaml` (policy admits only
`app=allowed-client`, so the real client is dropped), `selector-demo.yaml`
(Service selects `component=search,tier=frontend`, pods are labelled
`tier=api` → no endpoints), `distractor-demo.yaml` (the same selector bug plus a loud
unrelated crashlooping pod — an anchoring A/B against `selector-demo`),
`initcontainer-demo.yaml` (init container exits 1, so the pod never leaves
Init), and `web-demo.yaml` (healthy nginx Deployment+Service, for
exercising `get_service_endpoints` against something that works).

Several scenarios live in `infra/kubernetes/eval-only/` instead:
`crossns-demo.yaml` + `crossns-backend-demo.yaml` (needs two namespaces),
`noisy-demo.yaml` (13 pods), and the built-to-fail set (`slowstart`,
`redherring`, `logtail`, `eventflood` (a per-minute CronJob), `targetport`,
`needle` (16 pods)). `leading` reuses `networkpolicy-demo.yaml` with a
different user request. They're kept
out of the demo bundle by living in a subdirectory — `kubectl apply -f
infra/kubernetes/` doesn't recurse — and the eval harness references them
by relative path and builds their namespaces itself.

**Don't state a scenario's difficulty without measuring it** — the eval
harness reports planner tool calls per scenario, and it has already
falsified three such claims. `readiness-demo` was documented in this file
as needing 2-3 tools; it resolves in zero, because `gather_context` sweeps
*every* event in the namespace and the probe-failure event names the cause
outright. And two predictions made while designing the hard tier were
wrong: `distractor` was expected to fail by anchoring on a loud irrelevant
crashloop, and `crossns` by stopping at a clean namespace-scoped sweep —
both passed.

Anything requiring genuine multi-hop work has to produce evidence the
sweep doesn't already return: `configmap-demo` (needs the ConfigMap's real
keys), `dns-demo`/`networkpolicy-demo`/`selector-demo` (nothing is
unhealthy at all — no failing pod status, no Warning events), `crossns`
(the cause is in another namespace entirely), `noisy` (the only signal is
one log stream among 13 healthy pods).

Manifests here carry **no `namespace:` field** on purpose, so the eval
harness can apply the same file into a throwaway `eval-<id>` namespace
while the demo copies live in `default`. They should otherwise match
what's live on the `kube-troubleshoot` cluster (`kubectl diff -f
infra/kubernetes/` is clean) — if you change one, either apply it for real
or keep it in sync with what the live demo pods look like.

`backend/tools/client.py` tries in-cluster config first, then falls back
to the ambient kubeconfig (`~/.kube/config`) — Kind writes and
kube-contexts itself there automatically. Override via `KUBE_NAMESPACE`
/ `KUBE_CONTEXT` / `KUBE_KUBECONFIG_PATH` in `backend/.env` if needed
(see `.env.example`); unset works fine against a single-context Kind
cluster.

### MCP server (`backend/mcp_server/`)

```bash
claude mcp add kube-troubleshooter -- uv run --directory "$PWD/backend" python -m mcp_server
```

One tool, `investigate(request, namespace?)`, which runs the whole graph
and returns a structured `Investigation` (diagnosis + confidence +
evidence, the fix, and the same trail steps the UI shows). It exposes
the *agent*, not the seven raw tools, on purpose: raw read-only cluster
tools are what every Kubernetes MCP server already offers; the measured
investigation is what this project adds (see `server.py`'s docstring).
Each call is a fresh thread — the client assistant keeps its own
conversation, and the returned trail lets it answer follow-ups without a
re-run. Per-node progress goes out via `ctx.report_progress`, since a run
takes 20-60s. Gotchas: **logging must go to stderr** (`__main__.py`) —
over stdio, stdout is the protocol, and one log line there breaks it;
and `--directory backend` matters, since config reads `.env` relative to
the working directory. Tracing goes to `LANGSMITH_PROJECT`, tagged `mcp`.
This is MCP SDK 2.x: `MCPServer` (FastMCP was renamed), `mcp_types`,
and an in-process `mcp.Client(server)` for tests.

### Docker (`infra/docker/`)

```bash
infra/docker/kubeconfig.sh                       # once per Kind cluster
docker compose -f infra/docker/compose.yaml up -d --build
docker compose -f infra/docker/compose.yaml run --rm backend python -m rag.index   # once per volume
docker compose -f infra/docker/compose.yaml down  # volumes (checkpoints, qdrant) survive
```

Things that were learned the hard way here:
- The backend reaches Kind by joining its `kind` Docker network with a
  kubeconfig from `kind get kubeconfig --internal` (server =
  `kube-troubleshoot-control-plane:6443`). `~/.kube/config` points at
  `127.0.0.1:<port>`, which inside a container is the container. The
  generated file is cluster-admin and gitignored.
- Published ports are bound to `127.0.0.1`, and that's load-bearing:
  Docker's proxy on WSL accepts connections on `::1` and then resets
  them, and Chromium resolving `localhost` to IPv6 doesn't fall back, so
  the UI showed "Failed to fetch" with nothing reaching the API.
  Loopback-only also keeps the unauthenticated API off the LAN.
- The app user needs a real home directory: Voyage's client downloads its
  tokenizer to `~/.cache/huggingface` on first embed, at index time *and*
  per query, so without one every `ground` retrieval fails (quietly, as
  `grounding_error`).
- uv's cache is a BuildKit cache mount in a build stage; baked into a
  layer it doubled the image. `.env` is excluded via
  `backend.Dockerfile.dockerignore` and passed with `env_file` at runtime.
- `NEXT_PUBLIC_API_URL` is a frontend *build arg* — it's inlined into the
  client bundle, so changing it means rebuilding the image.
- Compose's Qdrant has its own volume and no published ports, so it
  never collides with a dev Qdrant on 6333 — but it starts empty; index it.

### RAG / Qdrant (required for the docs-search tool)

```bash
docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage qdrant/qdrant
cd backend && uv run python -m rag.index   # builds/rebuilds the collection
```

Needs `VOYAGE_API_KEY` in `backend/.env` (Anthropic has no embeddings
API). On Voyage's free tier without a payment method on file, rate limits
are strict (3 RPM / 10K TPM) and neither `VoyageAIEmbeddings` nor
`QdrantVectorStore` back off for that — `rag/index.py` embeds in small,
spaced batches itself (`_EMBED_BATCH_SIZE`/`_EMBED_DELAY_SECONDS`) to
stay under those caps; don't replace that with a plain
`QdrantVectorStore.from_documents(all_chunks, ...)` call or reindexing
will fail with `RateLimitError` partway through.

### Frontend (`frontend/`, Next.js App Router + TypeScript + Tailwind)

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev          # dev server, http://localhost:3000
npm run build         # production build
npm run lint          # eslint
npx tsc --noEmit       # typecheck
npm test              # node:test unit tests (src/**/*.test.ts), no extra deps
```

**`frontend/AGENTS.md` matters**: this Next.js version has breaking
changes from what training data assumes. Read the relevant doc under
`node_modules/next/dist/docs/` before writing Next.js code, and don't
strip the AGENTS.md/CLAUDE.md block in `frontend/` — `next dev`
regenerates it anyway.

## Architecture

Two independent services, no shared package/monorepo tooling — each has
its own `.env`/`.env.local` (see `docs/architecture.md`'s framing: backend
secrets must never reach the frontend bundle, and `NEXT_PUBLIC_*` vars are
public by construction).

- **Backend → LLM**: model selection is never hardcoded in a node/route —
  every node goes through `models.config.get_chat_model(tier)`, which
  maps a `ModelTier` (`REASONING` or `FAST`) to a concrete model id, so
  tier/provider changes stay a one-place edit. `max_tokens` is 4096, not
  the Week-1 default of 1024 — extended-thinking tokens count against
  this same budget, and a low cap silently truncates a real diagnosis
  (`stop_reason: max_tokens`, no error) rather than failing loudly.
- **The LangGraph state machine** (`backend/graph/build.py`,
  `backend/agent/nodes.py`) replaced the Week 1-3 bounded probe/execute
  round trip: `intake` (fast tier, resolves `Scope` or short-circuits to
  a clarifying question) → `gather_context` (deterministic initial
  sweep) → `plan` (reasoning tier, bound to `tools.TOOLS` +
  `rag.search_k8s_docs_tool`) ⇄ `execute_tool` (deterministic, one tool
  call per round) → `diagnose` (reasoning tier, structured `Diagnosis`)
  → `ground` (deterministic docs retrieval on the diagnosed root cause)
  → `recommend` (reasoning tier, plain text, cites the retrieved docs).
  `ground` exists because eval measured the planner making zero docs
  searches: evidence settles the diagnosis and the public K8s docs
  overlap with model knowledge, so retrieval's real job is citing
  sources for the fix, not finding the cause. `configurable.rag=False`
  turns off both `ground` and the planner's docs tool (`eval --no-rag`). The plan↔execute_tool loop
  is capped at `graph.state.LOOP_GUARD_MAX` (8) — see
  `docs/architecture.md` §3.4/§7. `backend/api/main.py`'s chat endpoint
  calls `graph.astream(new_turn_input(message), config={"configurable":
  {"thread_id": ...}}, stream_mode=["messages", "values"])`: `"messages"` chunks tagged `langgraph_node == "recommend"`
  stream token-by-token to the frontend (the only node whose output is
  meant to read as prose); `"values"` chunks track the final state so the
  endpoint can fall back to `scope.clarifying_question` when `intake`
  ended the run early. `answer_followup` streams the same way
  (`PROSE_NODES` in `api/main.py`). Don't stream any other node's *LLM
  output* — `diagnose` and `intake` use `with_structured_output`, which
  has no user-facing text to stream. What other nodes did reaches the UI
  as the **investigation trail** instead: the endpoint also streams in
  `"updates"` mode and emits one `event: step` (JSON) per sweep, tool
  call, diagnosis, docs retrieval, or follow-up answer. Steps are built
  by `api/trail.py` from the same records a `TurnRecord` stores, and
  `GET /api/threads/{thread_id}` rebuilds the trail from those records
  on reload — so streamed and reloaded trails can't drift; add a new
  step type in `trail.py` (and `frontend/src/app/trail.tsx`'s
  `TrailStep` union), not inline in the endpoint. Tool results are cut
  to `RESULT_PREVIEW_CHARS` for display only; the model saw all of it.
  Each `ToolCallRecord` carries the planner's `rationale` (its text
  alongside the call), which is often empty — the model frequently calls
  a tool without saying why — and the UI omits it then.
- **Conversation memory** (`graph/state.py`): `AgentState` is split into
  *conversation* fields (`messages`, `turns` — append-only reducers,
  persisted by the checkpointer, written only by the `finalize` node,
  which every turn ends in, clarifying-question turns included) and
  *run* fields (everything else). A checkpointer merges a turn's input
  into the saved state rather than replacing it, so **every graph input
  must come from `new_turn_input()`**, which resets each run field to its
  default — pass a bare `{"user_request": ...}` and `step_count`
  accumulates until the loop guard trips before the first tool call,
  and the new turn diagnoses over the last one's evidence. It derives the
  reset from the model's fields, so a new run field needs no extra
  wiring; a new *conversation* field must be added to
  `CONVERSATION_FIELDS`. `build_graph(checkpointer)` is a factory: the
  API compiles it with `AsyncSqliteSaver` (`backend/data/checkpoints.db`,
  gitignored, `API_CHECKPOINT_DB_PATH`) opened in the FastAPI lifespan;
  the eval runner with `InMemorySaver` and a fresh thread per scenario.
  Both use `checkpoint_serializer()`, a strict msgpack allowlist of our
  state models — **a new Pydantic model nested in state must be added
  there**, or loading a thread containing it fails.
- **Follow-ups**: `intake` sets `Scope.is_followup` when the new message
  is answerable from earlier turns' archived evidence (`TurnRecord`s);
  `route_after_intake` then sends it to `answer_followup` (reasoning
  tier, no tools, last `FOLLOWUP_TURNS` investigations as context)
  instead of `gather_context`. The route is honoured only if an earlier
  turn actually investigated — a guard in code, not the prompt, so a
  misclassified first turn can't skip the investigation and answer from
  nothing. Questions about *current* state ("is it fixed now?", "what
  about the other pod?") must still investigate; `intake_v3` says so.
  Each `TurnRecord` carries its `route` (`investigate`/`followup`/
  `clarify`) and the `reply` shown.
- **Logging** (`backend/observability/`): stdlib `logging` with a JSON
  (`LOG_FORMAT=json`) or `key=value` console formatter — same fields
  either way. Log an *event name* plus fields via `extra=` (e.g.
  `log.info("tool.call", extra={...})`), never prose with values baked
  in. `bind(request_id=..., thread_id=...)` attaches context to every
  record in a chat turn, graph nodes included; it's entered inside the
  SSE generator because that runs in the response task. **Don't use a
  `LogRecord` attribute name as an `extra` key** (`args`, `name`,
  `msg`, `module`, ...): `logging` raises `KeyError`, and inside a node
  that's a crash — `tool.call` logs `tool_args` for exactly this reason,
  after the test suite caught it. User-written text is never logged, only
  its length; `sse_starlette` is held at WARNING because its DEBUG output
  is every response chunk, i.e. the reply text.
- **Tool catalog** (`backend/tools/`): plain functions
  (`pods.py`/`events.py`/`logs.py`/`nodes.py`/`services.py`/`describe.py`/`policies.py`)
  against the official `kubernetes` Python client — chosen over shelling
  out to `kubectl` so read-only is enforced by which client methods get
  called (`list_*`/`read_*`/`get_*` only), not by parsing text. Each
  returns a normalized Pydantic model from `models.py`, never a raw API
  object. `describe_resource` composes object status + filtered events
  itself, since `kubectl describe` has no JSON form to parse; it covers
  pod/deployment/service/node/configmap/secret, and for configmap/secret
  returns **key names and value sizes but never values** — the
  missing-key diagnoses it exists for need the key list and nothing more.
  For a pod it also returns `config_references`: every Secret/ConfigMap
  the pod reads via `env`/`envFrom`/volumes, with the key it asks for —
  again never values, and literal `env[].value` strings are omitted
  entirely. It deliberately does **not** return container ports: the
  `targetport` scenario exists to measure honesty about that gap.
  `get_network_policies` resolves each policy's label selector server-side
  into the pods it actually selects, so the model isn't evaluating
  selectors by eye. `get_pod_status` returns `init_containers` separately
  from `containers` plus each pod's `labels` — **anything that iterates
  container statuses must scan both lists**, since a pod blocked in init
  reports only `PodInitializing` on its app containers, which names
  neither the failing init container nor why; `describe.py` and
  `eval/cluster.py`'s readiness predicate both do. `langchain_tools.py` wraps these as `@tool`s for
  Anthropic tool calling, kept separate so the LangGraph nodes in
  `backend/agent/` and the eval harness can call the plain functions
  directly with no LangChain dependency — `backend/eval/cluster.py` does
  exactly that to decide when a scenario has finished breaking. Known gap:
  credentials are whatever the ambient kubeconfig grants (Kind's admin
  config, locally) — no RBAC-scoped read-only ServiceAccount yet, which
  `docs/architecture.md` §5 calls for as defense in depth; enforcement
  today is code-layer only.
- **Nothing in a node may raise on a recoverable condition.** Eval has now
  caught four crashes of this shape, each killing a run that had already
  gathered the evidence it needed: a 404 escaping `execute_tool`, an
  unreachable sweep in `gather_context`, `Diagnosis` raising
  `ValidationError` because `with_structured_output` returned without the
  required `confidence` field, and `recommend` returning
  `response.content` — a list of blocks, not a string, whenever the model
  emits a thinking block. Hence every field on `Diagnosis` and
  `Scope` carries a default (`confidence` defaults to `"low"`, so an
  omission never reads as certainty). Don't make a structured-output
  field required *in Python*, and read model text via `.text`, never
  `.content` (`api/main.py`'s streaming path included). Structured
  output goes through `with_structured_output(..., method="json_schema")`
  (constrained decoding), never the default `function_calling`: that
  forces a tool call (which Sonnet 5.5 / Opus 5.5 reject) and once leaked
  tool-call markup that dropped a field. `STRUCTURED_OUTPUT_CONFIG` in
  `graph/state.py` marks every field required in the *schema sent to the
  model*, so the model must fill them; the Python defaults stay as the
  backstop. Put developer notes on these models in comments, not
  docstrings, since the docstring becomes the schema description the
  model sees. The eval judge (`JudgeVerdict`) deliberately has no
  defaults: it retries once, then falls back to the signal check.
- **Failed tool calls are evidence, not accidents.** `execute_tool`
  catches exceptions from a tool call, normalizes them via
  `tools/errors.py`, and appends the result to the investigation log
  instead of letting them propagate; `gather_context`'s initial sweep is
  guarded the same way. This is not defensive boilerplate — it's a fix for
  a real bug the eval harness caught on its first run, where the agent
  asked about a missing Secret, got a correct 404 (*the answer*), and the
  raw `ApiException` killed the entire graph run. Don't reintroduce a bare
  `await tool.ainvoke(...)` there. `execute_tool` also fills in
  `scope.namespace` when the planner omits `namespace` (it does so in most
  runs); without that the call silently hits `default`, and the resulting
  404 reads as "doesn't exist" — see `backend/eval/README.md`, "Defects
  eval caught".
- **RAG** (`backend/rag/`): `corpus/` is ~22 curated K8s/kubectl doc pages
  (YAML frontmatter + markdown, scoped to the failure-scenario catalog,
  not a full site crawl) → `index.py` chunks them
  (`RecursiveCharacterTextSplitter`, markdown-aware) and embeds via
  Voyage AI into Qdrant → `retriever.py`'s `search_docs()` does the
  query-time similarity search, returning normalized
  `{title, source_url, content, score}` results, no LangChain dependency
  — same plain-function-vs-`@tool`-adapter split as `backend/tools/`, for
  the same reason (LangGraph node / eval-harness reuse without a
  LangChain dependency).
  `retriever.py` caches its `QdrantVectorStore` (`@lru_cache`) and passes
  `validate_collection_config=False`: the default constructor otherwise
  embeds a dummy string on every call just to check vector-size
  compatibility, which is both wasteful and, on Voyage's throttled free
  tier, enough by itself to trigger `RateLimitError` on repeated
  searches.
- **Frontend → Backend**: `frontend/src/app/page.tsx` keeps only the
  conversation's `thread_id` (in `localStorage`, wrapped in try/catch —
  storage can be unavailable, in which case each load is a new
  conversation), fetches `GET /api/threads/{id}` on load to rehydrate, and
  mints a new id for "New conversation". It calls `POST
  /api/chat` and hand-parses the SSE response itself (`event: token` /
  `event: step` / `event: error` / `event: done`) rather than using `EventSource`, because
  `EventSource` can't send a POST body. `sse-starlette` emits CRLF
  (`\r\n`) line endings and can split one token's text across multiple
  `data:` lines — the parser normalizes `\r\n`→`\n` before framing on
  blank lines and joins every `data:` line per event; don't reintroduce a
  bare `\n\n` split or a first-`data:`-line-only read, both silently drop
  content instead of erroring. The parser lives in `src/lib/sse.ts` with
  tests beside it; it normalizes CRLF across the whole buffer, because
  per-chunk normalization left a stray `\r` on a token whenever a `\r\n`
  split across network chunks. Assistant messages render through
  `react-markdown`; user messages stay plain text.
- **Prompts** are versioned files under `backend/prompts/` — one per
  graph node (`intake_v3.md` (live; adds the follow-up decision — v1/v2
  retired), `followup_v1.md`,
  `plan_v1.md`, `diagnose_v1.md`, `recommend_v2.md` (live; v1 retired —
  v2 cites only the docs `ground` retrieved), plus `eval_judge_v2.md` for
  the eval harness's LLM judge; `chat_v1-3.md` are
  the retired Week 1-3 single-prompt versions, kept for history) — loaded
  by name via `prompts.load_prompt()`. Add a new version file rather than
  editing one in place when a prompt changes behavior you want to compare
  against. `intake_v2` exists because eval caught v1 short-circuiting an
  answerable request ("a pod in namespace X won't start") into a
  clarifying question, which ends the run and returns no diagnosis at all;
  v2 spells out that the initial sweep will find the resource, so a
  missing resource name is never grounds for clarifying.
- **Safety boundary** (binding for all future tool work, not just a
  suggestion): tools must be read-only by construction — enforced in the
  tool layer, not the prompt. No tool may expose a mutating `kubectl` verb
  (`apply`, `delete`, `edit`, `scale`, `rollout`). Fixes are always
  suggested as text, never executed by the agent.
- **LangChain vs. LangGraph**: LangGraph now owns orchestration — the
  state graph in `backend/graph/build.py`, the plan/execute loop, the
  loop guard, the checkpointer. LangChain is
  used for RAG plumbing (`langchain-qdrant`, `langchain-text-splitters`,
  `langchain-voyageai`) and the model wrapper (`ChatAnthropic`) only; the
  node functions in `backend/agent/nodes.py` call `chat_model`/tools
  directly rather than wrapping the loop in a LangChain chain.
