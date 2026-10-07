# Eval harness

Turns "the agent works" into a number (`docs/architecture.md` §9, plan
§6). Twenty injected failure scenarios plus one healthy control, each with a golden label stating
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

## Scenarios built to fail

The first thirteen scenarios all passed, which says more about the suite
than the agent. A suite where nothing fails can't show where the agent
stops working. So the last seven each target one known limit, and their
`notes` name which one:

| Limit | Scenarios | What's being measured |
|---|---|---|
| Reasoning trap | `leading`, `slowstart`, `redherring` | Evidence is reachable, but the obvious reading is wrong. `leading` is an A/B against `networkpolicy` with the user asserting "it's DNS". |
| Truncated default | `logtail`, `eventflood` | The answer is outside the 100-line log tail or the sweep's 20-newest events. The planner can widen either; does it? |
| Capability gap | `targetport` | No tool returns container ports, so the container's real port is invisible. Measures honesty, not skill. Pod `describe` gained `config_references` in Week 6, but ports were left out on purpose to keep this case. |
| Step budget | `needle` | 16 identical pods, one bad log stream, 8-call loop guard. Expected to fail; the question is whether the diagnosis says what it didn't check. |

**Measured (2026-10-03/04).** Results vary from run to run, so a
single run is not a score:

| Run | Scope | Score | Failed | Note |
|---|---|---|---|---|
| A | built-to-fail 7 | 5/7 | `logtail` (crashed), `needle` | `.content` bug, below. A `logtail` re-run alone was also wrong |
| B | full suite | 19/21 | `logtail`, `needle` | |
| C | full suite | 17/21 | `logtail`, `needle`, `crossns`, `targetport` | last run before the namespace fix |
| D | full suite | 19/21 | `logtail`, `needle` | after the namespace fix |

Most predicted failures didn't happen. The reasoning tier sees through
one-step traps: it rejected the user's DNS guess, read the previous
container's logs on `slowstart`, and recovered `eventflood`'s buried
event by filtering events to the pod. What does fail:

- `logtail` — wrong in every run, and it has never widened the log tail.
  Instead it makes up an explanation ("a busybox stub serving canned
  data", "an orphan pod missing a CronJob", "rates hardcoded in the
  busybox script"), usually at low confidence and once at medium. This is
  the failure that matters most: a made-up answer rather than an honest
  "I couldn't find it".
- `needle` — wrong in every run at **low** confidence, listing which
  shards it never checked, so it fails honestly. It's a pure step-budget
  failure: the planner reads shard-0..7 in order and hits the guard.
- `targetport` — failed once (run C) on a scorer disagreement. The signal
  check passed because it named the port mismatch, but the judge failed it
  for asserting at medium confidence that nothing in the container listens
  at all, a claim no tool can support. In run D it passed at low
  confidence. It's on the line between the two, so read it over several
  runs.
- `crossns` — failed once (run C) by hitting the loop guard. See the
  namespace bug below.

### Defects eval caught

- **`recommend` crash (run A).** `recommend` returned `response.content`,
  which is a list of blocks rather than a string when the model emits a
  thinking block. Fixed (`.text`), in the API streaming path too.
- **Omitted namespace misrouted tool calls (runs A-C).** Every
  namespaced tool takes `namespace` as optional, and an omitted one fell
  back to the backend's configured default (`default`) rather than the
  namespace the user asked about. The call didn't fail. It returned a 404
  or an empty list from the wrong namespace, which the planner read as
  evidence. In run C's `crossns`, 4 of 8 calls went to `default` (one
  sent `namespace: null` explicitly); three came back "not found" and
  backed the wrong conclusion ("the `stock` Service doesn't exist"),
  and the run hit the loop guard. `execute_tool` now
  fills in the scope's namespace when the planner leaves it out (an
  explicit one still wins, which cross-namespace lookups need), and logs
  the args actually used so the planner can see where it looked. The
  omission is a regular model habit, not a rare one: it appeared in runs
  A (`needle`, 3 calls), B (`noisy`, 1) and C (`crossns`, 4), in all 3
  standalone `crossns` re-runs after the fix (all passed), and 3 times in
  run D. `namespace_filled` on each tool call and the "namespace omitted by
  planner Nx" summary line keep it measured. Caveat: `crossns` also passed
  in run B without the fix, so these runs show the fix stops wrong-namespace
  results from misleading the planner, not that it alone decides the
  outcome.

- **Judge output failed validation (2026-10-05).** The judge used
  `with_structured_output`'s default `function_calling` method, which
  forces a tool call and validates afterwards. In 2 of 20 calls the model
  leaked tool-call markup (`</parameter></invoke>`) into a string field.
  Once that swallowed the `reasoning` field, `JudgeVerdict` raised, and
  `networkpolicy` went unscored, reported as an *agent* error. The judge
  now uses `method="json_schema"` (constrained decoding via
  `output_config.format`, so the schema is guaranteed), retries once on a
  malformed verdict, then falls back to the signal check and reports
  "judge failed Nx". A scoring exception is now `scoring_failed`, not
  `agent_failed`. Re-scored, that diagnosis was correct.

