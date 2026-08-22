"""Kubernetes access configuration.

Separate from `models.config.Settings` (model tier routing) since this
governs cluster access, not the LLM. Env vars are prefixed `KUBE_`.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class KubeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="KUBE_")

    namespace: str = "default"
    context: str | None = None
    kubeconfig_path: str | None = None


settings = KubeSettings()
