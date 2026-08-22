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

The repo is scaffolded for the full 8-week plan; Weeks 1-2 are
implemented. `backend/tools/` has a real read-only Kubernetes tool
catalog (pod status, describe, logs, events, node status, service
endpoints) against the official `kubernetes` Python client, plus a
LangChain `@tool` adapter layer, and `backend/api/main.py`'s chat
endpoint does a single tool-call round trip — not yet the full
LangGraph plan/execute loop. `backend/agent/`, `backend/graph/`, and
`backend/rag/` each still contain only an `__init__.py` and a
`README.md` stating which week fills them in — don't expect a LangGraph
state machine or RAG retrieval to exist yet. `eval/`, `infra/`,
`tests/`, and `demo/` are likewise empty placeholders for later weeks.

## Commands

### Backend (`backend/`, uv-managed Python 3.13)

```bash
cd backend
cp .env.example .env        # fill in ANTHROPIC_API_KEY
uv run uvicorn api.main:app --reload --port 8000
```

- Add a dependency: `uv add <package>` (run from `backend/`)
- No test suite or linter is configured yet for the backend.

### Kubernetes cluster (required for the tool layer)

`/api/health` and tool-free chat work without a cluster, but any
question that makes the model call a tool needs one reachable. Local dev
target is Kind:

```bash
kind create cluster --name kube-troubleshoot
```

`backend/tools/client.py` tries in-cluster config first, then falls back
to the ambient kubeconfig (`~/.kube/config`) — Kind writes and
kube-contexts itself there automatically. Override via `KUBE_NAMESPACE`
/ `KUBE_CONTEXT` / `KUBE_KUBECONFIG_PATH` in `backend/.env` if needed
(see `.env.example`); unset works fine against a single-context Kind
cluster.

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

- **Backend → LLM**: `backend/api/main.py` builds a LangChain message
  list, binds the tool catalog (`tools.TOOLS`) via `bind_tools`, and
  streams tokens from `ChatAnthropic` over SSE (`sse-starlette`). Model
  selection is never hardcoded in a node/route — it goes through
  `models.config.get_chat_model(tier)`, which maps a `ModelTier`
  (`REASONING` or `FAST`) to a concrete model id. This is the same
  chokepoint later LangGraph nodes will use, so tier/provider changes stay
  a one-place edit.
- **Tool-call round trip is intentionally partial**: the chat endpoint
  probes for tool calls, executes at most one round of them, appends the
  results, then streams the final answer — not the bounded plan/execute
  loop in `docs/architecture.md` §7, which is Week 4's job. If the model
  wants a second tool call after seeing the first result, it won't get
  one yet; that gap is expected until Week 4 replaces this with the real
  LangGraph loop.
- **Tool catalog** (`backend/tools/`): plain functions
  (`pods.py`/`events.py`/`logs.py`/`nodes.py`/`services.py`/`describe.py`)
  against the official `kubernetes` Python client — chosen over shelling
  out to `kubectl` so read-only is enforced by which client methods get
  called (`list_*`/`read_*`/`get_*` only), not by parsing text. Each
  returns a normalized Pydantic model from `models.py`, never a raw API
  object. `describe_resource` composes object status + filtered events
  itself, since `kubectl describe` has no JSON form to parse.
  `langchain_tools.py` wraps these as `@tool`s for Anthropic tool
  calling, kept separate so Week 4's LangGraph nodes and Week 5's eval
  harness can call the plain functions directly with no LangChain
  dependency. Known gap: credentials are whatever the ambient kubeconfig
  grants (Kind's admin config, locally) — no RBAC-scoped read-only
  ServiceAccount yet, which `docs/architecture.md` §5 calls for as
  defense in depth; enforcement today is code-layer only.
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
- **Prompts** are versioned files under `backend/prompts/` (e.g.
  `chat_v2.md`), loaded by name via `prompts.load_prompt()` — add a new
  version file rather than editing one in place when a prompt changes
  behavior you want to compare against.
- **Safety boundary** (binding for all future tool work, not just a
  suggestion): tools must be read-only by construction — enforced in the
  tool layer, not the prompt. No tool may expose a mutating `kubectl` verb
  (`apply`, `delete`, `edit`, `scale`, `rollout`). Fixes are always
  suggested as text, never executed by the agent.
- **LangChain vs. LangGraph**: LangGraph (arriving Week 4) owns
  orchestration — the state graph, plan/execute loop, checkpointing.
  LangChain is used for RAG plumbing (Week 3) and the model wrapper
  (`ChatAnthropic`) only; the agent loop itself is not wrapped in a
  LangChain chain.
