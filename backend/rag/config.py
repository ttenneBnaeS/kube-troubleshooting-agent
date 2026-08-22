"""RAG configuration.

Tunables use env_prefix RAG_ (RAG_QDRANT_URL, RAG_COLLECTION_NAME, ...);
VOYAGE_API_KEY is a special-cased alias so it matches the SDK's own
standard env var name rather than becoming RAG_VOYAGE_API_KEY.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="RAG_")

    voyage_api_key: str = Field(default="", validation_alias="VOYAGE_API_KEY")
    embedding_model: str = "voyage-3.5"
    qdrant_url: str = "http://localhost:6333"
    collection_name: str = "k8s_docs"
    chunk_size: int = 800
    chunk_overlap: int = 100
    top_k: int = 4


settings = RagSettings()
