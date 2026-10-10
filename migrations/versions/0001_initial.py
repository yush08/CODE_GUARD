"""initial schema: problems, test_cases, submissions, test_results,
behavior_fingerprints, behavior_events, security_alerts, trust_history

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-10

Portability note: only generic SQLAlchemy types are used so the exact same
migration runs on PostgreSQL 17 (the supported target). The application has
no SQLite fallback; any dialect-specific rendering below is incidental.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "problems",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=220), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_format", sa.Text(), nullable=False),
        sa.Column("output_format", sa.Text(), nullable=False),
        sa.Column("constraints", sa.Text(), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("supported_languages", sa.JSON(), nullable=False),
        sa.Column("time_limit_ms", sa.Integer(), nullable=True),
        sa.Column("memory_limit_mb", sa.Integer(), nullable=True),
        sa.Column("process_limit", sa.Integer(), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_problems_slug"),
    )
    op.create_index("ix_problems_slug", "problems", ["slug"], unique=False)
    op.create_index("ix_problems_visibility", "problems", ["visibility"], unique=False)

    op.create_table(
        "test_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("input_data", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("is_generated", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_test_cases_problem_id", "test_cases", ["problem_id"], unique=False)

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=True),
        sa.Column("workspace_label", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("compile_output", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("cpu_time_ms", sa.Integer(), nullable=True),
        sa.Column("peak_memory_bytes", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("is_custom_run", sa.Boolean(), nullable=False),
        sa.Column("custom_input", sa.Text(), nullable=True),
        sa.Column("judge_details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["problem_id"], ["problems.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_submissions_problem_id", "submissions", ["problem_id"], unique=False)
    op.create_index("ix_submissions_status", "submissions", ["status"], unique=False)
    op.create_index("ix_submissions_workspace_label", "submissions", ["workspace_label"], unique=False)
    op.create_index(
        "ix_submissions_workspace_label_created_at",
        "submissions",
        ["workspace_label", "created_at"],
        unique=False,
    )

    op.create_table(
        "test_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("test_case_id", sa.Integer(), nullable=True),
        sa.Column("test_index", sa.Integer(), nullable=False),
        sa.Column("test_is_public", sa.Boolean(), nullable=False),
        sa.Column("test_is_generated", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_bytes", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_case_id"], ["test_cases.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_test_results_submission_id", "test_results", ["submission_id"], unique=False)
    op.create_index("ix_test_results_test_case_id", "test_results", ["test_case_id"], unique=False)
    op.create_index("ix_test_results_output_hash", "test_results", ["output_hash"], unique=False)

    op.create_table(
        "behavior_fingerprints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint_json", sa.JSON(), nullable=False),
        sa.Column("measurement_source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", name="uq_behavior_fingerprints_submission_id"),
    )

    op.create_table(
        "behavior_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_behavior_events_submission_id", "behavior_events", ["submission_id"], unique=False)
    op.create_index(
        "ix_behavior_events_submission_occurred",
        "behavior_events",
        ["submission_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "security_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], ["submissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "severity IN ('INFO','LOW','MEDIUM','HIGH')",
            name="ck_alerts_severity",
        ),
    )
    op.create_index("ix_security_alerts_submission_id", "security_alerts", ["submission_id"], unique=False)
    op.create_index(
        "ix_security_alerts_review_status",
        "security_alerts",
        ["review_status", "created_at"],
        unique=False,
    )

    op.create_table(
        "trust_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_label", sa.String(length=120), nullable=False),
        sa.Column("previous_score", sa.Integer(), nullable=False),
        sa.Column("new_score", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=60), nullable=False),
        sa.Column("related_submission_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("new_score >= 0 AND new_score <= 100", name="ck_trust_score_range"),
        sa.ForeignKeyConstraint(["related_submission_id"], ["submissions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_label",
            "related_submission_id",
            "reason",
            name="uq_trust_history_label_submission_reason",
        ),
    )
    op.create_index("ix_trust_history_workspace_label", "trust_history", ["workspace_label"], unique=False)


def downgrade() -> None:
    op.drop_table("trust_history")
    op.drop_table("security_alerts")
    op.drop_table("behavior_events")
    op.drop_table("behavior_fingerprints")
    op.drop_table("test_results")
    op.drop_table("submissions")
    op.drop_table("test_cases")
    op.drop_table("problems")
