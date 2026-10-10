"""Configuration state machine: DATABASE_URL classification and masking."""

from __future__ import annotations

from app.core.config import (
    DatabaseConfigState,
    Settings,
)


def _settings(url: str | None) -> Settings:
    return Settings(_env_file=None, database_url=url)


def test_missing_url_is_unconfigured() -> None:
    s = _settings(None)
    assert s.database_config_state() == DatabaseConfigState.UNCONFIGURED_MISSING_URL
    assert "DATABASE_URL is not set" in s.database_config_reason()
    assert s.database_url_display() is None


def test_placeholder_password_is_unconfigured() -> None:
    s = _settings("postgresql+psycopg://postgres:your_password@localhost:5432/codeguard")
    assert s.database_config_state() == DatabaseConfigState.UNCONFIGURED_PLACEHOLDER
    assert ".env" in s.database_config_reason()


def test_sqlite_url_is_invalid_not_a_fallback() -> None:
    s = _settings("sqlite:///./codeguard.db")
    assert s.database_config_state() == DatabaseConfigState.INVALID_URL
    assert "No SQLite/in-memory fallback" in s.database_config_reason()


def test_garbage_url_is_invalid() -> None:
    assert _settings("not a url").database_config_state() == DatabaseConfigState.INVALID_URL
    assert _settings("ftp://user:pw@host/db").database_config_state() == DatabaseConfigState.INVALID_URL


def test_real_credentials_are_configured_and_masked() -> None:
    s = _settings("postgresql+psycopg://postgres:SUPER_SECRET@localhost:5432/codeguard")
    assert s.database_config_state() == DatabaseConfigState.CONFIGURED
    display = s.database_url_display()
    assert display is not None
    assert "SUPER_SECRET" not in display
    assert "****" in display
    assert display.endswith("localhost:5432/codeguard") or "localhost:5432/codeguard" in display


def test_cors_origins_come_from_settings_only() -> None:
    s = _settings(None)
    origins = s.cors_origins_list
    assert origins, "at least one origin must be configurable"
    # No origin may be embedded in code paths: they are env-driven.
    s2 = Settings(_env_file=None, cors_origins="http://127.0.0.1:3000, http://localhost:8080")
    assert s2.cors_origins_list == ["http://127.0.0.1:3000", "http://localhost:8080"]


def test_hard_limits_are_bounded() -> None:
    s = _settings(None)
    assert s.max_source_size_bytes > 0
    assert s.max_output_size_bytes > 0
    assert s.max_allowed_time_limit_ms >= s.default_execution_timeout_seconds * 1000
    assert s.max_concurrent_executions >= 1
    assert s.max_queued_jobs >= 1
