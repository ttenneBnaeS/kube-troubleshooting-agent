# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A LangGraph agent that diagnoses Kubernetes failures using read-only tool
calling and RAG over official docs, suggesting fixes as text for a human
to run rather than applying them. It's a portfolio project targeting AI
Engineer roles — the point is demoable fluency with the current agentic
stack, not the Kubernetes domain knowledge itself. Full design lives in
`docs/architecture.md`; the phased build-out is in
`Kubernetes_Troubleshooting_Agent_Plan_v2.md` (8-week roadmap).

**Read `docs/architecture.md` before making any structural change** — it
records decisions (LangChain/LangGraph split, model tier routing, tool
design philosophy, the read-only safety boundary) that were deliberately
locked in up front so they wouldn't get re-litigated mid-build.

## Current state vs. planned state

The repo is scaffolded for the full 8-week plan; Weeks 1-5 are
implemented. `backend/tools/` has a real read-only Kubernetes tool
catalog (pod status, describe, logs, events, node status, service
endpoints, network policies) against the official `kubernetes` Python
client. `backend/rag/` has a real RAG pipeline (curated K8s/kubectl doc
corpus → Voyage AI embeddings → Qdrant) exposed as another tool.
`backend/graph/` and `backend/agent/` hold a real LangGraph state machine
(`intake` → `gather_context` → `plan`/`execute_tool` loop → `diagnose` →
`recommend`) that `backend/api/main.py`'s chat endpoint drives directly —
the Week 1-3 bounded probe/execute round-trip in `main.py` is gone.
`backend/eval/` holds the Week 5 eval harness, and
`infra/kubernetes/` now has thirteen golden-labelled failure scenarios
(tiered easy/medium/hard) plus one healthy control.

Still open: `tests/` and `demo/` are empty placeholders; conversational
memory is still "client resends full history" (`AgentState.messages`), not
a LangGraph checkpointer — that's Week 6, along with the
investigation-trail UI. Week 7 is tests/Docker/structured logging, Week 8
the MCP server.

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
- No test suite or linter is configured yet for the backend (Week 7). The
  eval harness below is the closest thing to a regression check.

### Eval harness (`backend/eval/`)

Needs a reachable Kind cluster with the demo scenarios' images pullable,
and `kubectl` on PATH. A full run is ~5-8 minutes, mostly spent waiting
for scenarios to break rather than on agent latency.

```bash
cd backend
uv run python -m eval                   # all thirteen scenarios
uv run python -m eval -s dns -s secret  # a subset
uv run python -m eval --list            # what's available
uv run python -m eval --no-judge        # deterministic scoring only (no LLM judge calls)
uv run python -m eval --keep            # leave eval-* namespaces up to inspect
```

Runs are sequential deliberately — scenarios contend for node memory (the
OOM case especially) and the RAG tool sits behind Voyage's free-tier rate
limits, so parallelism produces flake that reads as agent error. Don't
"optimize" that into a `gather()`.

Each run writes a full JSON record to `backend/eval/results/`
(gitignored). LangSmith tracing is optional, env-gated via `LANGSMITH_*`
in `backend/.env`; `eval/tracing.py` has to load `.env` into `os.environ`
itself, because backend config goes through pydantic-settings (which
doesn't export there) while LangChain reads the environment directly.

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
(Service selects `app=search-api-v2`, pods are labelled `app=search-api` →
no endpoints), `distractor-demo.yaml` (the same selector bug plus a loud
unrelated crashlooping pod — an anchoring A/B against `selector-demo`),
`initcontainer-demo.yaml` (init container exits 1, so the pod never leaves
Init), and `web-demo.yaml` (healthy nginx Deployment+Service, for
exercising `get_service_endpoints` against something that works).

Two scenarios live in `infra/kubernetes/eval-only/` instead
(`crossns-demo.yaml` + `crossns-backend-demo.yaml`, which need two
namespaces, and `noisy-demo.yaml`, which creates 13 pods). They're kept
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
  → `recommend` (reasoning tier, plain text). The plan↔execute_tool loop
  is capped at `graph.state.LOOP_GUARD_MAX` (8) — see
  `docs/architecture.md` §3.4/§7. `backend/api/main.py`'s chat endpoint
  calls `troubleshooting_graph.astream(..., stream_mode=["messages",
  "values"])`: `"messages"` chunks tagged `langgraph_node == "recommend"`
  stream token-by-token to the frontend (the only node whose output is
  meant to read as prose); `"values"` chunks track the final state so the
  endpoint can fall back to `scope.clarifying_question` when `intake`
  ended the run early. Don't stream any other node's output — `diagnose`
  and `intake` use `with_structured_output`, which forces tool-calling
  under the hood and has no user-facing text to stream.
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
  caught three crashes of this shape, each killing a run that had already
  gathered the evidence it needed: a 404 escaping `execute_tool`, an
  unreachable sweep in `gather_context`, and `Diagnosis` raising
  `ValidationError` because `with_structured_output` returned without the
  required `confidence` field. Hence every field on `Diagnosis` and
  `Scope` carries a default (`confidence` defaults to `"low"`, so an
  omission never reads as certainty). Don't make a structured-output
  field required.
- **Failed tool calls are evidence, not accidents.** `execute_tool`
  catches exceptions from a tool call, normalizes them via
  `tools/errors.py`, and appends the result to the investigation log
  instead of letting them propagate; `gather_context`'s initial sweep is
  guarded the same way. This is not defensive boilerplate — it's a fix for
  a real bug the eval harness caught on its first run, where the agent
  asked about a missing Secret, got a correct 404 (*the answer*), and the
  raw `ApiException` killed the entire graph run. Don't reintroduce a bare
  `await tool.ainvoke(...)` there.
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
- **Frontend → Backend**: `frontend/src/app/page.tsx` calls `POST
  /api/chat` and hand-parses the SSE response itself (`event: token` /
  `event: error` / `event: done`) rather than using `EventSource`, because
  `EventSource` can't send a POST body. `sse-starlette` emits CRLF
  (`\r\n`) line endings and can split one token's text across multiple
  `data:` lines — the parser normalizes `\r\n`→`\n` before framing on
  blank lines and joins every `data:` line per event; don't reintroduce a
  bare `\n\n` split or a first-`data:`-line-only read, both silently drop
  content instead of erroring. Assistant messages render through
  `react-markdown`; user messages stay plain text.
- **Prompts** are versioned files under `backend/prompts/` — one per
  graph node (`intake_v2.md` (live; `intake_v1.md` retired),
  `plan_v1.md`, `diagnose_v1.md`, `recommend_v1.md`, plus
  `eval_judge_v1.md` for the eval harness's LLM judge; `chat_v1-3.md` are
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
  loop guard. Checkpointing is not wired in yet (Week 6). LangChain is
  used for RAG plumbing (`langchain-qdrant`, `langchain-text-splitters`,
  `langchain-voyageai`) and the model wrapper (`ChatAnthropic`) only; the
  node functions in `backend/agent/nodes.py` call `chat_model`/tools
  directly rather than wrapping the loop in a LangChain chain.
