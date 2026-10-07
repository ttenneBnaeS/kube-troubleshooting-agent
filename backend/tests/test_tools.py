"""Normalization in the read-only tool layer (docs/architecture.md §4)."""

import base64
import json
from datetime import UTC, datetime, timedelta

from builders import container_status, event, pod
from kubernetes import client as k8s

from tools import describe_resource, get_container_logs, get_pod_status, get_recent_events, get_service_endpoints
from tools.errors import describe_tool_error
from tools.policies import _render_selector


class TestPodStatus:
    def test_waiting_reason_is_flattened(self, core):
        core.pods = [
            pod("api", ready=False, phase="Running",
                containers=[container_status(waiting_reason="CrashLoopBackOff", message="back-off", restarts=7)])
        ]
        [result] = get_pod_status(namespace="default")
        [c] = result.containers
        assert (c.state, c.reason, c.message, c.restart_count) == ("waiting", "CrashLoopBackOff", "back-off", 7)
        assert result.pod_ready is False

    def test_terminated_reason_survives(self, core):
        core.pods = [pod("oom", containers=[container_status(terminated_reason="OOMKilled", exit_code=137)])]
        [result] = get_pod_status(namespace="default")
        assert result.containers[0].state == "terminated"
        assert result.containers[0].reason == "OOMKilled"

    def test_init_containers_reported_separately(self, core):
        # A pod blocked in init: app container says only PodInitializing,
        # the real failure is on the init container. Both must survive.
        core.pods = [
            pod(
                "migrate",
                phase="Pending",
                ready=False,
                containers=[container_status(waiting_reason="PodInitializing")],
                init_containers=[container_status("db-migrate", waiting_reason="CrashLoopBackOff", restarts=4)],
            )
        ]
        [result] = get_pod_status(namespace="default")
        assert result.containers[0].reason == "PodInitializing"
        assert [(c.name, c.reason) for c in result.init_containers] == [("db-migrate", "CrashLoopBackOff")]

    def test_labels_and_age(self, core):
        core.pods = [pod("web", labels={"tier": "api"}, age=timedelta(hours=1))]
        [result] = get_pod_status(namespace="default")
        assert result.labels == {"tier": "api"}
        assert 3500 < result.age_seconds < 3700

    def test_namespace_scoping(self, core):
        core.pods = [pod("a", namespace="one"), pod("b", namespace="two")]
        assert [p.name for p in get_pod_status(namespace="two")] == ["b"]


class TestEvents:
    def test_newest_first_filtered_and_limited(self, core):
        now = datetime.now(UTC)
        core.events = [
            event("web", reason="Old", last_seen=now - timedelta(minutes=10)),
            event("web", reason="New", last_seen=now),
            event("other", reason="Unrelated", last_seen=now),
        ]
        records = get_recent_events(namespace="default", involved_object_name="web")
        assert [r.reason for r in records] == ["New", "Old"]
        assert records[0].involved_object == "Pod/web"
        assert len(get_recent_events(namespace="default", limit=1)) == 1


class TestLogs:
    def test_bytes_are_decoded_into_lines(self, core):
        # The client can hand back `b'...'` reprs if left to decode itself;
        # the tool decodes raw bytes and must yield real lines.
        core.logs["api"] = "starting\nFATAL: missing template\n"
        result = get_container_logs("api", namespace="default")
        assert result.lines == ["starting", "FATAL: missing template"]


class TestServices:
    def test_endpoint_readiness_split(self, core):
        core.services["search"] = k8s.V1Service(
            spec=k8s.V1ServiceSpec(
                selector={"tier": "frontend"},
                ports=[k8s.V1ServicePort(port=80, target_port=8080)],
                cluster_ip="10.0.0.1",
            )
        )
        core.endpoints["search"] = k8s.V1Endpoints(
            subsets=[
                k8s.V1EndpointSubset(
                    addresses=[k8s.V1EndpointAddress(ip="10.1.0.5")],
                    not_ready_addresses=[k8s.V1EndpointAddress(ip="10.1.0.6")],
                )
            ]
        )
        result = get_service_endpoints("search", namespace="default")
        assert result.selector == {"tier": "frontend"}
        assert result.ready_addresses == ["10.1.0.5"]
        assert result.not_ready_addresses == ["10.1.0.6"]
        assert result.ports[0].target_port == "8080"

    def test_no_subsets_means_no_endpoints(self, core):
        core.services["s"] = k8s.V1Service(spec=k8s.V1ServiceSpec(selector={"a": "b"}))
        core.endpoints["s"] = k8s.V1Endpoints(subsets=None)
        result = get_service_endpoints("s", namespace="default")
        assert result.ready_addresses == [] and result.not_ready_addresses == []


