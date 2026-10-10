"""Shared pytest fixtures.

Two markers carry meaning here and are enforced for every test run:

* ``db``      — needs real PostgreSQL credentials. Skipped (with an explicit
  "pending credentials" reason) until ``DATABASE_URL`` in ``.env`` holds a
  real password. The suite never silently falls back to SQLite.
* ``windows`` — needs Windows execution primitives (Job Objects / GCC).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # pragma: no cover - pytest pythonpath also covers this
    sys.path.insert(0, str(ROOT))


def _database_usable() -> bool:
    try:
        from app.core.config import get_settings
        from app.db.session import probe_database

        return probe_database(get_settings()).is_usable
    except Exception:  # pragma: no cover - defensive
        return False


DB_USABLE = _database_usable()
IS_WINDOWS = sys.platform.startswith("win")


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(
        reason=(
            "PENDING: PostgreSQL credentials are not configured yet — set "
            "DATABASE_URL in .env (real password) and rerun. No SQLite/in-memory "
            "fallback exists by design."
        )
    )
    skip_windows = pytest.mark.skip(reason="Requires Windows execution primitives.")
    for item in items:
        if "db" in item.keywords and not DB_USABLE:
            item.add_marker(skip_db)
        if "windows" in item.keywords and not IS_WINDOWS:
            item.add_marker(skip_windows)


@pytest.fixture
def settings(tmp_path):
    """Fresh settings whose sandbox lives inside the test's temp dir."""
    from app.core.config import Settings

    return Settings(_env_file=None, sandbox_root=tmp_path / "sandbox")


@pytest.fixture(scope="module")
def client():
    """HTTP client with application lifespan (queue start/stop) handled.
    
    Uses the real application with the configured database from .env.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def unconfigured_client():
    """HTTP client for testing database-unconfigured mode.
    
    Creates a fresh application instance with no DATABASE_URL configured,
    so that all DB-dependent endpoints return 503 with the standard
    error envelope.
    """
    import os
    from fastapi.testclient import TestClient

    # Save original DATABASE_URL
    original_db_url = os.environ.get("DATABASE_URL")
    
    # Set environment variable BEFORE any app imports
    os.environ["DATABASE_URL"] = ""

    # Clear the settings cache so a new Settings instance is created
    from app.core.config import reset_settings_cache
    reset_settings_cache()

    # Clear only the modules that cache settings at import time
    import sys
    modules_to_reload = [
        'app.core.config',
        'app.db.session',
        'app.main',
        'app.api.v1.router',
        'app.api.v1.problems',
        'app.api.v1.submissions',
        'app.api.v1.faculty',
        'app.api.v1.analysis',
        'app.api.v1.system',
        'app.api.v1.health',
    ]
    for mod in modules_to_reload:
        if mod in sys.modules:
            del sys.modules[mod]

    try:
        # Now import and create the app - it will read DATABASE_URL="" from env
        from app.main import create_app
        app = create_app()

        with TestClient(app) as test_client:
            yield test_client
    finally:
        # Clean up
        from app.db.session import dispose_engine, reset_probe_cache
        dispose_engine()
        reset_probe_cache()
        # Restore DATABASE_URL
        if original_db_url is not None:
            os.environ["DATABASE_URL"] = original_db_url
        else:
            os.environ.pop("DATABASE_URL", None)
        reset_settings_cache()
        # Re-import cleared modules to restore original state
        for mod in modules_to_reload:
            if mod in sys.modules:
                del sys.modules[mod]
        reset_settings_cache()


@pytest.fixture
def fixture_source():
    """Read a demo/fixture program by file name."""
    programs = ROOT / "tests" / "fixtures" / "programs"

    def _read(name: str) -> str:
        return (programs / name).read_text(encoding="utf-8")

    return _read
