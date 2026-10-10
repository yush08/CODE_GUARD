"""Alembic environment for CodeGuard.

* The URL comes from ``DATABASE_URL`` in the environment / `.env`.
* Migrations are NEVER applied automatically by the web application.
* In offline mode (``alembic upgrade head --sql``) a placeholder URL is used
  so SQL can be generated and inspected without a live database.
* This environment only targets PostgreSQL. There is no SQLite fallback.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the repository root importable when alembic runs from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.models  # noqa: F401,E402  (registers all tables on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

PLACEHOLDER_URL = "postgresql+psycopg://postgres:placeholder@localhost:5432/codeguard"


def _resolve_url() -> str:
    """Return the configured PostgreSQL URL, erroring clearly otherwise."""
    settings = get_settings()
    url = (settings.database_url or "").strip()
    if not url or settings.database_config_state().value != "CONFIGURED":
        if context.is_offline_mode():
            return PLACEHOLDER_URL
        raise RuntimeError(
            "No usable DATABASE_URL is configured, so migrations cannot be applied.\n"
            f"  Reason: {settings.database_config_reason()}\n"
            "  Fix: copy .env.example to .env, set the real PostgreSQL password,\n"
            "  then run: alembic upgrade head"
        )
    return url


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Never touch unrelated tables that might exist in the database."""
    if type_ == "table":
        return name in Base.metadata.tables
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