class TestDescribe:
    def test_secret_returns_key_names_never_values(self, core):
        secret_value = base64.b64encode(b"hunter2-super-secret").decode()
        core.secrets["db"] = k8s.V1Secret(type="Opaque", data={"password": secret_value})
        result = describe_resource("secret", "db", namespace="default")
        dumped = json.dumps(result.model_dump())
        assert result.summary["keys"] == ["password"]
        assert secret_value not in dumped
        assert "hunter2" not in dumped

    def test_configmap_returns_keys_not_values(self, core):
        core.configmaps["app"] = k8s.V1ConfigMap(data={"log_level": "debug-and-a-long-value"})
        result = describe_resource("configmap", "app", namespace="default")
        assert result.summary["keys"] == ["log_level"]
        assert "debug-and-a-long-value" not in json.dumps(result.model_dump())

    def test_pod_config_references(self, core):
        spec_containers = [
            k8s.V1Container(
                name="main",
                image="busybox",
                env=[
                    k8s.V1EnvVar(
                        name="DB_PASSWORD",
                        value_from=k8s.V1EnvVarSource(
                            secret_key_ref=k8s.V1SecretKeySelector(name="db-credentials", key="password")
                        ),
                    ),
                    k8s.V1EnvVar(name="API_TOKEN", value="inline-credential-do-not-leak"),
                ],
                env_from=[k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name="app-config"))],
            )
        ]
        volumes = [
            k8s.V1Volume(
                name="certs",
                secret=k8s.V1SecretVolumeSource(secret_name="tls", items=[k8s.V1KeyToPath(key="tls.crt", path="c")]),
            )
        ]
        core.pods = [pod("secret-demo", spec_containers=spec_containers, volumes=volumes)]

        result = describe_resource("pod", "secret-demo", namespace="default")
        refs = result.summary["config_references"]

        assert refs["env"] == [
            {
                "container": "main",
                "container_kind": "app",
                "env_var": "DB_PASSWORD",
                "source": "secret",
                "name": "db-credentials",
                "key": "password",
                "optional": False,
            }
        ]
        assert refs["env_from"][0]["name"] == "app-config"
        assert refs["volumes"][0] == {
            "volume": "certs", "source": "secret", "name": "tls", "keys": ["tls.crt"], "optional": False,
        }
        # Literal env values are where people inline credentials.
        assert "inline-credential-do-not-leak" not in json.dumps(result.model_dump())

    def test_pod_describe_does_not_expose_container_ports(self, core):
        # Deliberate gap: the `targetport` eval scenario measures whether
        # the agent admits it can't see the container's real port.
        spec = [k8s.V1Container(name="main", image="x", ports=[k8s.V1ContainerPort(container_port=9090)])]
        core.pods = [pod("p", spec_containers=spec)]
        assert "9090" not in json.dumps(describe_resource("pod", "p", namespace="default").model_dump())

    def test_pod_describe_includes_init_statuses(self, core):
        core.pods = [pod("m", init_containers=[container_status("init", waiting_reason="CrashLoopBackOff")])]
        summary = describe_resource("pod", "m", namespace="default").summary
        assert summary["init_container_statuses"][0]["state"] == {
            "waiting": {"reason": "CrashLoopBackOff", "message": None}
        }


class TestErrors:
    def test_api_404_is_normalized(self, core):
        try:
            describe_resource("secret", "db-credentials", namespace="default")
        except Exception as exc:
            fact = describe_tool_error(exc)
        assert fact == {"error": "not_found", "status": 404, "message": 'secrets "db-credentials" not found'}

    def test_unexpected_exception(self):
        assert describe_tool_error(ValueError("boom")) == {"error": "tool_failed", "message": "ValueError: boom"}


class TestSelectorRendering:
    def test_empty_selector_matches_everything(self):
        assert _render_selector(k8s.V1LabelSelector()) == ""
        assert _render_selector(None) == ""

    def test_labels_and_expressions(self):
        selector = k8s.V1LabelSelector(
            match_labels={"b": "2", "a": "1"},
            match_expressions=[
                k8s.V1LabelSelectorRequirement(key="env", operator="In", values=["prod", "stage"]),
                k8s.V1LabelSelectorRequirement(key="legacy", operator="DoesNotExist"),
                k8s.V1LabelSelectorRequirement(key="team", operator="Exists"),
            ],
        )
        assert _render_selector(selector) == "a=1,b=2,env in (prod,stage),!legacy,team"
