"""Database engine/session management with honest configuration states.

Design rules (from the project specification):

* The app NEVER falls back to SQLite or an in-memory database.
* While ``DATABASE_URL`` is missing or still contains the placeholder
  password, the app starts in *database-unconfigured development mode* and
  no connection is ever attempted.
* When credentials are present, connectivity is probed lazily (on demand,
  cached with a TTL, single attempt per TTL) — never retried indefinitely.
* Credentials are never logged, never included in exceptions, and never
  returned by any API (``database_url_display()`` masks the password).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import (
    DatabaseConfigState,
    DatabaseConnectivity,
    Settings,
    get_settings,
)
from app.core.exceptions import DatabaseNotConfiguredError, DatabaseUnavailableError

logger = logging.getLogger(__name__)

_engine_lock = threading.Lock()
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

_probe_lock = threading.Lock()
_probe_cache: "tuple[float, DatabaseProbe] | None" = None


@dataclass(frozen=True)
class DatabaseProbe:
    """Result of inspecting database configuration + connectivity."""

    config_state: DatabaseConfigState
    connectivity: DatabaseConnectivity
    reason: str
    checked_at: float | None = None

    @property
    def is_usable(self) -> bool:
        return (
            self.config_state == DatabaseConfigState.CONFIGURED
            and self.connectivity == DatabaseConnectivity.REACHABLE
        )

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "configured": self.config_state == DatabaseConfigState.CONFIGURED,
            "config_state": self.config_state.value,
            "connectivity": self.connectivity.value,
            "reason": self.reason,
        }


def _build_engine(settings: Settings) -> Engine:
    url = (settings.database_url or "").strip()
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": settings.db_probe_timeout_seconds,
            "application_name": "codeguard-backend",
        },
    )
    return engine


def get_engine(settings: Settings | None = None) -> Engine:
    """Return (creating on first use) the application engine.

    Raises ``DatabaseNotConfiguredError`` when the URL is missing or still a
    placeholder, and ``DatabaseUnavailableError`` when credentials exist but
    the server cannot be reached.
    """
    settings = settings or get_settings()
    state = settings.database_config_state()
    if state != DatabaseConfigState.CONFIGURED:
        raise DatabaseNotConfiguredError(settings.database_config_reason())

    global _engine, _session_factory
    with _engine_lock:
        if _engine is None:
            try:
                _engine = _build_engine(settings)
            except SQLAlchemyError as exc:
                raise DatabaseUnavailableError(
                    "The configured DATABASE_URL could not be used."
                ) from exc
        return _engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    global _session_factory
    engine = get_engine(settings)
    with _engine_lock:
        if _session_factory is None:
            _session_factory = sessionmaker(
                bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
            )
        return _session_factory


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Transactional scope around a session (thread-safe: new session per call)."""
    factory = get_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """Drop the cached engine (used on shutdown and by tests)."""
    global _engine, _session_factory
    with _engine_lock:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:  # pragma: no cover
                pass
        _engine = None
        _session_factory = None


def probe_database(
    settings: Settings | None = None, *, force: bool = False
) -> DatabaseProbe:
    """Inspect configuration and (only when configured) probe connectivity.

    Results are cached: success for ``db_probe_ttl_seconds``, failure for a
    longer backoff, so a missing password never triggers connection storms.
    """
    global _probe_cache
    settings = settings or get_settings()
    state = settings.database_config_state()

    now = time.monotonic()
    if not force:
        with _probe_lock:
            if _probe_cache is not None:
                cached_at, probe = _probe_cache
                ttl = (
                    settings.db_probe_ttl_seconds
                    if probe.connectivity != DatabaseConnectivity.UNREACHABLE
                    else max(settings.db_probe_ttl_seconds * 2, 60)
                )
                if now - cached_at < ttl and probe.config_state == state:
                    return probe

    if state != DatabaseConfigState.CONFIGURED:
        probe = DatabaseProbe(
            config_state=state,
            connectivity=DatabaseConnectivity.NOT_ATTEMPTED,
            reason=settings.database_config_reason(),
        )
    else:
        probe = _probe_connectivity(settings, state)

    with _probe_lock:
        _probe_cache = (now, probe)
    return probe


def _probe_connectivity(settings: Settings, state: DatabaseConfigState) -> DatabaseProbe:
    """Single bounded connection attempt. Never logs or raises credentials."""
    try:
        engine = get_engine(settings)
    except DatabaseUnavailableError:
        return DatabaseProbe(
            config_state=state,
            connectivity=DatabaseConnectivity.UNREACHABLE,
            reason="The configured connection string is not usable by SQLAlchemy.",
        )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return DatabaseProbe(
            config_state=state,
            connectivity=DatabaseConnectivity.REACHABLE,
            reason="Connection verified.",
        )
    except (DBAPIError, SQLAlchemyError, OSError) as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
            getattr(exc, "orig", None), "pgcode", None
        )
        logger.warning("Database probe failed (sqlstate=%s): %s", sqlstate, exc.__class__.__name__)
        return DatabaseProbe(
            config_state=state,
            connectivity=DatabaseConnectivity.UNREACHABLE,
            reason=(
                "Credentials are configured but the database could not be reached "
                f"({exc.__class__.__name__}). Check that PostgreSQL 17 is running "
                "and the password in .env is correct."
            ),
        )


def reset_probe_cache() -> None:
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def require_database(settings: Settings | None = None) -> DatabaseProbe:
    """Return the probe if the database is usable, else raise the right error.

    This is the single gate every DB-dependent endpoint goes through.
    """
    probe = probe_database(settings)
    if probe.config_state != DatabaseConfigState.CONFIGURED:
        raise DatabaseNotConfiguredError(settings.database_config_reason() if settings else get_settings().database_config_reason())
    if probe.connectivity != DatabaseConnectivity.REACHABLE:
        raise DatabaseUnavailableError(probe.reason)
    return probe
