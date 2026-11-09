"""Application configuration.

One ``Settings`` object, environment driven, injected everywhere. No module in the
codebase reads ``os.environ`` directly -- that is what makes configuration testable and
what stops secrets leaking into import-time state.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: A list setting, written in the environment as comma-separated text
#: (`CURVEVISION_CORS_ORIGINS=https://a.example,https://b.example`) or as a JSON list.
#: `NoDecode`, because pydantic-settings otherwise decodes a list-typed variable as JSON
#: before any validator runs, and every deployment that set one as text failed to start.
CommaSeparated = Annotated[tuple[str, ...], NoDecode]

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

    # --- local (desktop) mode -----------------------------------------------------
    #: Single-user desktop mode: the app owns its data directory, provisions one local
    #: account at first launch, and skips the sign-in screen entirely. This is how the
    #: installed application runs; it is never enabled on a shared instance because it
    #: also unlocks reading media from arbitrary paths on this machine.
    local_mode: bool = False
    #: Where the desktop app keeps its database, media and settings. Resolved per-OS by
    #: `curvevision.desktop.default_app_data_dir()` when unset.
    app_data_dir: str | None = None
    #: A built copy of the web application to serve from this process. The desktop build
    #: sets it so the whole product is one executable and the editor is same-origin with
    #: the API. A container deployment leaves it unset and puts a web server in front.
    web_root: str | None = None

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
    #: Where an export is assembled before it is sent: the archive, and any images fetched
    #: from S3 for it. An export with images needs about their total size free here. The
    #: system's temporary directory when unset.
    export_work_dir: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str = "curvevision"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_force_path_style: bool = True
    #: Where browsers reach the object store, when they can: media is then fetched from it
    #: directly, by presigned URL, rather than through the API (the bucket must allow the
    #: application's origin by CORS). Unset, the API streams media itself — which is right
    #: whenever `s3_endpoint_url` is an address only the server can reach, as `http://s3:9000`
    #: is under Docker Compose and in a cluster.
    s3_public_endpoint_url: str | None = None
    presigned_url_ttl_seconds: int = 3600

    # --- jobs ---------------------------------------------------------------------
    job_queue_backend: Literal["inline", "dramatiq"] = "inline"
    redis_url: str = "redis://localhost:6379/0"

    # --- media --------------------------------------------------------------------
    frames_per_chunk: int = 36
    thumbnail_max_edge: int = 320
    max_upload_bytes: int = 5 * 1024 * 1024 * 1024
    allowed_image_extensions: CommaSeparated = (
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
    )
    allowed_video_extensions: CommaSeparated = (".mp4", ".mov", ".mkv", ".avi", ".webm")

    #: Scanning uploads for malware before they are stored (`media/scanning.py`). ``none``
    #: by default; ``clamd`` sends each upload to ClamAV and refuses what it flags.
    content_scanner: Literal["none", "clamd"] = "none"
    clamd_host: str = "127.0.0.1"
    clamd_port: int = 3310
    #: A Unix socket instead of `clamd_host`/`clamd_port`, when set.
    clamd_socket: str | None = None
    clamd_timeout_seconds: float = 30.0
    #: Accept an upload that cannot be scanned (clamd down) rather than refuse it. Off: a
    #: scanner an attacker can disable by overloading it is not a control.
    content_scanner_fail_open: bool = False

    # --- single sign-on (OpenID Connect; `services/oidc.py`) -------------------------
    #: Sign-in through an OpenID Connect provider: Keycloak, Okta, Entra ID, Google,
    #: Authentik, Dex... Server shape only; ignored in local mode.
    oidc_enabled: bool = False
    #: The provider's issuer URL; its configuration is read from
    #: ``<issuer>/.well-known/openid-configuration``.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    #: Where the provider sends the browser back: this deployment's web address plus
    #: ``/auth/callback``, registered with the provider exactly so.
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid email profile"
    #: The sign-in button's label: "Sign in with <this>".
    oidc_display_name: str = "single sign-on"
    #: Create an account on a first sign-in. Off: only people who already have one (matched
    #: by verified email) or an existing link can sign in this way.
    oidc_create_users: bool = True
    #: Only these email domains may sign in (comma-separated); empty allows any.
    oidc_allowed_domains: CommaSeparated = ()
    #: A workspace (by slug) a newly created account joins, as an annotator.
    oidc_default_workspace: str = ""
    #: Disable the password sign-in form, when every person signs in through the provider.
    oidc_only: bool = False

    # --- single sign-on (SAML 2.0; `services/saml.py`) ---------------------------------
    #: Sign-in through a SAML identity provider: Entra ID, Okta, AD FS, Keycloak, Shibboleth,
    #: Google Workspace... Server shape only. Needs the `saml` extra.
    saml_enabled: bool = False
    #: The assertion consumer service the provider posts to: this deployment's API address
    #: plus ``/api/v1/auth/saml/acs``, registered with the provider exactly so.
    saml_acs_url: str = ""
    #: Where the browser is sent once the answer is read: the web address plus
    #: ``/auth/callback``.
    saml_callback_url: str = ""
    #: This service provider's entity ID; empty is the metadata URL beside the ACS.
    saml_sp_entity_id: str = ""
    #: The provider's metadata, from which its entity ID, sign-in URL and keys are read...
    saml_idp_metadata_url: str = ""
    #: ...or the three of them given directly.
    saml_idp_entity_id: str = ""
    saml_idp_sso_url: str = ""
    #: The provider's signing certificate, PEM or bare base64.
    saml_idp_x509_cert: str = ""
    #: The attribute holding the email address; empty tries the usual ones.
    saml_email_attribute: str = ""
    #: Trust the email address the provider asserts to link an existing account and make
    #: new ones — it is the operator's own provider. Off: only existing links sign in.
    saml_trust_email: bool = True
    #: The sign-in button's label: "Sign in with <this>".
    saml_display_name: str = "SAML single sign-on"
    saml_create_users: bool = True
    saml_allowed_domains: CommaSeparated = ()
    saml_default_workspace: str = ""

    # --- api ----------------------------------------------------------------------
    api_prefix: str = "/api/v1"
    cors_origins: CommaSeparated = ("http://localhost:5173", "http://127.0.0.1:5173")
    default_page_size: int = 50
    max_page_size: int = 500
    rate_limit_per_minute: int = 600
    #: Where request counts are kept. ``memory``: in this process, which is right for one
    #: instance and for the desktop build. ``redis``: in `redis_url`, shared by every
    #: instance, so a server run as several processes or replicas enforces one limit
    #: rather than one per process. Needs the `redis` extra.
    rate_limit_backend: Literal["memory", "redis"] = "memory"

    # --- observability ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    #: OpenTelemetry tracing (`core/tracing.py`), to an OTLP/HTTP collector. Needs the
    #: `tracing` extra.
    tracing_enabled: bool = False
    tracing_service_name: str = "curvevision-server"
    #: e.g. ``http://otel-collector:4318/v1/traces``; unset, the standard
    #: ``OTEL_EXPORTER_OTLP_ENDPOINT`` (or its default, localhost:4318) applies.
    tracing_otlp_endpoint: str | None = None
    #: The share of traces kept, from 0 to 1. A request arriving inside a sampled trace is
    #: always kept, whatever this says.
    tracing_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator(
        "cors_origins",
        "allowed_image_extensions",
        "allowed_video_extensions",
        "oidc_allowed_domains",
        "saml_allowed_domains",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                return json.loads(value)
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
def _settings_from_environment() -> Settings:
    return Settings()


_bound: Settings | None = None


def configure_settings(settings: Settings | None) -> None:
    """Bind this process to an explicit configuration.

    The application factory accepts a ``Settings`` object, and background job handlers,
    the storage factory and the job-queue factory all reach for ``get_settings()``. Unless
    the process is told which one won, those two answers differ: the app writes media to
    the directory it was given while a job reads from whatever the environment names. The
    desktop sidecar makes that divergence certain, because nothing about its configuration
    comes from the environment at all.

    Pass ``None`` to unbind, which is what tests do on teardown.
    """
    global _bound
    _bound = settings


def get_settings() -> Settings:
    """The configuration this process is running with."""
    return _bound if _bound is not None else _settings_from_environment()