## RAG: what eval showed it is (and isn't) for

**Finding.** In every saved eval run before 2026-10-06 (14 local runs
from August plus three full-suite runs on 2026-10-05, the last at 19/21
correct), the planner called `search_k8s_docs` **zero times**. Two causes:

- The evidence settles the diagnosis. Every scenario is solved from
  cluster state, and the planner is told to stop once the evidence is
  clear, which is before docs would add anything.
- The corpus overlaps with the model's knowledge. Official Kubernetes
  docs are heavily represented in training data; looking up what
  CrashLoopBackOff means returns what the model already knows. RAG pays
  off on knowledge the model can't have (post-cutoff changes, internal
  runbooks), and a public-docs corpus has little of that.

So RAG doesn't improve diagnosis here, and the suite can't claim it does.

**What it does now.** `docs/architecture.md` §8 always said RAG's job was
grounding the *fix*. Retrieval now runs deterministically in a `ground`
node after `diagnose` (query: the diagnosed root cause), and
`recommend_v2` cites what comes back, only where a page supports a
specific part of the fix. Since `ground` runs after `diagnose`, it can't
change diagnosis accuracy by construction.

**Measured (2026-10-06, all 21 scenarios; 17 in one run, plus 4 re-run
after an API billing error interrupted them):**

| | Before (`recommend_v1`, no docs) | After (`ground` + `recommend_v2`) |
|---|---|---|
| Diagnosis correct | 19/21 | 19/21 (`logtail`, `needle`, as before) |
| Remediation appropriate | 19/21 | 19/21 |
| Recommendations citing a doc | 0 | 21/21 (1-3 pages each) |
| Citations not in the corpus (made up) | n/a | 0 of 41 |
| Citations from the pages actually retrieved | n/a | 41/41 |
| Docs retrieval failures | n/a | 0 |

The model is selective: it cites 1 of the 3 retrieved pages for
`readiness` and `noisy`, not everything it's given. One known gap: the
validity check confirms a URL is a real corpus page, not that the link
text describes it. In a smoke run, `selector` labelled the Debug Pods URL
"Kubernetes service debugging guide". Checking that would need the judge.

`--no-rag` turns off both `ground` and the planner's docs tool, for a
same-prompt comparison.

## Multi-turn follow-ups

Week 6 added checkpointed conversation memory, and with it a routing
decision `intake` can get wrong: answer a follow-up from the evidence
earlier turns gathered (`answer_followup`, no tools), or investigate
again. A scenario can carry `follow_ups`, which run on the same thread
after the first turn, before teardown so a "is it fixed yet?" question
still sees the broken cluster. Each is scored deterministically on two
things: whether it took the expected route, and whether the reply
carries its `required_signals`. Routing right but answering from evidence
that never had the answer still fails.

| Scenario | Follow-up | Expected route | Why |
|---|---|---|---|
| `secret` | "Which key in that Secret was the pod trying to read?" | followup | The answer should already be in turn 1's evidence. |
| `secret` | "I think I've fixed it now. Is the pod running?" | investigate | Current state; nothing was fixed, so a stale answer is wrong. |
| `selector` | "…how can I confirm traffic is actually reaching the pods?" | followup | Explanatory; the diagnosis already names empty endpoints. |
| `distractor` | "What about the report-generator pod?" | investigate | The second real failure; only its logs say why, and turn 1 had no reason to read them. |

**Measured (2026-10-06).** Baseline 3/4, routing 4/4. The miss was the
first `secret` follow-up: routed correctly, and the reply honestly said
the evidence didn't name the key. It didn't, because `describe_resource`
on a pod returned no env references: the Failed event names the missing
Secret but not the key. That was a tool gap rather than a memory bug.
Pod describe now returns `config_references` (Secret/ConfigMap names and
keys, never values). Re-run: 4/4. The fix also stopped turn-1
recommendations guessing the key (it had suggested `username`/`password`).

The full-suite regression after `intake_v3` (the prompt that adds the
follow-up decision) scored 18/21 with zero runs ended at intake:
`logtail` and `needle` as always, plus `targetport`, its known borderline
case (above).

## Running it

Needs a reachable Kind cluster and `kubectl` on PATH.

```bash
cd backend
uv run python -m eval                  # all scenarios
uv run python -m eval -s dns -s secret # just these
uv run python -m eval --list           # what's available
uv run python -m eval --no-judge       # deterministic scoring only, no LLM judge
uv run python -m eval --keep           # leave namespaces up for inspection
uv run python -m eval --no-followups   # first turn only
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
4. **Follow up**, if the scenario has `follow_ups`: later turns on the
   same conversation thread (above).
5. **Score** the diagnosis against the golden label (below).
6. **Tear down** the namespace.

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
- **LLM judge** (reasoning tier, `prompts/eval_judge_v2.md`) — compares
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
