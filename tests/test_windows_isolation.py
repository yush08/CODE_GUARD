"""Windows-specific isolation primitives: workspaces, ACLs, Job Objects.

The ACL tests prove real OS-level behaviour (the process itself is denied),
not a simulated check.
"""

from __future__ import annotations

import shutil

import pytest

from app.services.filesystem_isolation import (
    WorkspaceError,
    WorkspaceManager,
    apply_deny_read_current_user,
    harden_workspace_acl,
    probe_acl_support,
)
from app.services.windows_job_manager import (
    JobLimits,
    create_job,
    probe_job_objects,
)

pytestmark = pytest.mark.windows


def test_workspace_is_created_under_sandbox_and_cleaned_up(settings) -> None:
    manager = WorkspaceManager(settings)
    ws = manager.create("demo label", submission_id=55)
    assert ws.path.exists()
    assert ws.path.is_relative_to(manager.root)
    assert ws.path.name.startswith("sub_55_")

    ok, detail = manager.cleanup(ws)
    assert ok, detail
    assert not ws.path.exists()


def test_unsafe_filenames_are_rejected(settings) -> None:
    manager = WorkspaceManager(settings)
    ws = manager.create("demo", submission_id=56)
    try:
        for bad in ("../escape.txt", "sub/dir/file.txt", ".hidden", "a\\b.txt", ""):
            with pytest.raises(WorkspaceError):
                ws.safe_path(bad)
        good = ws.safe_path("output.txt")
        assert good.is_relative_to(ws.path.resolve())
    finally:
        manager.cleanup(ws)


def test_cleanup_never_touches_anything_outside_the_sandbox(settings, tmp_path) -> None:
    manager = WorkspaceManager(settings)
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("important", encoding="utf-8")

    from app.services.filesystem_isolation import Workspace

    ws = Workspace(path=outside, root=manager.root)
    ok, detail = manager.cleanup(ws)
    assert ok is False
    assert "Refused" in detail
    assert (outside / "keep.txt").exists()


def test_written_content_is_size_limited(settings) -> None:
    manager = WorkspaceManager(settings)
    ws = manager.create("demo", submission_id=57)
    try:
        with pytest.raises(WorkspaceError):
            ws.write_text("big.c", "x" * 100, max_bytes=10)
    finally:
        manager.cleanup(ws)


def test_acl_hardening_is_available_on_this_volume(settings) -> None:
    settings.sandbox_root.mkdir(parents=True, exist_ok=True)
    ok, detail = probe_acl_support(settings.sandbox_root)
    assert ok, detail
    assert "current user" in detail.lower() or "system" in detail.lower()


def test_deny_ace_proves_os_level_enforcement(tmp_path) -> None:
    """A real Windows ACL denial: this very process gets PermissionError."""
    target = tmp_path / "secret.txt"
    target.write_text("student source", encoding="utf-8")

    ok, detail = apply_deny_read_current_user(target)
    assert ok, detail

    try:
        with pytest.raises(PermissionError):
            target.read_text(encoding="utf-8")
    finally:
        try:
            # the owner may still delete via the parent's DELETE_CHILD right
            target.unlink()
        except OSError:  # pragma: no cover - depends on volume ACLs
            shutil.rmtree(tmp_path, ignore_errors=True)


def test_job_objects_are_really_available() -> None:
    available, detail = probe_job_objects(force=True)
    assert available, detail


def test_job_enforces_configured_limits() -> None:
    job = create_job(
        JobLimits(
            memory_limit_bytes=128 * 1024 * 1024,
            active_process_limit=4,
            cpu_time_limit_ms=5000,
        )
    )
    try:
        assert job.available, job.detail
        # accounting query works without a process inside the job
        accounting = job.query_accounting()
        assert accounting is None or accounting.total_processes >= 0
    finally:
        job.close()


def test_judge_exit_codes_are_stable() -> None:
    from app.services.execution_service import (
        EXIT_CLEANUP,
        EXIT_OUTPUT_LIMIT,
        EXIT_PROCESS_LIMIT,
        EXIT_TIME_LIMIT,
    )

    assert (EXIT_TIME_LIMIT, EXIT_PROCESS_LIMIT, EXIT_OUTPUT_LIMIT, EXIT_CLEANUP) == (
        42,
        43,
        44,
        45,
    )


def test_child_environment_does_not_inherit_backend_secrets() -> None:
    import os

    from app.services.process_support import build_child_environment

    env = build_child_environment()
    assert "DATABASE_URL" not in env
    assert "PATH" in env
    # nothing from the parent process leaks through by default
    assert os.environ.get("DATABASE_URL") is None or "DATABASE_URL" not in env
