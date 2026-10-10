"""GET /api/v1/system/status — truthful capability reporting.

Every capability is reported by what it actually does today: ACTIVE only when
the mechanism is in use, MEASURED_ONLY when the value is observed but not
enforced, PARTIAL when it works with documented gaps, NOT_AVAILABLE when it
does not exist yet, RESERVED for schema-level placeholders.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app import __version__
from app.core.config import get_settings
from app.core.constants import Language
from app.db.session import probe_database
from app.schemas.system import (
    CapabilityInfo,
    CompilerStatus,
    DatabaseStatus,
    JobObjectStatus,
    SystemStatusResponse,
    WorkerStatus,
)

router = APIRouter(tags=["system"])


def _running_as_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover
        return False


def _capabilities(
    db_configured: bool,
    compilers_ok: bool,
    job_available: bool,
) -> list[CapabilityInfo]:
    caps: list[CapabilityInfo] = []

    caps.append(
        CapabilityInfo(
            id="problem_management",
            name="Problem & test-case management",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "CRUD over problems/test cases with hidden-test protection."
                if db_configured
                else "Requires a configured database (503 until DATABASE_URL "
                "has real credentials)."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="compilation",
            name="C/C++ compilation (local GCC/MinGW)",
            status="ACTIVE" if compilers_ok else "NOT_AVAILABLE",
            detail=(
                "Compilers are detected from PATH/environment; never downloaded."
                if compilers_ok
                else "No gcc/g++ detected — set COMPILER_C_PATH/COMPILER_CPP_PATH."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="resource_limits",
            name="Windows Job Object resource limits",
            status=(
                "ACTIVE"
                if job_available
                else ("MEASURED_ONLY" if compilers_ok else "NOT_AVAILABLE")
            ),
            detail=(
                "Wall-clock deadline and output cap enforced by the engine; "
                "memory/process limits enforced by the Job Object when "
                "available, otherwise measured and reported as MEASURED_ONLY. "
                "CPU usage is MEASURED_ONLY: the kernel per-job CPU-time cap "
                "(JOB_OBJECT_LIMIT_JOB_TIME) is configured but was verified "
                "unreliable on this machine (fires late or not at all), so "
                "CPU-bound programs are stopped by the wall-clock deadline."
            )
            if job_available
            else (
                "Job Objects unavailable: limits are enforced by the "
                "monitor/termination path and reported per-limit."
                if compilers_ok
                else "No execution engine without a compiler."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="behavior_monitoring",
            name="Runtime behavior monitoring",
            status="ACTIVE" if job_available or compilers_ok else "NOT_AVAILABLE",
            detail=(
                "PROCESS_START/EXIT, CHILD_PROCESS_CREATED, limit events, "
                "workspace file events with OBSERVED/INFERRED sources. "
                "ACCESS_DENIED is reserved and not emitted yet."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="fingerprinting",
            name="Behavior fingerprinting",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "Per-submission metrics with explicit availability map; "
                "unavailable metrics stay null."
                if db_configured
                else "Analysis is persisted with the submission (needs database)."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="sequence_analysis",
            name="Sequence-aware event analysis",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "Ordered-event rules with explanations; matches create "
                "reviewable alerts."
                if db_configured
                else "Rules run during judging and persist alerts (needs database)."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="hardcoding_detection",
            name="Hardcoding heuristics",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "NO_SIGNAL / REVIEW_RECOMMENDED / INSUFFICIENT_DATA with "
                "evidence text; confidence is always null and no accusations "
                "are made."
                if db_configured
                else "Results are stored with the submission (needs database)."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="adaptive_trust",
            name="Adaptive trust scoring",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "Auditable per-workspace history with dedupe; never weakens "
                "resource/security controls."
                if db_configured
                else "Trust persistence requires the database."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="faculty_reporting",
            name="Faculty dashboard & reporting APIs",
            status="ACTIVE" if db_configured else "NOT_AVAILABLE",
            detail=(
                "Aggregates, alerts, review workflow, trust history."
                if db_configured
                else "All faculty endpoints return 503 in unconfigured mode."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="auth_rbac",
            name="Authentication / RBAC",
            status="PARTIAL",
            detail=(
                "auth_mode=local_dev: an Actor seam exists (X-Workspace-Label, "
                "X-Actor-Role) but NO credentials are verified. Real login/"
                "roles are not implemented."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="filesystem_isolation",
            name="Filesystem isolation",
            status="PARTIAL",
            detail=(
                "Per-run workspace directories, deny-ACL helper for isolated "
                "paths, and workspace file-event monitoring. This is NOT a "
                "full sandbox: no filesystem virtualization, and "
                "ACCESS_DENIED events are not emitted yet."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="network_isolation",
            name="Network isolation",
            status="NOT_AVAILABLE",
            detail=(
                "No network blocking is implemented. Job Objects do not "
                "restrict network access."
            ),
        )
    )
    caps.append(
        CapabilityInfo(
            id="security_violation_status",
            name="SECURITY_VIOLATION submission status",
            status="RESERVED",
            detail=(
                "The status exists in the model but no detector currently "
                "emits it — documented honestly rather than claimed."
            ),
        )
    )
    return caps


@router.get(
    "/system/status",
    response_model=SystemStatusResponse,
    summary="Truthful system capability status",
)
def system_status(
    refresh: bool = Query(
        False,
        description="Re-run compiler and Job Object capability probes now "
        "instead of using the cached result.",
    ),
) -> SystemStatusResponse:
    settings = get_settings()
    probe = probe_database(settings)
    db_configured = probe.config_state.value == "CONFIGURED"

    from app.services.compilation_service import CompilationService

    compilers = CompilationService(settings).detect_all(force=refresh)
    compilers_ok = all(c.available for c in compilers.values())

    from app.services.windows_job_manager import probe_job_objects

    job_ok, job_detail = probe_job_objects(force=refresh)
    from app.services.execution_queue import execution_queue

    snap = execution_queue.snapshot()

    warnings: list[str] = []
    if not db_configured:
        warnings.append(
            "Database-unconfigured development mode: DB-dependent endpoints "
            f"return 503. {probe.reason}"
        )
    if not compilers_ok:
        warnings.append("One or more compilers were not detected.")
    if not job_ok:
        warnings.append(
            "Windows Job Objects unavailable; resource limits fall back to "
            "the measurement/termination path (per-limit statuses apply)."
        )
    if _running_as_admin():
        warnings.append(
            "The backend is running elevated (as Administrator). Student code "
            "should never be executed as admin — run the server as a normal "
            "user."
        )
    warnings.append(
        "Job Objects are not a full sandbox: no filesystem/network "
        "virtualization."
    )

    return SystemStatusResponse(
        app=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        os={
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        python={
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        auth_mode=settings.auth_mode,
        running_as_admin=_running_as_admin(),
        database=DatabaseStatus(
            configured=db_configured,
            config_state=probe.config_state.value,
            connectivity=probe.connectivity.value,
            reason=probe.reason,
            display_url=settings.database_url_display(),
        ),
        compilers=[
            CompilerStatus(**compilers[Language.C.value].as_dict()),
            CompilerStatus(**compilers[Language.CPP.value].as_dict()),
        ],
        supported_languages=[Language.C.value, Language.CPP.value],
        job_object=JobObjectStatus(available=job_ok, detail=job_detail),
        execution_worker=WorkerStatus(
            running=bool(snap.get("running")),
            active_executions=int(snap.get("in_flight_submissions", 0)),
            queued_jobs=int(snap.get("queued_jobs", 0)),
            max_concurrent_executions=int(snap.get("max_concurrent_executions", 0)),
            max_queued_jobs=int(snap.get("max_queued_jobs", 0)),
        ),
        capabilities=_capabilities(db_configured, compilers_ok, job_ok),
        warnings=warnings,
    )


def timestamp_now() -> datetime:  # pragma: no cover - helper for routes
    return datetime.now(timezone.utc)
