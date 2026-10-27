from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STEPWISE_", extra="ignore")

    database_url: str = "sqlite:///./stepwise.sqlite3"
    redis_url: str = "redis://localhost:6379/0"
    artifact_root: Path = Path("data/artifacts")
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "stepwise"
    s3_prefix: str = "artifacts"
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"
    max_attempts: int = Field(default=3, ge=1, le=5)
    retry_delay_s: int = Field(default=5, ge=0, le=300)
    job_timeout_s: int = Field(default=300, ge=10, le=3600)
    recovery_grace_s: int = Field(default=60, ge=10, le=3600)

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
