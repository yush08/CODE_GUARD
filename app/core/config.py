"""Central configuration for the CodeGuard backend.

Configuration is loaded from environment variables and an optional `.env`
file (never committed). The module also implements the database-configuration
state machine required by the project:

* missing ``DATABASE_URL``           -> UNCONFIGURED (missing url)
* placeholder password               -> UNCONFIGURED (placeholder password)
* malformed / unsupported URL        -> INVALID
* real credentials present           -> CONFIGURED (connectivity verified lazily)

The backend deliberately does NOT fall back to SQLite or any in-memory
database. When the database is not configured the application starts in an
explicitly identified *database-unconfigured development mode*.
"""

from __future__ import annotations

import functools
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PASSWORDS = {"", "your_password", "your-password", "changeme", "password"}


class DatabaseConfigState(str, Enum):
    """How the database configuration itself looks (no connection attempted)."""

    UNCONFIGURED_MISSING_URL = "UNCONFIGURED_MISSING_URL"
    UNCONFIGURED_PLACEHOLDER = "UNCONFIGURED_PLACEHOLDER"
    INVALID_URL = "INVALID_URL"
    CONFIGURED = "CONFIGURED"


class DatabaseConnectivity(str, Enum):
    """Result of an actual connectivity probe (only meaningful when CONFIGURED)."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    REACHABLE = "REACHABLE"
    UNREACHABLE = "UNREACHABLE"


class Settings(BaseSettings):
    """Application settings. All values may be overridden via environment/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -------------------------------------------------------
    app_name: str = "CodeGuard"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug: bool = True

    # --- database ----------------------------------------------------------
    database_url: str | None = None
    db_probe_timeout_seconds: int = 3
    db_probe_ttl_seconds: int = 30

    # --- web ---------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://localhost:4173"

    # --- sandbox -----------------------------------------------------------
    sandbox_root: Path = Path("./sandbox")

    # --- hard server-side limits -------------------------------------------
    max_source_size_bytes: int = 100_000
    max_output_size_bytes: int = 1_048_576
    max_compilation_seconds: int = 15

    # --- default execution limits ------------------------------------------
    default_execution_timeout_seconds: float = 3.0
    default_memory_limit_mb: int = 128
    default_active_process_limit: int = 10

    # --- hard bounds for per-problem limits --------------------------------
    max_allowed_time_limit_ms: int = 30_000
    max_allowed_memory_limit_mb: int = 1024
    max_allowed_process_limit: int = 64
    max_allowed_test_cases: int = 50

    # --- concurrency -------------------------------------------------------
    max_concurrent_executions: int = 2
    max_queued_jobs: int = 50
    run_wait_timeout_seconds: float = 30.0
    workspace_cleanup_timeout_seconds: int = 10

    # --- compilers ---------------------------------------------------------
    compiler_c_path: str = ""
    compiler_cpp_path: str = ""
    compiler_c_flags: str = "-O2 -std=c11"
    compiler_cpp_flags: str = "-O2 -std=c++14"

    # --- logging -----------------------------------------------------------
    log_level: str = "INFO"

    # --- access model ------------------------------------------------------
    auth_mode: str = "local_dev"
    workspace_label_header: str = "X-Workspace-Label"

    # --- misc --------------------------------------------------------------
    sequence_rules_path: str = ""

    @field_validator("sandbox_root", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    def database_config_state(self) -> DatabaseConfigState:
        """Classify the configured DATABASE_URL without opening a connection."""
        raw = (self.database_url or "").strip()
        if not raw:
            return DatabaseConfigState.UNCONFIGURED_MISSING_URL

        parsed = _parse_postgres_url(raw)
        if parsed is None:
            return DatabaseConfigState.INVALID_URL

        _user, password, _rest = parsed
        if password.strip().lower() in PLACEHOLDER_PASSWORDS:
            return DatabaseConfigState.UNCONFIGURED_PLACEHOLDER
        return DatabaseConfigState.CONFIGURED

    def database_config_reason(self) -> str:
        state = self.database_config_state()
        return {
            DatabaseConfigState.UNCONFIGURED_MISSING_URL: "DATABASE_URL is not set.",
            DatabaseConfigState.UNCONFIGURED_PLACEHOLDER: (
                "DATABASE_URL still contains the placeholder password. "
                "Copy .env.example to .env and replace YOUR_PASSWORD."
            ),
            DatabaseConfigState.INVALID_URL: (
                "DATABASE_URL is malformed or uses an unsupported driver. "
                "Expected postgresql+psycopg://user:password@host:port/database. "
                "No SQLite/in-memory fallback exists by design."
            ),
            DatabaseConfigState.CONFIGURED: "Credentials present.",
        }[state]

    def database_url_display(self) -> str | None:
        """A credential-free representation of DATABASE_URL for logs/status."""
        raw = (self.database_url or "").strip()
        if not raw:
            return None
        parsed = _parse_postgres_url(raw)
        if parsed is None:
            return "<unparseable DATABASE_URL>"
        user, _password, rest = parsed
        host, port, database = rest
        return urlunsplit(
            (
                "postgresql+psycopg",
                f"{user}:****@{host}:{port}",
                "/" + database.lstrip("/"),
                "",
                "",
            )
        )


def _parse_postgres_url(raw: str) -> tuple[str, str, tuple[str, str, str]] | None:
    """Return (user, password, (host, port, path)) for supported URLs, else None."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in {"postgresql", "postgres", "postgresql+psycopg", "postgresql+psycopg2"}:
        return None
    if not parts.hostname:
        return None
    user = parts.username or "postgres"
    password = parts.password or ""
    port = str(parts.port or 5432)
    database = parts.path.lstrip("/")
    return user, password, (parts.hostname, port, database)

@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (cached)."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests)."""
    get_settings.cache_clear()
