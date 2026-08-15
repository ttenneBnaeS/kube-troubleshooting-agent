# Kubernetes Troubleshooting Agent — In-Depth Project Plan

## 1. What this project is proving

This is a portfolio project aimed at AI Engineer roles. Its job is to give you first-hand, demoable fluency with the current agentic stack (LangGraph, tool calling, RAG, eval, MCP) using tools you *can't* touch at work, while leaning on Kubernetes — a domain you already know cold — so your time goes into agent engineering rather than learning the problem space.

The signal it sends to a hiring manager:

- You can design an agent loop with explicit state, not an ad-hoc `while True` with a dictionary.
- You separate deterministic fact-gathering from LLM-driven judgment (the same instinct you're applying to the rack-server tool).
- You can *measure* an agent, not just claim it works.
- You understand read-only/safety boundaries when an agent touches real infrastructure.

Keep that framing in mind — every milestone should ladder up to one of those points, and anything that doesn't is a candidate for the cut list (Section 10).

---

## 2. Architecture decisions to make before writing code

Lock these down in Week 1 so you're not re-litigating them mid-build.

### 2.1 LangChain vs LangGraph boundary

Decide the division of labor explicitly, because "I used both" is a weak interview answer and "I used LangGraph for X and reserved LangChain for Y" is a strong one.

Recommended split:
- **LangGraph** owns orchestration: the state machine, the plan/execute loop, conditional routing, checkpointing, memory.
- **LangChain** is used narrowly — really just for the RAG plumbing (document loaders, text splitters, the retriever interface over Qdrant) and the model wrapper. Do *not* wrap your agent loop in LangChain chains; call the model and your tools directly from LangGraph nodes.

This keeps your control flow legible and gives you a clean story about why each library is present.

### 2.2 Model choice and routing

Since the whole point is using non-work-approved tooling, use a current frontier model here. A defensible setup:
- A strong reasoning model (Claude Sonnet-class or equivalent) for the planning and diagnosis nodes.
- A cheap/fast model (Haiku-class) for narrow classification steps — e.g. "is the user's message a new incident or a follow-up?" or "which resource type is this about?"

Even a two-tier split like this is worth a resume line, because model-tier routing demonstrates cost awareness, which postings explicitly screen for. Don't over-engineer it — two tiers is plenty.

### 2.3 Tool design philosophy (this is the important one)

Apply the same principle you arrived at for the rack-server system: **fact-gathering steps produce clean structured data; judgment steps stay LLM-driven.**

Concretely, that means:
- Tools return **normalized, structured output** (parsed pod status, extracted container states, decoded reasons), not raw `kubectl` text dumps that the model has to re-parse every turn.
- Do the noisy parsing/normalization **in Python**, deterministically, before the model ever sees it — the same way you're doing deterministic log preprocessing at work.
- Prefer **purpose-built tools** (`get_pod_status`, `get_recent_events`, `get_container_logs`) over a single general `run_kubectl` passthrough. Purpose-built tools let you control the output shape, enforce read-only, and make the agent's transcript readable — all of which help in a demo.

### 2.4 Safety boundary: read-only by construction

The agent must not be able to mutate the cluster. This is both correct engineering and excellent interview material.

- Every tool is read-only: `get`, `describe`, `logs`, `events`, `top`. No `apply`, `delete`, `edit`, `scale`, `rollout`.
- Fixes are **suggested, never applied** — the agent outputs the recommended `kubectl`/manifest change as text for a human to run.
- Enforce this at the tool layer (the tools simply don't expose mutating verbs), not just in the prompt. "I enforced it in code, not just by asking the model nicely" is the answer you want to be able to give.

---

## 3. The LangGraph state graph

Design the graph on paper before building. Here's a proposed structure — a structured plan/execute loop rather than a bare ReAct agent.

### 3.1 State object

Keep a single typed state (a `TypedDict` or Pydantic model) threaded through the graph. Rough shape:

```
AgentState:
  user_request: str
  scope: {namespace, resource_type, resource_name}   # resolved target
  context_snapshot: dict        # structured facts gathered so far
  investigation_log: list       # tools called + structured results
  hypothesis: str | None        # current working theory
  diagnosis: dict | None        # root cause + confidence
  recommendation: str | None    # suggested fix (text only)
  messages: list                # conversational history (for memory)
  step_count: int               # loop guard
```

### 3.2 Nodes

1. **`intake`** *(LLM, cheap tier)* — parse the user's complaint, resolve scope (namespace / resource). If ambiguous, this is where a clarifying question could fire.
2. **`gather_context`** *(deterministic, no LLM)* — initial fixed sweep: pod list + status, recent events, resource state. Produces the first structured `context_snapshot`. This is your fact-gathering step — no model judgment, just clean data.
3. **`plan`** *(LLM, reasoning tier)* — given the snapshot and investigation log, decide one of: (a) call a specific tool to gather more evidence, (b) enough evidence → move to diagnosis. This is the judgment step. It emits a structured decision (which tool + args, or "diagnose now").
4. **`execute_tool`** *(deterministic)* — run the chosen read-only tool, append normalized result to `investigation_log`.
5. **`diagnose`** *(LLM, reasoning tier)* — synthesize root cause with a confidence level and cite which pieces of evidence support it.
6. **`recommend`** *(LLM, reasoning tier)* — produce the suggested fix as text, explicitly framed as "run this yourself."

### 3.3 Edges

```
intake → gather_context → plan
plan ──(need more evidence)──▶ execute_tool ──▶ plan     # the loop
plan ──(enough evidence)─────▶ diagnose ──▶ recommend ──▶ END
```

### 3.4 Loop control

Add a hard guard: cap the plan→execute loop at, say, 8 iterations (`step_count`). If it hits the cap, force a transition to `diagnose` with whatever evidence exists and lower the stated confidence. This prevents runaway loops and is a concrete answer to "how did you handle non-termination?"

### 3.5 Checkpointing & memory

Use LangGraph's checkpointer for conversational memory across turns (follow-ups like "what about the other pod?"). This also gives you human-in-the-loop capability almost for free if you want it later.

---

## 4. Tool catalog (read-only)

Start with these; each returns normalized structured data, not raw text:

| Tool | Wraps | Returns (normalized) |
|---|---|---|
| `get_pod_status` | `get pods` | name, phase, container states, restart counts, age |
| `describe_resource` | `describe <kind>` | parsed conditions, events, key fields |
| `get_container_logs` | `logs` | last N lines, with optional `--previous` for crashed containers |
| `get_recent_events` | `get events` | sorted, filtered to the target, deduped |
| `get_node_status` | `get nodes` / `top nodes` | conditions, pressure flags, allocatable vs used |
| `get_service_endpoints` | `get svc` / `get endpoints` | selectors, endpoint readiness |

The normalization work (parsing container states, decoding reasons like `CrashLoopBackOff` → structured fields) is the deterministic layer. Resist the urge to hand the model raw dumps.

---

## 5. RAG design

Scope this tightly — RAG here is a supporting actor, not the main event.

- **Corpus:** official Kubernetes docs, `kubectl` reference, and Helm docs. Chunk and embed into Qdrant.
- **Retrieval:** invoked as a *tool the planner can call* ("look up what readiness probe failures mean") rather than always-on retrieval on every turn. This is a cleaner pattern and demonstrates you understand when retrieval helps vs. adds noise.
- **What it's for:** grounding explanations and fix recommendations in canonical docs, so the agent cites real remediation steps rather than hallucinating flag names.
- **Keep it honest:** don't oversell RAG in the writeup — its role is grounding the diagnosis/recommendation, not doing the diagnosis.

---

## 6. Eval harness (do not skip this)

This is the single highest-leverage addition to your original plan. It turns "it works" into a number.

### 6.1 Approach

- Maintain a set of **injected failure scenarios** as Kubernetes manifests that deliberately break in known ways (see Section 7).
- For each scenario, write a **golden label**: the true root cause, and ideally the expected remediation category.
- The harness spins up each scenario in Kind, runs the agent against it, and scores:
  - **Diagnosis correctness** — did it identify the right root cause? (the primary metric)
  - **Tool efficiency** — how many tool calls did it take? (catches thrashing)
  - **Grounding** — did the recommendation reference the right remediation?

### 6.2 Tooling

- Use an eval framework (RAGAS for the retrieval-grounding pieces; a simple custom scorer or LangSmith-based eval for diagnosis correctness). Even a hand-rolled scorer that checks the diagnosis against the golden label is fine — the point is having the harness and the number.
- Wire LangSmith tracing in from early so you can inspect *why* a run failed (which tool call went wrong, where the plan node made a bad call). This is the AI-engineering equivalent of the observability work already on your resume.

### 6.3 The payoff

You get a resume line like "correctly diagnosed N of M injected failure scenarios, averaging K tool calls per diagnosis" instead of a vague claim — and a genuinely strong interview thread about how you evaluated a non-deterministic system.

---

## 7. Failure scenario catalog

Your original list is good. Organize each as: manifest + golden label + expected evidence trail. Target ~8–10:

1. **CrashLoopBackOff** — bad command / failing entrypoint. Evidence: restart count, previous-container logs.
2. **ImagePullBackOff** — bad image tag / registry auth. Evidence: events, pod status reason.
3. **OOMKilled** — memory limit too low. Evidence: container last-state, node pressure.
4. **Readiness probe failure** — probe misconfigured / app slow to start. Evidence: describe conditions, endpoints not ready.
5. **DNS resolution failure** — service discovery broken. Evidence: logs, CoreDNS state.
6. **Missing/mis-referenced Secret** — pod can't mount/read secret. Evidence: events, describe.
7. **NetworkPolicy blocking traffic** — connectivity denied. Evidence: policy inspection, endpoint reachability.
8. **ConfigMap mismatch** — missing key / wrong mount path. Evidence: describe, logs.

Each scenario doubles as an eval case (Section 6) and a demo case (Section 9). Build them once, use them three ways.

---

## 8. Revised 8-week roadmap

Changes from your original: architecture doc is a Week 1 deliverable; React chat UI stays in Week 1 (you can stand up a streaming interface fast); eval harness lands mid-project as a first-class milestone, not a footnote; failure scenarios move earlier so they're available to the eval harness.

**Week 1 — Foundation + architecture doc**
- Write `docs/architecture.md`: the state graph, tool boundaries, LangChain/LangGraph split, safety model. (Doing this now clarifies your thinking and is interview-ready even if the project stalls.)
- FastAPI backend skeleton + streaming endpoint.
- Minimal React chat UI wired to the stream.
- Single LLM call round-trips end to end (no tools yet).

**Week 2 — Read-only tool layer**
- Implement the tool catalog (Section 4) against a Kind cluster.
- Normalization layer: structured output, not raw dumps.
- Get basic tool calling working (planner can invoke one tool and see structured results).

**Week 3 — RAG**
- Build the K8s/Helm/kubectl doc corpus, embed into Qdrant.
- Expose retrieval as a planner-callable tool.

**Week 4 — LangGraph agent loop**
- Refactor into the full state graph (Section 3): intake → gather_context → plan/execute loop → diagnose → recommend.
- Add the loop guard.

**Week 5 — Failure scenarios + eval harness**
- Author the 8–10 scenario manifests with golden labels (Section 7).
- Build the eval harness (Section 6). Get your first real numbers.
- Iterate on prompts/tool design using eval feedback — this is where the agent actually gets good.

**Week 6 — Memory + UI polish**
- Add checkpointed conversational memory for follow-ups.
- Upgrade the UI to show the investigation trail: tools used, evidence gathered, diagnosis, suggested fix.

**Week 7 — Production hardening**
- Tests (unit tests on the deterministic tool/normalization layer are easy wins and demonstrate rigor).
- Structured logging, Dockerize, LangSmith tracing in production mode.
- Basic auth if you're deploying it publicly.

**Week 8 — MCP + docs + demo**
- Build the MCP server exposing the read-only K8s operations (your highest-value stretch goal — promote it into the core).
- Finalize README, architecture doc, and a recorded demo walking through 2–3 failure scenarios.

---

## 9. Repository structure

Your proposed structure is good; here it is with the eval and docs additions:

```text
backend/
  api/          # FastAPI, streaming endpoint
  agent/        # graph assembly, node functions
  graph/        # state definition, edges, loop control
  tools/        # read-only kubectl tools + normalization
  rag/          # corpus loading, embedding, retriever
  prompts/      # versioned prompt templates
  models/       # model config, tier routing
eval/           # scenarios, golden labels, harness, scorers   ← added
frontend/src/
infra/
  docker/
  kubernetes/   # scenario manifests live here too
tests/
docs/
  architecture.md   # write this Week 1                        ← added
demo/
```

---

## 10. Scope guardrails

You're doing this alongside a full-time job, the rack-server project, a course, and a job search. Protect the finish line.

**Must ship (the project is incomplete without these):**
- LangGraph agent loop with read-only tools
- At least 6 working failure scenarios
- The eval harness + real numbers
- README + architecture doc

**Ship if time allows:**
- Polished investigation-trail UI
- MCP server (high value — try hard to keep it)
- Conversational memory

**Cut first if time gets tight:**
- Slack bot integration
- Grafana metrics integration
- Alertmanager-triggered investigations
- GitHub deployment correlation
- Public auth/deployment (a local demo + recording is enough)

A finished project with 6 scenarios, an eval harness, and an MCP server beats an unfinished one with a Slack bot and no eval, every time.

---

## 11. Resume framing (fill in after eval)

Draft line for the AI/Agentic Projects section, with placeholders for your real numbers:

> **Kubernetes Troubleshooting Agent** — Built a LangGraph agent that diagnoses Kubernetes failures using read-only tool calling and RAG over official docs. Separated deterministic fact-gathering from LLM-driven root-cause reasoning; enforced a read-only safety boundary at the tool layer. Evaluated against [N] injected failure scenarios (CrashLoopBackOff, OOMKilled, NetworkPolicy, etc.), correctly diagnosing [X/N] at an average of [K] tool calls per diagnosis. Exposed cluster operations via a custom MCP server. Stack: Python, LangGraph, FastAPI, Qdrant, Docker, Kind, LangSmith.

---

## 12. Interview talking points to prepare

Have crisp answers ready for these — they're what the project is *for*:

- **Why LangGraph over a simple loop?** Explicit state, checkpointing, a bounded plan/execute cycle, and legible traces vs. an ad-hoc loop that's hard to debug.
- **Why separate fact-gathering from judgment?** Deterministic normalized data reduces the model's error surface and token cost; judgment stays where it's actually needed. (Tie this to the same principle in your work project.)
- **How did you evaluate a non-deterministic system?** Golden-labeled injected scenarios, diagnosis-correctness scoring, tool-efficiency tracking, LangSmith traces for failure analysis.
- **How did you keep it safe against a real cluster?** Read-only tools by construction, suggest-don't-apply fixes, enforced at the tool layer.
- **What would you do differently at production scale?** (Have a thoughtful answer — e.g. RBAC-scoped service accounts, per-namespace isolation, rate limiting, human approval gates for any future write actions.)
