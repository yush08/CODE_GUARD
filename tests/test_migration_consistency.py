"""Static consistency checks between the ORM models and the hand-written
migration. These run WITHOUT a database, so schema drift is caught in CI-like
runs even while credentials are pending."""

from __future__ import annotations

import re
from pathlib import Path

from app.db.base import Base

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0001_initial.py"
)

EXPECTED_TABLES = {
    "problems",
    "test_cases",
    "submissions",
    "test_results",
    "behavior_fingerprints",
    "behavior_events",
    "security_alerts",
    "trust_history",
}


def _migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_all_specified_tables_exist_in_models() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables), (
        EXPECTED_TABLES - set(Base.metadata.tables)
    )


def test_migration_creates_every_model_table() -> None:
    source = _migration_source()
    for table in EXPECTED_TABLES:
        assert re.search(
            rf"op\.create_table\(\s*\"{re.escape(table)}\"", source
        ), f"table {table} missing from the initial migration"


def test_migration_declares_every_model_column() -> None:
    source = _migration_source()
    missing: list[str] = []
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        for column in table.columns:
            if f'sa.Column("{column.name}"' not in source:
                missing.append(f"{table_name}.{column.name}")
    assert not missing, f"columns missing from migration: {missing}"


def test_migration_downgrade_removes_tables() -> None:
    source = _migration_source()
    assert "def downgrade()" in source
    for table in EXPECTED_TABLES:
        assert f'op.drop_table("{table}")' in source


def test_every_foreign_key_has_a_cascade_rule() -> None:
    source = _migration_source()
    for match in re.finditer(r'sa\.ForeignKeyConstraint\((.*?)\)', source, re.S):
        fragment = match.group(1)
        assert "ondelete=" in fragment, f"FK without ondelete: {fragment[:80]}"
    for table_name in EXPECTED_TABLES:
        for fk in Base.metadata.tables[table_name].foreign_keys:
            assert fk.ondelete, f"{table_name}.{fk} has no ondelete rule"
            assert source.count('ondelete="CASCADE"') + source.count('ondelete="SET NULL"') > 0


def test_timestamps_are_timezone_aware() -> None:
    for table_name in EXPECTED_TABLES:
        for column in Base.metadata.tables[table_name].columns:
            if column.name.endswith("_at"):
                assert (
                    column.type.timezone is True
                ), f"{table_name}.{column.name} must store timezone-aware UTC"
