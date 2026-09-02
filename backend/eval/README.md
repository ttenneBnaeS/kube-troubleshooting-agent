# Eval harness

Turns "the agent works" into a number (`docs/architecture.md` §9, plan
§6). Thirteen injected failure scenarios, each with a golden label stating
the true root cause; the harness breaks a cluster in a known way, runs the
agent against it, and scores the diagnosis.

## Difficulty tiers

Scenarios carry an `easy`/`medium`/`hard` tier and results are reported
per tier, because a single blended accuracy number stops being
interpretable once the suite mixes trivial and adversarial cases — 11/14
doesn't say which kind failed.

The **easy** tier is a regression guard, and what it actually watches for
is tool-call *inflation* rather than accuracy: it should sit at 100%
forever, so the signal is a scenario that used to resolve in one call
starting to take six. That's the failure mode a hard-only suite is blind
to, and it's the reason the easy scenarios are kept rather than replaced
as the suite grows.

The **hard** tier is where there's room to improve, and where the headline
number comes from. Several hard scenarios probe the architecture rather
than Kubernetes:

- `crossns` — the cause is in another namespace. `gather_context` sweeps
  only `scope.namespace`, so the initial sweep is entirely clean and the
  only breadcrumb is a fully-qualified hostname in a log line.
- `noisy` — 13 pods, all healthy by every status field, one bad log
  stream. Probes noise handling and the prompt growth in
  `_investigation_summary`, which re-serializes the full snapshot plus
  every prior tool result on each planning round.
- `distractor` — a deliberate A/B against `selector`: identical root
  cause plus a loud, genuinely-broken, irrelevant pod. Failing this while
  `selector` passes would mean salience, not capability.

## Running it

Needs a reachable Kind cluster and `kubectl` on PATH.

```bash
cd backend
uv run python -m eval                  # all 8 scenarios
uv run python -m eval -s dns -s secret # just these
uv run python -m eval --list           # what's available
uv run python -m eval --no-judge       # deterministic scoring only, no LLM judge
uv run python -m eval --keep           # leave namespaces up for inspection
```

A full run takes roughly 5-8 minutes: most of it is waiting for scenarios
to actually break (image pulls, restart backoff), not agent latency.

## Why it lives in `backend/`

The plan's repo diagram puts `eval/` at the root. It's here instead
because backend modules are top-level (`graph`, `tools`, `models` — there
is no `backend` package prefix), so a root-level `eval/` can't import the
graph without `sys.path` surgery. Under `backend/` it runs in the same uv
environment as the code it tests. The scenario *manifests* stay in
`infra/kubernetes/` as planned.

## How a run works

Per scenario, sequentially:

1. **Create** a throwaway namespace `eval-<id>` and apply the manifest
   into it (`cluster.py`). Manifests carry no `namespace:` field, so the
   same file serves both the eval run and the demo copies in `default`.
2. **Wait** until the failure is genuinely observable — the right
   container reason, a pod stuck not-Ready, or an expected line in the
   logs. Skipping this would grade the agent on a pod that is still
   pulling its image.
3. **Run** the agent graph against a natural-language request that names
   the namespace and the symptom, but never the cause.
4. **Score** the diagnosis against the golden label (below).
5. **Tear down** the namespace.

Runs are sequential on purpose: scenarios contend for node memory (the OOM
case especially), and the RAG docs tool sits behind Voyage's free-tier
rate limits. Parallelism would produce flake that looks like agent error.

The harness mutates the cluster; the agent can't. That asymmetry is the
point — the read-only boundary (`docs/architecture.md` §5) constrains the
agent under test, not its test rig.

## Scoring

Two scorers run on every scenario:

- **Signal check** (deterministic) — the golden label's `required_signals`
  are an AND of ORs; each group must be hit by one of its synonyms.
  Matching is word-boundary aware, so "payments" doesn't match inside
  "payments-api". Reproducible and free, and it names *which* part of the
  expected answer was missing.
- **LLM judge** (reasoning tier, `prompts/eval_judge_v1.md`) — compares
  the diagnosis to the ground truth and owns the verdict, because keyword
  matching can't tell a paraphrase from a miss.

`forbidden_terms` are **advisory only** and never flip a verdict. A real
run demonstrated why: on the DNS scenario the agent correctly wrote "this
is *not* a NetworkPolicy issue — no NetworkPolicies are present in the
namespace," having called `get_network_policies` to rule it out. A naive
substring check scores that as a wrong-cause claim. The judge doesn't.

Disagreements between the two scorers are counted and printed rather than
smoothed over — a disagreement means either the synonym list is too narrow
or the judge is being generous, and both are worth knowing.

Also tracked, but not scored: planner tool calls per diagnosis (catches
thrashing), whether the loop guard fired, stated confidence, and which of
the golden label's expected evidence tools were actually used. The initial
sweep is excluded from the tool-call count — it's a fixed node, not a
planner decision — but it *is* credited as having gathered pod status and
events, since `gather_context` calls exactly those two functions.

## Results

Every run writes a full JSON record to `results/` (gitignored): every tool
call with its arguments, the diagnosis, the recommendation, and the
judge's reasoning. The console prints a summary table and the headline
line.

## LangSmith tracing

Optional and env-gated. Set in `backend/.env`:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=kube-troubleshooting-agent-eval
```

`tracing.py` loads `.env` into the process environment before anything
imports LangChain (pydantic-settings doesn't export to `os.environ`, and
LangChain reads it directly). Each run is tagged `eval` and
`scenario:<id>` so a failure is findable per-node. With the vars unset,
runs proceed untraced and the JSON records remain the log.

## Adding a scenario

1. Write the manifest in `infra/kubernetes/`, with no `namespace:` field.
2. Add a `Scenario` to `scenarios.py`: the manifest, a `user_request` that
   describes the symptom without naming the cause, a `ready_when`
   predicate, and a `GoldenLabel`.
3. Confirm the failure is diagnosable with the tools the agent actually
   has. The NetworkPolicy scenario needed `get_network_policies` added to
   the catalog first — without it that scenario is unsolvable by
   construction, not hard.
