# Kubernetes Troubleshooting Agent

A LangGraph agent that diagnoses Kubernetes failures using read-only tool
calling and RAG over official docs, with fixes suggested (never applied)
for a human to run. See [`docs/architecture.md`](docs/architecture.md) for
the full design.

## Status

**Week 1**: architecture doc, FastAPI streaming skeleton, minimal Next.js
chat UI. Single LLM round trip, no tools yet.

**Week 2**: read-only Kubernetes tool catalog (pod status, describe, logs,
events, node status, service endpoints) against a real cluster via the
official `kubernetes` Python client.

**Week 3**: RAG over official Kubernetes/kubectl docs. A curated corpus is
chunked, embedded with Voyage AI, and indexed into Qdrant; retrieval is
exposed as another tool the model can call to ground a diagnosis or fix in
real docs, alongside the Week 2 cluster tools. With two tool categories
now in play, diagnosing-then-grounding is naturally a multi-step tool
sequence, so the chat endpoint loops the probe→execute cycle (capped at 5
rounds) rather than allowing just one round trip — still not the full
LangGraph plan/execute graph (Week 4), but no longer silently drops the
answer when a second tool call is needed.

## Setup

Beyond the backend/frontend env files below, the tool-calling and RAG
paths need:

- **A reachable Kubernetes cluster** — local dev target is
  [Kind](https://kind.sigs.k8s.io/): `kind create cluster --name
  kube-troubleshoot`. Without one, `/api/health` and tool-free chat still
  work, but cluster questions will error.
- **Qdrant**, for the docs-search tool:
  ```bash
  docker run -d --name qdrant -p 6333:6333 -p 6334:6334 \
    -v qdrant_storage:/qdrant/storage qdrant/qdrant
  ```
- **A Voyage AI API key** (`VOYAGE_API_KEY` in `backend/.env`) — Anthropic
  has no embeddings API, so Voyage embeds the docs corpus. Free tier works
  but throttles hard without a payment method on file; `rag/index.py`
  batches/paces requests to stay under those limits.
- Once Qdrant and the Voyage key are set up, build the index (one-time,
  re-run whenever the corpus changes):
  ```bash
  cd backend
  uv run python -m rag.index
  ```

See `backend/tools/README.md` and `backend/rag/README.md` for more detail
on each piece.

## Running locally

### Backend

```bash
cd backend
cp .env.example .env   # fill in ANTHROPIC_API_KEY, VOYAGE_API_KEY
uv run uvicorn api.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open http://localhost:3000.
