"""Scenario lifecycle against the Kind cluster: create, wait, destroy.

Two trust domains meet in this file, and the split is deliberate. The
harness *must* mutate the cluster — injecting a failure is the whole
point — so it shells out to `kubectl apply`/`delete`. The agent under test
cannot: it only ever sees the read-only tool catalog, which has no
mutating verb to call (docs/architecture.md §5). The read-only boundary
constrains the agent, not its test rig.

Waiting reuses the plain tool functions from `tools/` rather than
reimplementing pod-status parsing — the same reuse `tools/langchain_tools.py`
was split apart to allow.
"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tools import get_container_logs, get_pod_status, get_service_endpoints
from tools.models import PodStatusResult

from .scenarios import ReadyWhen, Scenario

DEFAULT_TIMEOUT_SECONDS = 240
POLL_INTERVAL_SECONDS = 3


class ScenarioSetupError(RuntimeError):
    """The scenario never reached its expected failure state."""


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise ScenarioSetupError(f"kubectl {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def ensure_kubectl_available() -> None:
    try:
        _kubectl("version", "--client=true", "--output=yaml")
    except FileNotFoundError as exc:  # pragma: no cover - environment check
        raise ScenarioSetupError("kubectl not found on PATH; the eval harness needs it") from exc


def setup_scenario(scenario: Scenario) -> str:
    """Create a throwaway namespace and apply the scenario into it."""
    namespace = scenario.namespace
    # Delete first so a previous aborted run can't leave half a scenario
    # behind and quietly poison the result.
    teardown_all(scenario, wait=True)

    # The aux namespace goes up first: it hosts the dependency the primary
    # namespace's client talks to, so creating it second would give the
    # client a window of failing for the wrong reason.
    if scenario.aux_namespace:
        _kubectl("create", "namespace", scenario.aux_namespace)
        _apply(scenario.aux_manifest_paths(), scenario.aux_namespace)

    _kubectl("create", "namespace", namespace)
    _apply(scenario.manifest_paths(), namespace)
    return namespace


def _apply(paths: list[Path], namespace: str) -> None:
    args = ["apply", "-n", namespace]
    for path in paths:
        if not path.exists():
            raise ScenarioSetupError(f"manifest not found: {path}")
        args += ["-f", str(path)]
    _kubectl(*args)


def teardown_scenario(namespace: str, wait: bool = False) -> None:
    _kubectl(
        "delete",
        "namespace",
        namespace,
        "--ignore-not-found",
        f"--wait={'true' if wait else 'false'}",
        check=False,
    )


def teardown_all(scenario: Scenario, wait: bool = False) -> None:
    teardown_scenario(scenario.namespace, wait=wait)
    if scenario.aux_namespace:
        teardown_scenario(scenario.aux_namespace, wait=wait)


def wait_until_broken(
    scenario: Scenario,
    namespace: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> "ReadyReport":
    """Block until the scenario's failure is actually observable.

    Without this the agent would sometimes be asked to diagnose a pod that
    is still pulling its image, which grades the harness's timing rather
    than the agent.
    """
    predicates = scenario.ready_predicates
    deadline = time.monotonic() + timeout_seconds
    last_seen = "no matching pod yet"

    while time.monotonic() < deadline:
        # Every predicate must hold: a scenario with two independent
        # failures isn't set up until both have actually appeared.
        results = [_predicate_met(spec, namespace) for spec in predicates]
        last_seen = "; ".join(detail for _, detail in results)
        if all(ok for ok, _ in results):
            time.sleep(max(spec.settle_seconds for spec in predicates))
            return ReadyReport(namespace=namespace, detail=last_seen)
        time.sleep(POLL_INTERVAL_SECONDS)

    raise ScenarioSetupError(
        f"scenario '{scenario.id}' did not reach its failure state within "
        f"{timeout_seconds}s (last observed: {last_seen})"
    )


@dataclass
class ReadyReport:
    namespace: str
    detail: str


def _predicate_met(spec: ReadyWhen, namespace: str) -> tuple[bool, str]:
    if spec.kind in ("service_no_endpoints", "service_has_endpoints"):
        return _check_service_endpoints(spec, namespace)

    try:
        pods = [p for p in get_pod_status(namespace=namespace) if p.name.startswith(spec.pod_prefix)]
    except Exception as exc:  # the namespace may not have propagated yet
        return False, f"pod lookup failed: {exc}"

    if not pods:
        return False, f"no pod with prefix '{spec.pod_prefix}'"

    pod = pods[0]
    if spec.kind == "container_reason":
        return _check_container_reason(pod, spec)
    if spec.kind == "pod_not_ready":
        if pod.phase == "Running" and not pod.pod_ready:
            return True, f"{pod.name} Running but not Ready"
        return False, f"{pod.name} phase={pod.phase} ready={pod.pod_ready}"
    if spec.kind == "pod_ready":
        # For scenarios where the pods are *supposed* to be healthy and the
        # failure lives elsewhere — waiting on this stops the agent being
        # asked about a Service whose pods are still starting.
        if all(p.phase == "Running" and p.pod_ready for p in pods):
            return True, f"{len(pods)} pod(s) matching '{spec.pod_prefix}' Running and Ready"
        return False, f"not all '{spec.pod_prefix}' pods ready yet ({len(pods)} found)"
    if spec.kind == "log_contains":
        return _check_logs(pod, spec, namespace)
    return False, f"unknown predicate kind {spec.kind!r}"


def _check_service_endpoints(spec: ReadyWhen, namespace: str) -> tuple[bool, str]:
    """Gate on a Service having, or not having, backing endpoints.

    The `has` direction matters for the no-fault scenario: asking the agent
    about a Service whose endpoints haven't populated yet would hand it a
    real fault to find, turning a scenario about restraint into a scenario
    about timing.
    """
    want_endpoints = spec.kind == "service_has_endpoints"
    try:
        result = get_service_endpoints(service_name=spec.service_name, namespace=namespace)
    except Exception as exc:
        return False, f"service {spec.service_name!r} lookup failed: {exc}"

    ready = len(result.ready_addresses)
    total = ready + len(result.not_ready_addresses)

    if want_endpoints:
        if ready > 0:
            return True, f"service {spec.service_name} has {ready} ready endpoint(s)"
        return False, f"service {spec.service_name} has no ready endpoints yet"
    if total == 0:
        return True, f"service {spec.service_name} has no endpoints (selector {result.selector})"
    return False, f"service {spec.service_name} still has {total} endpoint(s)"


def _check_container_reason(pod: PodStatusResult, spec: ReadyWhen) -> tuple[bool, str]:
    # Init containers count: a scenario that breaks during init never
    # produces a matching reason on an app container (those just report
    # PodInitializing), so scanning only `pod.containers` would leave such
    # a scenario waiting until it timed out.
    for container in [*pod.init_containers, *pod.containers]:
        reason_matches = container.reason in spec.reasons
        restarts_ok = container.restart_count >= spec.min_restarts
        if reason_matches and restarts_ok:
            return True, f"{pod.name}/{container.name} reason={container.reason} restarts={container.restart_count}"
    observed = ", ".join(
        f"{c.name}={c.reason or c.state}:{c.restart_count}" for c in [*pod.init_containers, *pod.containers]
    )
    return False, f"{pod.name} containers [{observed}]"


def _check_logs(pod: PodStatusResult, spec: ReadyWhen, namespace: str) -> tuple[bool, str]:
    try:
        logs = get_container_logs(pod_name=pod.name, namespace=namespace, tail_lines=50)
    except Exception as exc:
        return False, f"{pod.name} logs unavailable: {exc}"
    blob = "\n".join(logs.lines).lower()
    needle = (spec.log_substring or "").lower()
    if needle and needle in blob:
        return True, f"{pod.name} logs contain {spec.log_substring!r}"
    return False, f"{pod.name} logs do not yet contain {spec.log_substring!r}"
