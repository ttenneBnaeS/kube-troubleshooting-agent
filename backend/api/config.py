"""API configuration.

Tunables use env_prefix API_ (API_CHECKPOINT_DB_PATH, ...).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="API_")

    # SQLite file the LangGraph checkpointer persists conversation threads
    # in. Relative to the backend directory; created on first start.
    checkpoint_db_path: str = "data/checkpoints.db"


settings = ApiSettings()
