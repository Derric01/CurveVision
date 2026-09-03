"""Application configuration.

One ``Settings`` object, environment driven, injected everywhere. No module in the
codebase reads ``os.environ`` directly -- that is what makes configuration testable and
what stops secrets leaking into import-time state.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CURVEVISION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- identity -----------------------------------------------------------------
    app_name: str = "CurveVision"
    environment: Environment = "development"
    debug: bool = False

    # --- security -----------------------------------------------------------------
    secret_key: str = Field(default="", description="HMAC key for tokens. Required outside dev.")
    access_token_ttl_seconds: int = 60 * 30
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    password_min_length: int = 10
    allow_registration: bool = True

    # --- database -----------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./curvevision.db"
    database_echo: bool = False
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # --- storage ------------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = "./data/storage"
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "curvevision"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_force_path_style: bool = True
    presigned_url_ttl_seconds: int = 3600

    # --- jobs ---------------------------------------------------------------------
    job_queue_backend: Literal["inline", "dramatiq"] = "inline"
    redis_url: str = "redis://localhost:6379/0"

    # --- media --------------------------------------------------------------------
    frames_per_chunk: int = 36
    thumbnail_max_edge: int = 320
    max_upload_bytes: int = 5 * 1024 * 1024 * 1024
    allowed_image_extensions: tuple[str, ...] = (
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
    )
    allowed_video_extensions: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".avi", ".webm")

    # --- api ----------------------------------------------------------------------
    api_prefix: str = "/api/v1"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    default_page_size: int = 50
    max_page_size: int = 500
    rate_limit_per_minute: int = 600

    # --- observability ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True

    @field_validator(
        "cors_origins", "allowed_image_extensions", "allowed_video_extensions", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        if not self.secret_key:
            if self.environment in ("production", "staging"):
                raise ValueError(
                    "CURVEVISION_SECRET_KEY must be set outside development. "
                    "Generate one with: python -c 'import secrets;print(secrets.token_urlsafe(48))'"
                )
            # Ephemeral key: fine for dev/test, and it invalidates tokens on restart,
            # which is the safe failure mode.
            object.__setattr__(self, "secret_key", secrets.token_urlsafe(48))
        if self.environment == "production" and self.debug:
            raise ValueError("debug must be disabled in production")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
