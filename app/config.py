from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    artifact_backend: Literal["local", "s3"] = "local"
    artifact_root: Path = Path("artifacts")
    artifact_s3_bucket: str | None = None
    artifact_s3_prefix: str = "observable-task-pipeline"
    artifact_s3_endpoint_url: str | None = None
    artifact_s3_region: str | None = None
    artifact_s3_access_key_id: str | None = None
    artifact_s3_secret_access_key: SecretStr | None = None
    log_level: str = "INFO"
    lease_seconds: float = 10.0
    poll_interval_seconds: float = 0.2
    worker_idle_backoff_max_seconds: float = 5.0
    worker_idle_backoff_multiplier: float = 2.0
    task_notify_enabled: bool = True
    task_notify_channel: str = "pipeline_task_ready"
    retry_base_seconds: float = 0.1
    retry_max_seconds: float = 2.0
    gpu_total_memory_mb: int = 8192


@lru_cache
def get_settings() -> Settings:
    return Settings()
