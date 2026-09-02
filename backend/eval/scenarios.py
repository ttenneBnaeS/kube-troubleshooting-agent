"""The scenario registry: manifests, golden labels, and readiness predicates.

Each entry ties together the three things an eval case needs (plan §6.1):
the manifest that injects a known failure, the golden label stating the
true root cause, and a predicate telling the harness when the scenario has
actually reached its failure state — applying a manifest and immediately
asking the agent about it would grade the agent on a cluster that hasn't
broken yet.

Manifests live in `infra/kubernetes/` and carry no `namespace:` field, so
the harness can apply the same file into a throwaway namespace per run
(see `cluster.py`) while the copies in `default` stay put for demos.
Scenarios that would make a poor demo — `noisy` puts 13 pods in a
namespace, `crossns` only means anything across two — live in
`infra/kubernetes/eval-only/`, which `kubectl apply -f infra/kubernetes/`
skips because it doesn't recurse.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# backend/eval/scenarios.py -> backend/eval -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "infra" / "kubernetes"


@dataclass(frozen=True)
class ReadyWhen:
    """When a scenario has finished breaking and is ready to be diagnosed.

    A scenario may carry several of these (see `Scenario.ready_when`), in
    which case every one must hold — a scenario with two independent
    failures isn't set up until both have actually appeared.
    """

    kind: Literal[
        "container_reason",
        "pod_not_ready",
        "pod_ready",
        "log_contains",
        "service_no_endpoints",
        "service_has_endpoints",
    ]
    # Deployment-managed pods get a hash suffix, so match on prefix.
    # Unused by `service_no_endpoints`, which keys off `service_name`.
    pod_prefix: str = ""
    reasons: tuple[str, ...] = ()
    log_substring: str | None = None
    min_restarts: int = 0
    service_name: str | None = None
    # Grace period after the predicate first passes, so events accumulate
    # and the agent sees the same steady state a human would.
    settle_seconds: int = 5


# Difficulty is reported as a tier rather than folded into one accuracy
# number. The easy tier is a regression guard — it should sit at 100%
# forever, and what it actually watches for is tool-call *inflation*, the
# failure mode a hard-only suite is blind to. The hard tier is where the
# agent has room to improve and where the headline number comes from.
Difficulty = Literal["easy", "medium", "hard"]


@dataclass(frozen=True)
class GoldenLabel:
    """Ground truth for one scenario.

    `required_signals` is an AND of ORs: every group must be hit by at
    least one of its synonyms for the deterministic pre-check to pass. It
    exists to explain *which* part of the diagnosis was missing, not to be
    the final word — the LLM judge owns correctness, because keyword
    matching can't tell a paraphrase from a miss.

    `forbidden_terms` are advisory for the same reason in reverse: an
    agent that correctly writes "this is not node memory pressure" would
    trip a naive substring check, so a hit is surfaced as a warning in the
    report and never flips the verdict on its own.
    """

    root_cause: str
    remediation: str
    remediation_category: str
    required_signals: tuple[tuple[str, ...], ...]
    forbidden_terms: tuple[str, ...] = ()
    expected_evidence_tools: tuple[str, ...] = ()
    # True when the correct answer is that nothing in scope is broken.
    # Every other scenario has a findable cause, which means an agent that
    # always confidently names *something* scores 100% — this is the only
    # case that can detect confabulation. The judge is told explicitly, so
    # it grades "declined to name a cause" as success rather than as a
    # non-answer.
    no_fault: bool = False


@dataclass(frozen=True)
class Scenario:
    id: str
    manifest_files: tuple[str, ...]
    # `{namespace}` is substituted at run time.
    user_request: str
    # One predicate, or several that must all hold before the agent runs.
    ready_when: ReadyWhen | tuple[ReadyWhen, ...]
    golden: GoldenLabel
    difficulty: Difficulty = "medium"
    notes: str = ""

    # A second namespace, created and destroyed alongside the scenario's
    # own. Needed for failures whose cause lives outside the namespace the
    # user is asking about — the name is fixed rather than derived because
    # the client manifest has to hard-code the FQDN that points at it.
    aux_namespace: str | None = None
    aux_manifest_files: tuple[str, ...] = ()

    @property
    def ready_predicates(self) -> tuple[ReadyWhen, ...]:
        return self.ready_when if isinstance(self.ready_when, tuple) else (self.ready_when,)

    def aux_manifest_paths(self) -> list[Path]:
        return [MANIFEST_DIR / name for name in self.aux_manifest_files]

    @property
    def namespace(self) -> str:
        return f"eval-{self.id}"

    def manifest_paths(self) -> list[Path]:
        return [MANIFEST_DIR / name for name in self.manifest_files]

    def request_for(self, namespace: str) -> str:
        return self.user_request.format(namespace=namespace)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="crashloop",
        manifest_files=("crashloop-demo.yaml",),
        user_request=(
            "Something's wrong in the `{namespace}` namespace — a pod keeps "
            "restarting and won't stay up. What's going on, and how do I fix it?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="crashloop-demo",
            reasons=("CrashLoopBackOff", "Error"),
            min_restarts=1,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The container's command (`sh -c 'echo booting; sleep 2; exit 1'`) "
                "exits with status 1 a couple of seconds after starting. Because the "
                "pod's restartPolicy is Always, the kubelet restarts it, it exits "
                "again, and the pod settles into CrashLoopBackOff. The image pulls "
                "fine and there is no resource or configuration problem — the "
                "workload itself is deliberately failing."
            ),
            remediation=(
                "Fix the container's command/entrypoint so it runs a long-lived "
                "process instead of exiting non-zero; inspect the previous "
                "container's logs to confirm what it did before dying."
            ),
            remediation_category="fix-container-command",
            required_signals=(
                ("crashloopbackoff", "crash loop", "crashloop", "restart loop"),
                ("exit 1", "exit code 1", "exits", "exiting", "non-zero", "status 1", "command", "entrypoint"),
            ),
            forbidden_terms=("imagepullbackoff", "oomkilled", "readiness probe", "networkpolicy"),
            expected_evidence_tools=("get_pod_status_tool", "get_container_logs_tool", "get_recent_events_tool"),
        ),
        difficulty="easy",
        notes="Single-hop: visible from pod status plus previous-container logs.",
    ),
    Scenario(
        id="imagepull",
        manifest_files=("imagepull-demo.yaml",),
        user_request=(
            "A pod in namespace `{namespace}` is stuck and never starts. "
            "What's wrong and how do I fix it?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="imagepull-demo",
            reasons=("ImagePullBackOff", "ErrImagePull"),
        ),
        golden=GoldenLabel(
            root_cause=(
                "The pod references the image "
                "`does-not-exist/definitely-not-a-real-image:v1`, which does not "
                "exist in any reachable registry. The kubelet cannot pull it, so the "
                "container never starts and the pod sits in ImagePullBackOff."
            ),
            remediation=(
                "Correct the image reference to a real repository/tag (and add "
                "imagePullSecrets if the registry is private), then re-apply."
            ),
            remediation_category="fix-image-reference",
            required_signals=(
                ("imagepullbackoff", "errimagepull", "image pull", "pull the image", "pulling the image"),
                ("does not exist", "doesn't exist", "not found", "invalid", "no such", "nonexistent", "unreachable"),
            ),
            forbidden_terms=("crashloopbackoff", "oomkilled", "readiness probe", "networkpolicy"),
            expected_evidence_tools=("get_pod_status_tool", "get_recent_events_tool"),
        ),
        difficulty="easy",
        notes="Single-hop: pod status reason plus the Failed event names the image.",
    ),
    Scenario(
        id="oomkilled",
        manifest_files=("oomkilled-demo.yaml",),
        user_request=(
            "A pod in namespace `{namespace}` keeps getting killed and restarted. "
            "What's the root cause?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="oomkilled-demo",
            reasons=("OOMKilled", "CrashLoopBackOff"),
            min_restarts=2,
            settle_seconds=10,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The container runs `stress --vm 1 --vm-bytes 150M` but its memory "
                "limit is 50Mi, so it allocates roughly three times what it is "
                "allowed and the kernel OOM-killer terminates it (last state "
                "terminated with reason OOMKilled, exit code 137). This is a "
                "container-level limit that is too low for the workload, not "
                "node-level memory pressure — the node reports no MemoryPressure "
                "condition."
            ),
            remediation=(
                "Raise the container's memory limit/request above its real working "
                "set (or reduce what the workload allocates). Node capacity is not "
                "the constraint."
            ),
            remediation_category="raise-memory-limit",
            required_signals=(
                ("oomkilled", "out of memory", "oom-killer", "oom killer", "oom"),
                ("memory limit", "50mi", "limit is too low", "exceeds", "150m", "resource limit"),
            ),
            forbidden_terms=("imagepullbackoff", "readiness probe", "networkpolicy", "bad address"),
            expected_evidence_tools=(
                "get_pod_status_tool",
                "get_recent_events_tool",
                "get_node_status_tool",
                "describe_resource_tool",
            ),
        ),
        notes="Multi-hop: distinguishing container limit from node pressure needs events + node status.",
        difficulty="medium",
    ),
    Scenario(
        id="readiness",
        manifest_files=("readiness-demo.yaml",),
        user_request=(
            "The readiness-demo pod in namespace `{namespace}` is Running but never "
            "becomes Ready, and traffic isn't reaching it. Why?"
        ),
        ready_when=ReadyWhen(
            kind="pod_not_ready",
            pod_prefix="readiness-demo",
            settle_seconds=15,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The Deployment's readinessProbe does an HTTP GET against "
                "`/this-path-does-not-exist` on port 80, which nginx answers with "
                "404. A non-2xx/3xx response fails the probe, so the container runs "
                "healthily but never passes readiness, the pod stays 0/1 Ready, and "
                "the Service drops it from its endpoints — leaving the Service with "
                "no ready addresses. The application itself is fine; the probe is "
                "misconfigured."
            ),
            remediation=(
                "Point the readiness probe at a path the app actually serves (e.g. "
                "`/`), or add the health endpoint the probe expects."
            ),
            remediation_category="fix-readiness-probe-path",
            required_signals=(
                ("readiness probe", "readinessprobe", "readiness check"),
                ("404", "path", "this-path-does-not-exist", "wrong path", "misconfigured", "does not exist"),
            ),
            forbidden_terms=("crashloopbackoff", "oomkilled", "imagepullbackoff"),
            expected_evidence_tools=(
                "get_pod_status_tool",
                "get_recent_events_tool",
                "get_service_endpoints_tool",
                "describe_resource_tool",
            ),
        ),
        # Originally written as a multi-hop case, and measurement said
        # otherwise: the initial sweep already returns every event in the
        # namespace, including "Readiness probe failed: HTTP probe failed
        # with statuscode: 404", which names the cause outright. It
        # resolves in zero planner tool calls.
        notes="Sweep-resolvable: the probe-failure event names the cause, so no planner tool call is needed.",
        difficulty="easy",
    ),
    Scenario(
        id="secret",
        manifest_files=("secret-demo.yaml",),
        user_request=(
            "A pod in namespace `{namespace}` won't start — it never gets to "
            "Running. What's the problem?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="secret-demo",
            reasons=("CreateContainerConfigError",),
        ),
        golden=GoldenLabel(
            root_cause=(
                "The pod sets the env var DB_PASSWORD from a secretKeyRef pointing "
                "at a Secret named `db-credentials`, which does not exist in the "
                "namespace. The kubelet cannot assemble the container's environment, "
                "so the container is never created and the pod stays in "
                "CreateContainerConfigError with zero restarts and no logs."
            ),
            remediation=(
                "Create the missing `db-credentials` Secret with a `password` key "
                "(or correct the secretKeyRef to name a Secret that exists)."
            ),
            remediation_category="create-missing-secret",
            required_signals=(
                ("secret", "secretkeyref"),
                ("db-credentials",),
                ("not found", "does not exist", "doesn't exist", "missing", "never created"),
            ),
            forbidden_terms=("oomkilled", "imagepullbackoff", "readiness probe", "networkpolicy"),
            expected_evidence_tools=("get_pod_status_tool", "get_recent_events_tool", "describe_resource_tool"),
        ),
        difficulty="easy",
        notes="Single-hop: the Failed event names the missing Secret directly.",
    ),
    Scenario(
        id="configmap",
        manifest_files=("configmap-demo.yaml",),
        user_request=(
            "A pod in namespace `{namespace}` won't start — it never gets to "
            "Running. What's the problem?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="configmap-demo",
            reasons=("CreateContainerConfigError",),
        ),
        golden=GoldenLabel(
            root_cause=(
                "The pod's env var LOG_LEVEL comes from a configMapKeyRef asking for "
                "the key `LOG_LEVEL` in ConfigMap `app-config`. The ConfigMap exists "
                "and is otherwise correct, but its keys are lowercase "
                "(`log_level`, `listen_port`, `upstream_url`) — there is no "
                "`LOG_LEVEL` key. ConfigMap keys are case-sensitive, so the lookup "
                "fails and the pod stays in CreateContainerConfigError. Unlike the "
                "missing-Secret case, the referenced object is present; only the key "
                "is wrong."
            ),
            remediation=(
                "Change the configMapKeyRef to `log_level` to match the key the "
                "ConfigMap actually defines (or add a `LOG_LEVEL` key to the "
                "ConfigMap)."
            ),
            remediation_category="fix-configmap-key-reference",
            required_signals=(
                ("configmap", "config map"),
                ("log_level", "key", "keys"),
                ("case", "mismatch", "lowercase", "uppercase", "not defined", "does not exist", "doesn't exist", "missing"),
            ),
            forbidden_terms=("oomkilled", "imagepullbackoff", "readiness probe", "networkpolicy"),
            expected_evidence_tools=("get_pod_status_tool", "get_recent_events_tool", "describe_resource_tool"),
        ),
        notes="Multi-hop: the fix depends on the ConfigMap's real keys, so it needs describe_resource(kind=configmap).",
        difficulty="medium",
    ),
    Scenario(
        id="dns",
        manifest_files=("dns-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, the dns-demo pod can't talk to its backend. "
            "Everything shows as Running. Can you work out why?"
        ),
        ready_when=ReadyWhen(
            kind="log_contains",
            pod_prefix="dns-demo",
            log_substring="bad address",
            settle_seconds=5,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The client is configured to call `http://payments-api:8080`, but no "
                "Service named `payments-api` exists in the namespace — the backend "
                "Service is named `payments`. The hostname fails DNS resolution "
                "(busybox reports `wget: bad address 'payments-api:8080'`), so no "
                "connection is ever attempted. Nothing is unhealthy: the client pod "
                "is Running with zero restarts, the payments Deployment is available, "
                "and its Service has ready endpoints. The only evidence is in the "
                "client's container logs."
            ),
            remediation=(
                "Point the client at the Service that actually exists — "
                "`http://payments:8080` — or rename/alias the Service to "
                "`payments-api` to match what the client expects."
            ),
            remediation_category="correct-service-hostname",
            required_signals=(
                ("dns", "resolve", "resolution", "bad address", "nxdomain", "hostname"),
                ("payments-api", "service name", "wrong name", "typo", "does not exist", "doesn't exist", "not found", "no service"),
            ),
            forbidden_terms=("networkpolicy", "network policy", "oomkilled", "crashloopbackoff", "readiness probe"),
            expected_evidence_tools=(
                "get_container_logs_tool",
                "get_pod_status_tool",
                "get_service_endpoints_tool",
            ),
        ),
        notes="Multi-hop and adversarial: pod status shows nothing wrong, so the planner must read logs anyway.",
        difficulty="hard",
    ),
    Scenario(
        id="networkpolicy",
        manifest_files=("networkpolicy-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, the orders-client pod can't reach the "
            "orders-api service, but everything looks healthy to me. What's wrong?"
        ),
        ready_when=ReadyWhen(
            kind="log_contains",
            pod_prefix="orders-client",
            log_substring="timed out",
            settle_seconds=5,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The NetworkPolicy `orders-api-allow-known-clients` selects pods "
                "labelled `app=orders-api` and admits ingress only from pods "
                "labelled `app=allowed-client`. The client pod is labelled "
                "`app=orders-client`, so it does not match the allowed peer and the "
                "CNI silently drops its packets — the request hangs and times out "
                "rather than being refused. Every other signal is healthy: both pods "
                "are Running and Ready, the Deployment is available, the Service has "
                "ready endpoints, and no Warning events are emitted, because a "
                "dropped packet produces no Kubernetes object state."
            ),
            remediation=(
                "Either add `app=orders-client` as an allowed peer in the "
                "NetworkPolicy's ingress rule, or relabel the client to "
                "`app=allowed-client` so it matches the existing policy."
            ),
            remediation_category="update-networkpolicy-or-client-labels",
            required_signals=(
                ("networkpolicy", "network policy"),
                ("orders-client", "allowed-client", "label", "selector"),
                ("block", "blocked", "denied", "deny", "drop", "dropped", "not allowed", "does not match", "doesn't match"),
            ),
            forbidden_terms=("dns", "bad address", "oomkilled", "crashloopbackoff", "readiness probe", "imagepullbackoff"),
            expected_evidence_tools=(
                "get_container_logs_tool",
                "get_service_endpoints_tool",
                "get_network_policies_tool",
            ),
        ),
        notes="Hardest scenario: no unhealthy object state anywhere; only get_network_policies explains it.",
        difficulty="hard",
    ),
    Scenario(
        id="selector",
        manifest_files=("selector-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, requests to the search-api service aren't "
            "getting through to anything. The pods look fine to me. What's wrong?"
        ),
        ready_when=(
            ReadyWhen(kind="pod_ready", pod_prefix="search-api"),
            ReadyWhen(kind="service_no_endpoints", service_name="search-api"),
        ),
        golden=GoldenLabel(
            root_cause=(
                "The `search-api` Service selects pods labelled "
                "`component=search,tier=frontend`, but the Deployment labels its "
                "pods `component=search,tier=api`. The `tier` value doesn't match, "
                "so the selector matches no pods, the Service's endpoint list is "
                "empty, and requests to it reach no backend. Everything else is "
                "healthy: both replicas are Running and Ready with zero restarts, "
                "the Deployment reports full availability, and no Warning event is "
                "emitted, because a selector matching nothing is an empty set "
                "rather than an error condition."
            ),
            remediation=(
                "Change the Service's selector to `tier: api` (keeping "
                "`component: search`) so it matches the labels the Deployment "
                "actually applies — or relabel the pods to `tier: frontend` if "
                "that was the intended state."
            ),
            remediation_category="fix-service-selector",
            required_signals=(
                ("selector", "label selector"),
                # `tier` is the discriminating term: it cannot be guessed from
                # the workload's name, so hitting it means the agent actually
                # read the labels rather than inferring them.
                ("tier", "frontend"),
                ("no endpoints", "empty", "matches no", "match no", "no pods", "mismatch", "does not match", "doesn't match"),
            ),
            forbidden_terms=("crashloopbackoff", "oomkilled", "imagepullbackoff", "readiness probe", "networkpolicy"),
            expected_evidence_tools=("get_service_endpoints_tool", "get_pod_status_tool"),
        ),
        difficulty="medium",
        notes="Quiet failure: nothing unhealthy. Needs the Service selector compared against pod labels.",
    ),
    Scenario(
        id="distractor",
        manifest_files=("distractor-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, the inventory service isn't serving any "
            "traffic. Can you work out why?"
        ),
        ready_when=(
            ReadyWhen(kind="pod_ready", pod_prefix="inventory"),
            ReadyWhen(kind="service_no_endpoints", service_name="inventory"),
            ReadyWhen(
                kind="container_reason",
                pod_prefix="report-generator",
                reasons=("CrashLoopBackOff", "Error"),
                min_restarts=2,
            ),
        ),
        golden=GoldenLabel(
            root_cause=(
                "The `inventory` Service selects `component=inventory,tier=api` "
                "while its Deployment labels pods "
                "`component=inventory,tier=backend`, so the selector matches "
                "nothing, the endpoint list is empty, and the Service serves no "
                "traffic. The `report-generator` pod in the same namespace is also "
                "genuinely broken (CrashLoopBackOff — its command exits 1 after "
                "logging a missing report template), but it is unrelated to the "
                "inventory Service: it is not a backend for it, shares no labels "
                "with it, and its failure does not affect it. Naming the crashloop "
                "as the reason inventory serves no traffic is incorrect."
            ),
            remediation=(
                "Change the inventory Service's selector to `tier: backend` "
                "(keeping `component: inventory`) so it matches the pods' labels. "
                "The report-generator crashloop is a separate issue worth fixing "
                "on its own, but it is not the cause of the reported symptom."
            ),
            remediation_category="fix-service-selector",
            required_signals=(
                ("selector", "label selector"),
                # As in `selector`: `tier` is unguessable from the name, so it
                # is the signal that distinguishes reading from inferring.
                ("tier", "backend"),
                ("no endpoints", "empty", "matches no", "match no", "no pods", "mismatch", "does not match", "doesn't match"),
            ),
            # Advisory as always, but pointed: these are the terms a
            # diagnosis anchored on the distractor would reach for.
            forbidden_terms=("report-generator", "crashloopbackoff", "report template"),
            expected_evidence_tools=("get_service_endpoints_tool", "get_pod_status_tool"),
        ),
        difficulty="hard",
        notes=(
            "Anchoring A/B against `selector`: identical root cause plus a loud, "
            "genuinely-broken but irrelevant crashlooping pod. A failure here with "
            "`selector` passing means salience, not capability."
        ),
    ),
    Scenario(
        id="initcontainer",
        manifest_files=("initcontainer-demo.yaml",),
        user_request=(
            "A pod in namespace `{namespace}` never finishes starting up. "
            "What's blocking it?"
        ),
        ready_when=ReadyWhen(
            kind="container_reason",
            pod_prefix="migrate-demo",
            reasons=("CrashLoopBackOff", "Error"),
            min_restarts=1,
        ),
        golden=GoldenLabel(
            root_cause=(
                "The pod's init container `db-migrate` fails: it tries to run a "
                "schema migration against host `db` on port 5432, can't reach it, "
                "logs 'host unreachable' and exits 1. Init containers must complete "
                "successfully before app containers start, so the kubelet keeps "
                "restarting it and the pod never leaves Init — the `app` container "
                "reports waiting/PodInitializing, which is a consequence rather "
                "than the cause."
            ),
            remediation=(
                "Fix what the init container depends on — make the `db` host "
                "resolvable and reachable on 5432 (or correct the host/port the "
                "migration targets). PodInitializing will clear once the init "
                "container exits 0."
            ),
            remediation_category="fix-init-container-dependency",
            required_signals=(
                ("init container", "initcontainer", "db-migrate", "init"),
                ("db", "database", "5432", "unreachable", "connect"),
            ),
            # PodInitializing is the symptom the old tool layer left the
            # agent stranded on; reporting it as the cause is the failure.
            forbidden_terms=("imagepullbackoff", "oomkilled", "readiness probe"),
            expected_evidence_tools=("get_pod_status_tool", "get_container_logs_tool", "describe_resource_tool"),
        ),
        difficulty="medium",
        notes="Validates init-container visibility: the app container only says PodInitializing.",
    ),
    Scenario(
        id="crossns",
        manifest_files=("eval-only/crossns-demo.yaml",),
        aux_namespace="warehouse-demo",
        aux_manifest_files=("eval-only/crossns-backend-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, the fulfillment pod can't reach the stock "
            "service it depends on. Everything in the namespace looks fine. Why?"
        ),
        ready_when=ReadyWhen(
            kind="log_contains",
            pod_prefix="fulfillment",
            log_substring="timed out",
        ),
        golden=GoldenLabel(
            root_cause=(
                "The cause is in a different namespace than the one being asked "
                "about. The client calls "
                "`stock-api.warehouse-demo.svc.cluster.local`, and the "
                "`stock-api-internal-only` NetworkPolicy in the `warehouse-demo` "
                "namespace admits ingress only from pods in namespaces labelled "
                "`access=warehouse`. The calling namespace does not carry that "
                "label, so its packets are dropped and the request times out. "
                "Nothing in the calling namespace is unhealthy — one pod, Running, "
                "zero restarts, no Warning events — and the backend itself is fine."
            ),
            remediation=(
                "Either label the calling namespace `access=warehouse` so it "
                "matches the policy's namespaceSelector, or widen the policy to "
                "admit the calling namespace explicitly."
            ),
            remediation_category="allow-cross-namespace-ingress",
            required_signals=(
                ("networkpolicy", "network policy"),
                ("warehouse-demo", "namespace"),
                ("namespaceselector", "namespace selector", "access=warehouse", "label", "labelled", "labeled"),
            ),
            forbidden_terms=("oomkilled", "crashloopbackoff", "readiness probe", "imagepullbackoff"),
            expected_evidence_tools=("get_container_logs_tool", "get_network_policies_tool"),
        ),
        difficulty="hard",
        notes=(
            "Probes the sweep's namespace scoping: the initial sweep is entirely "
            "clean, and the only breadcrumb is the FQDN in the client's logs."
        ),
    ),
    Scenario(
        id="noisy",
        manifest_files=("eval-only/noisy-demo.yaml",),
        user_request=(
            "In namespace `{namespace}`, message processing has stalled somewhere "
            "in our queue consumers, but every pod shows as Running. Which one is "
            "broken, and what's wrong with it?"
        ),
        ready_when=(
            ReadyWhen(kind="pod_ready", pod_prefix="queue-consumer-d"),
            ReadyWhen(
                kind="log_contains",
                pod_prefix="queue-consumer-d",
                log_substring="unsupported schema version",
            ),
        ),
        golden=GoldenLabel(
            root_cause=(
                "`queue-consumer-d` is failing at the application level: it logs "
                "'cannot decode message: unsupported schema version 3 (this "
                "consumer supports version 2)' and drops every batch without "
                "processing anything. The other three consumers (a, b, c) are "
                "processing batches normally. Nothing is visible in Kubernetes "
                "state — all pods are Running and Ready with zero restarts and no "
                "Warning events — so the failure is only findable by reading the "
                "consumers' logs."
            ),
            remediation=(
                "Update queue-consumer-d to a build that understands schema "
                "version 3, or stop producers emitting v3 messages to it until it "
                "is upgraded. The other consumers need no change."
            ),
            remediation_category="fix-application-schema-mismatch",
            required_signals=(
                ("queue-consumer-d", "consumer-d"),
                ("schema", "version"),
                ("decode", "unsupported", "mismatch", "drop", "dropping", "dropped"),
            ),
            forbidden_terms=("oomkilled", "imagepullbackoff", "readiness probe", "networkpolicy", "selector"),
            expected_evidence_tools=("get_pod_status_tool", "get_container_logs_tool"),
        ),
        difficulty="hard",
        notes=(
            "Probes noise handling and prompt growth: 13 pods, all healthy by "
            "every status field, one bad log stream and no shortcut to guessing "
            "which pod holds it."
        ),
    ),
    Scenario(
        id="nofault",
        # Reuses the healthy demo pair rather than duplicating it — the
        # scenario *is* "the manifest that isn't broken".
        manifest_files=("web-demo.yaml",),
        # Phrased with the same alarmed framing as every other scenario and
        # asserting a fault that isn't there, so the agent gets no stylistic
        # tell that this one is different.
        user_request=(
            "Users are reporting errors from the web service in namespace "
            "`{namespace}`. Something's broken in there — what is it?"
        ),
        ready_when=(
            ReadyWhen(kind="pod_ready", pod_prefix="web"),
            ReadyWhen(kind="service_has_endpoints", service_name="web"),
        ),
        golden=GoldenLabel(
            no_fault=True,
            root_cause=(
                "There is no fault in this namespace. The `web` Deployment is "
                "fully available, its pod is Running and Ready with zero restarts, "
                "the Service's selector matches that pod and its endpoint list "
                "contains a ready address, and there are no Warning events. The "
                "correct answer is that the Kubernetes-visible state is healthy "
                "and the reported errors originate somewhere these tools cannot "
                "see — inside the application, upstream of the cluster (ingress, "
                "DNS, a caller), or in a different namespace. Naming any specific "
                "Kubernetes fault here is a fabrication."
            ),
            remediation=(
                "Report that nothing in the namespace is misconfigured and point "
                "the investigation elsewhere: application logs and error rates, "
                "the ingress or load balancer in front of the Service, or the "
                "caller's own namespace. Suggesting a Kubernetes change to 'fix' "
                "this namespace would be wrong — there is nothing to fix."
            ),
            remediation_category="no-fault-found",
            required_signals=(
                ("healthy", "no fault", "nothing wrong", "no issue", "no problem", "correctly configured", "working"),
                ("outside", "elsewhere", "application", "upstream", "another namespace", "beyond", "not in kubernetes", "not a kubernetes"),
            ),
            # Advisory here as everywhere else, and deliberately so: a good
            # no-fault answer *enumerates what it ruled out* ("this is not a
            # CrashLoopBackOff, the selector matches, endpoints are ready"),
            # so a load-bearing forbidden check would systematically punish
            # the best answers. The judge distinguishes ruling a cause out
            # from asserting it; a substring match cannot.
            forbidden_terms=("crashloopbackoff", "oomkilled", "imagepullbackoff"),
            expected_evidence_tools=("get_pod_status_tool", "get_service_endpoints_tool"),
        ),
        difficulty="hard",
        notes=(
            "The only scenario with no findable cause. Detects confabulation: an "
            "agent that always names something passes all twelve others."
        ),
    ),
)

SCENARIOS_BY_ID = {s.id: s for s in SCENARIOS}


def get_scenarios(ids: list[str] | None = None) -> list[Scenario]:
    if not ids:
        return list(SCENARIOS)
    unknown = [i for i in ids if i not in SCENARIOS_BY_ID]
    if unknown:
        known = ", ".join(SCENARIOS_BY_ID)
        raise SystemExit(f"Unknown scenario(s): {', '.join(unknown)}. Known: {known}")
    return [SCENARIOS_BY_ID[i] for i in ids]
