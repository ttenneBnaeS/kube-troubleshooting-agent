"""Model tier configuration.

Two tiers only (see docs/architecture.md §3): a reasoning-tier model for
planning/diagnosis nodes, and a fast/cheap tier for narrow classification
steps. Node functions ask for a tier, never hardcode a model id.
"""

from enum import Enum

from langchain_anthropic import ChatAnthropic
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelTier(str, Enum):
    REASONING = "reasoning"
    FAST = "fast"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    reasoning_model: str = "claude-sonnet-5"
    fast_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 1024

    def model_for(self, tier: ModelTier) -> str:
        return self.reasoning_model if tier is ModelTier.REASONING else self.fast_model


settings = Settings()


def get_chat_model(tier: ModelTier, **kwargs) -> ChatAnthropic:
    """Build a LangChain chat model for the given tier.

    Centralized here (rather than instantiated per-node) so tier→model
    routing and provider choice are a single edit, not a search-and-replace
    across every graph node that needs a model.
    """
    return ChatAnthropic(
        model=settings.model_for(tier),
        anthropic_api_key=settings.anthropic_api_key,
        max_tokens=settings.max_tokens,
        **kwargs,
    )
