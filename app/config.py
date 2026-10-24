from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://pipeline_user:change-me@localhost:5432/observable_pipeline"
    )
    artifact_root: Path = Path("artifacts")
    log_level: str = "INFO"
    lease_seconds: float = 10.0
    poll_interval_seconds: float = 0.2
    retry_base_seconds: float = 0.1
    retry_max_seconds: float = 2.0
    gpu_total_memory_mb: int = 8192


@lru_cache
def get_settings() -> Settings:
    return Settings()
