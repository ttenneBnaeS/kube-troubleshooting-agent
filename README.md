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

**Week 4**: the real LangGraph state machine — `intake` →
`gather_context` → `plan`/`execute_tool` loop → `diagnose` → `recommend`,
with the plan↔execute loop capped by a step guard. The Week 1-3 bounded
round-trip in the chat endpoint is gone; the endpoint now drives the graph
and streams the `recommend` node's tokens.

**Week 5**: failure scenarios and the eval harness. Eight injected
scenarios with golden labels, and a harness that applies each into a
throwaway namespace, waits for it to actually break, runs the agent, and
scores the diagnosis against ground truth — see
[Evaluating the agent](#evaluating-the-agent).

## Setup

Beyond the backend/frontend env files below, the tool-calling and RAG
paths need:

- **A reachable Kubernetes cluster with something broken to diagnose** —
  local dev target is [Kind](https://kind.sigs.k8s.io/):
  ```bash
  kind create cluster --name kube-troubleshoot
  kubectl apply -f infra/kubernetes/
  ```
  The second command applies the demo scenarios so there's actually
  something to ask the agent about — eight deliberate failures plus one
  healthy control:

  | Manifest | Failure |
  |---|---|
  | `crashloop-demo` | container exits 1 right after starting → `CrashLoopBackOff` |
  | `imagepull-demo` | bogus image reference → `ImagePullBackOff` |
  | `oomkilled-demo` | workload blows past its 50Mi limit → repeated `OOMKilled` |
  | `readiness-demo` | readiness probe hits a 404 path, so the pod runs but never joins its Service |
  | `secret-demo` | `secretKeyRef` to a Secret that doesn't exist → `CreateContainerConfigError` |
  | `configmap-demo` | ConfigMap exists but the referenced key is the wrong case → `CreateContainerConfigError` |
  | `dns-demo` | client calls `payments-api`, the Service is named `payments` → name resolution fails |
  | `networkpolicy-demo` | policy admits only `app=allowed-client`, so the real client's packets are dropped |
  | `selector-demo` | Service selects `app=search-api-v2`, pods are labelled `app=search-api` → zero endpoints |
  | `distractor-demo` | same selector bug, plus a loud unrelated crashlooping pod competing for attention |
  | `initcontainer-demo` | init container can't reach its database and exits 1, so the pod never leaves `Init` |
  | `web-demo` | healthy nginx Deployment/Service, for exercising `get_service_endpoints` against something that *isn't* broken |

  Two further scenarios live in `infra/kubernetes/eval-only/` and are
  excluded from the demo bundle (`kubectl apply -f` doesn't recurse):
  `crossns-demo` needs two namespaces to mean anything, and `noisy-demo`
  puts thirteen pods in one namespace. Both are exercised by the eval
  harness, which builds and destroys their namespaces itself.

  They vary deliberately in how much investigation they need. The
  imagepull and readiness cases resolve from the initial sweep alone; the
  ConfigMap case needs the agent to read the ConfigMap's actual keys; and
  the DNS and NetworkPolicy cases are adversarial — every pod is Running
  and Ready, the Services have ready endpoints, and no Warning event is
  ever emitted, so the only evidence is in container logs and the policy
  itself. Without a cluster at all, `/api/health` and tool-free chat still
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

## Evaluating the agent

The eval harness measures diagnosis correctness against injected failures
rather than asserting the agent works. Each of the thirteen scenarios
carries a golden label — the true root cause and the remediation that
actually fixes it — and the harness applies it into a throwaway
`eval-<id>` namespace, waits until the failure is genuinely observable,
runs the agent against a natural-language complaint that names the symptom
but never the cause, scores the diagnosis, and tears the namespace down.

```bash
cd backend
uv run python -m eval                   # all thirteen scenarios
uv run python -m eval -s dns -s secret  # a subset
uv run python -m eval --list            # what's available
```

Scenarios are tiered `easy`/`medium`/`hard` and reported per tier, because
one blended number stops being interpretable once the suite spans trivial
and adversarial cases. The easy tier is a regression guard whose *mean
tool count* matters as much as its accuracy — it's what catches the agent
starting to over-investigate things it used to answer immediately.

Scoring pairs a deterministic signal check with an LLM judge. The judge
owns the verdict — keyword matching can't tell a valid paraphrase from a
miss — while the signal check names *which* expected signal was absent.
Alongside correctness the harness tracks planner tool calls per diagnosis
(to catch thrashing), whether the loop guard fired, and whether the agent
gathered the evidence the golden label expects. Every run writes a full
JSON record — each tool call with its arguments, the diagnosis, and the
judge's reasoning — to `backend/eval/results/`. LangSmith tracing is
optional and env-gated.

Details, including how to add a scenario, are in
[`backend/eval/README.md`](backend/eval/README.md).

### What the runs caught

The harness earned its place immediately. Its first run found a bug that
the demo cluster had never surfaced: the agent called `describe_resource`
on a Secret to confirm the Secret was missing, the Kubernetes API
correctly returned 404 — *which was the answer* — and the raw exception
propagated out of `execute_tool` and killed the whole graph run. Tool
failures are now normalized into facts and appended to the investigation
log, so the planner can reason about them. The second run caught `intake`
short-circuiting a perfectly answerable request into a clarifying
question, which prompted `intake_v2`.

Adding the harder tier caught a third crash of the same shape: the
`diagnose` node's structured output arrived without its `confidence`
field, and because the schema required it, pydantic raised inside the
graph and killed a run that had already gathered everything it needed.
Every field on `Diagnosis` now has a default — `confidence` defaults to
`"low"`, so an omission can never read as certainty.

It also corrected a claim this README used to make. The readiness-probe
scenario was written as a multi-hop case; measurement showed the agent
resolves it in *zero* planner tool calls, because the graph's initial
sweep already returns every event in the namespace, probe failure
included. That scenario is now kept precisely as a measure of how much the
deterministic sweep is doing on its own.

And it disproved two predictions made while designing the hard tier.
`distractor` was built expecting the agent to anchor on the loud
irrelevant crashloop; it instead diagnosed the selector bug and explicitly
noted the crashloop as unrelated. `crossns` was built expecting a clean
sweep to stop the investigation; the agent found the namespace from an
FQDN in a log line and kept going. Both passed — but `crossns` burned four
tool calls guessing at resource names before reading the logs that held
the only breadcrumb, which is the kind of thrashing the tool-efficiency
metric exists to surface.
