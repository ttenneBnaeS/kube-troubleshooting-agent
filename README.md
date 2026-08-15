# Kubernetes Troubleshooting Agent

A LangGraph agent that diagnoses Kubernetes failures using read-only tool
calling and RAG over official docs, with fixes suggested (never applied)
for a human to run. See [`docs/architecture.md`](docs/architecture.md) for
the full design.

## Status

**Week 1**: architecture doc, FastAPI streaming skeleton, minimal Next.js
chat UI. Single LLM round trip, no tools yet.

## Running locally

### Backend

```bash
cd backend
cp .env.example .env   # fill in ANTHROPIC_API_KEY
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
