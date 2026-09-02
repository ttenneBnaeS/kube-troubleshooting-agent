"""Shared Kubernetes API clients.

Config loading happens once, lazily, on first use — not at import time —
so importing `tools` never requires a reachable cluster. Falls back from
in-cluster config to kubeconfig, matching how the agent would actually be
deployed (in-cluster service account) vs. run locally against Kind.
"""

from functools import lru_cache

from kubernetes import client, config

from .config import settings


@lru_cache(maxsize=1)
def _ensure_config_loaded() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(config_file=settings.kubeconfig_path, context=settings.context)


@lru_cache(maxsize=1)
def get_core_v1_api() -> client.CoreV1Api:
    _ensure_config_loaded()
    return client.CoreV1Api()


@lru_cache(maxsize=1)
def get_apps_v1_api() -> client.AppsV1Api:
    _ensure_config_loaded()
    return client.AppsV1Api()


@lru_cache(maxsize=1)
def get_networking_v1_api() -> client.NetworkingV1Api:
    _ensure_config_loaded()
    return client.NetworkingV1Api()
