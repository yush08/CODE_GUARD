"""Judge pipeline: compile → run tests → compare → analyze.

``judge_submission`` is deliberately DB-free: it takes source code and test
specs and returns a complete in-memory ``JudgeOutcome``. Persistence is the
worker's job (app/workers/execution_worker.py), which keeps the pipeline unit
testable without a database.

Execution policy resolution (resolve_execution_policy) takes only the
problem limits and server settings. Trust scores can never modify it — core
protection is identical for every submission, by design and by test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from app.core.config import Settings, get_settings
from app.core.constants import (
    AnalysisType,
    EventSource,
    EventType,
    Language,
    SubmissionStatus,
    TestStatus,
)
from app.core.exceptions import SourceTooLargeError, UnsupportedLanguageError
from app.services import fingerprint_service, hardcoding_analyzer, sequence_analyzer, trust_service
from app.services.behavior_monitor import BehaviorMonitor
from app.services.compilation_service import (
    CompileOutcome,
    CompilationService,
    EXECUTABLE_NAME,
)
from app.services.execution_service import ExecutionPolicy, RunResult, run_program
from app.services.filesystem_isolation import (
    Workspace,
    WorkspaceManager,
    harden_workspace_acl,
)

logger = logging.getLogger(__name__)

#: Map a per-test failure to the submission-level status name (identical
#: strings by design so DB and API stay consistent).
_TEST_TO_SUBMISSION = {
    TestStatus.WRONG_ANSWER: SubmissionStatus.WRONG_ANSWER,
    TestStatus.RUNTIME_ERROR: SubmissionStatus.RUNTIME_ERROR,
    TestStatus.TIME_LIMIT_EXCEEDED: SubmissionStatus.TIME_LIMIT_EXCEEDED,
    TestStatus.MEMORY_LIMIT_EXCEEDED: SubmissionStatus.MEMORY_LIMIT_EXCEEDED,
    TestStatus.PROCESS_LIMIT_EXCEEDED: SubmissionStatus.PROCESS_LIMIT_EXCEEDED,
}

#: NTSTATUS values seen when a console program cannot load a runtime DLL
#: (shown as signed 32-bit by Python's subprocess).
DLL_FAILURE_EXIT_CODES = {-1073741515, -1073741514}  # 0xC0000135, 0xC0000142


@dataclass(frozen=True)
class ProblemLimits:
    """Per-problem limits (NULL fields fall back to server defaults)."""

    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    process_limit: int | None = None


def resolve_execution_policy(
    problem_limits: ProblemLimits | None,
    settings: Settings | None = None,
) -> ExecutionPolicy:
    """Turn problem + server configuration into a concrete policy.

    NOTE: this function intentionally accepts nothing but limits/settings.
    There is no trust input anywhere in the enforcement path.
    """
    settings = settings or get_settings()
    time_limit_ms = (
        problem_limits.time_limit_ms
        if problem_limits and problem_limits.time_limit_ms
        else int(settings.default_execution_timeout_seconds * 1000)
    )
    memory_mb = (
        problem_limits.memory_limit_mb
        if problem_limits and problem_limits.memory_limit_mb
        else settings.default_memory_limit_mb
    )
    process_limit = (
        problem_limits.process_limit
        if problem_limits and problem_limits.process_limit
        else settings.default_active_process_limit
    )
    time_limit_ms = max(50, min(time_limit_ms, settings.max_allowed_time_limit_ms))
    memory_mb = max(8, min(memory_mb, settings.max_allowed_memory_limit_mb))
    process_limit = max(1, min(process_limit, settings.max_allowed_process_limit))
    return ExecutionPolicy(
        timeout_ms=time_limit_ms,
        memory_limit_bytes=memory_mb * 1024 * 1024,
        active_process_limit=process_limit,
        max_output_bytes=settings.max_output_size_bytes,
    )


# ---------------------------------------------------------------------------
# Output comparison (standard online-judge semantics)
# ---------------------------------------------------------------------------
def normalize_output(text: str) -> str:
    """CRLF -> LF, strip trailing spaces per line, drop trailing blank lines."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)


# ---------------------------------------------------------------------------
# Input/output types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TestCaseSpec:
    id: int | None
    index: int  # 1-based for problem tests; 0 for a custom-input run
    input_data: str
    expected_output: str | None  # None => custom run, no comparison
    is_public: bool = False
    is_generated: bool = False
    label: str | None = None


@dataclass
class TestRunOutcome:
    test_case_id: int | None
    test_index: int
    is_public: bool
    is_generated: bool
    status: TestStatus
    run: RunResult
    output_summary: str
    output_hash: str
    error_summary: str | None


@dataclass
class JudgeOutcome:
    status: SubmissionStatus
    error_summary: str | None
    compile: CompileOutcome | None
    tests: list[TestRunOutcome]
    fingerprint: dict[str, Any]
    sequence_analysis: dict[str, Any]
    hardcoding: dict[str, Any] | None
    trust_factors: list[str]
    policy: ExecutionPolicy
    acl_detail: str = ""
    cleanup_ok: bool = True
    cleanup_detail: str = ""
    workspace_name: str = ""
    monitor: BehaviorMonitor | None = field(default=None, repr=False)


def _parse_language(language: str) -> Language:
    try:
        return Language(language.lower())
    except ValueError as exc:
        raise UnsupportedLanguageError(
            f"Unsupported language {language!r}. Supported: "
            f"{', '.join(l.value for l in Language)}."
        ) from exc


def classify_run(run: RunResult, expected: str | None) -> tuple[TestStatus, str | None]:
    """Classify one test execution. Order matters: resource violations beat
    output correctness; a crash beats a wrong answer."""
    if run.spawn_error:
        return TestStatus.RUNTIME_ERROR, f"Program could not be started ({run.spawn_error})."
    if run.timed_out:
        return (
            TestStatus.TIME_LIMIT_EXCEEDED,
            f"Exceeded the wall-clock deadline ({run.duration_ms} ms).",
        )
    if run.cpu_time_exceeded:
        return (
            TestStatus.TIME_LIMIT_EXCEEDED,
            f"Exceeded the CPU-time budget ({run.cpu_time_ms} ms).",
        )
    if run.memory_limit_hit:
        return (
            TestStatus.MEMORY_LIMIT_EXCEEDED,
            f"Reached the configured memory limit (peak {run.peak_memory_bytes} bytes).",
        )
    if run.process_limit_hit:
        return (
            TestStatus.PROCESS_LIMIT_EXCEEDED,
            "Exceeded the configured active-process limit; the tree was terminated.",
        )
    if run.output_limit_hit:
        return (
            TestStatus.RUNTIME_ERROR,
            "Output limit exceeded; the program was terminated.",
        )
    if run.exit_code in DLL_FAILURE_EXIT_CODES:
        return (
            TestStatus.RUNTIME_ERROR,
            "Program could not load a required runtime DLL "
            "(0x%08X) — missing compiler runtime in the execution environment."
            % (run.exit_code & 0xFFFFFFFF),
        )
    if run.exit_code != 0:
        tail = (run.stderr or "").strip()[-400:]
        return (
            TestStatus.RUNTIME_ERROR,
            f"Exited with code {run.exit_code}." + (f" stderr: {tail}" if tail else ""),
        )
    if expected is not None and not outputs_match(run.stdout, expected):
        return TestStatus.WRONG_ANSWER, "Output did not match the expected answer."
    return TestStatus.ACCEPTED, None


def judge_submission(
    source_code: str,
    language: str,
    test_cases: Sequence[TestCaseSpec],
    *,
    settings: Settings | None = None,
    problem_limits: ProblemLimits | None = None,
    workspace_label: str = "local-workspace",
    submission_id: int | str | None = None,
    on_progress: Callable[[SubmissionStatus], None] | None = None,
    enable_hardcoding_analysis: bool = True,
) -> JudgeOutcome:
    """Compile, execute, evaluate, and analyze one submission. DB-free."""
    settings = settings or get_settings()
    lang = _parse_language(language)

    if len(source_code.encode("utf-8")) > settings.max_source_size_bytes:
        raise SourceTooLargeError(
            f"Source code exceeds the {settings.max_source_size_bytes}-byte limit."
        )

    policy = resolve_execution_policy(problem_limits, settings)
    ws_manager = WorkspaceManager(settings)
    workspace = ws_manager.create(workspace_label, submission_id)
    compilation = CompilationService(settings)
    monitor = BehaviorMonitor()

    def progress(status: SubmissionStatus) -> None:
        if on_progress is not None:
            try:
                on_progress(status)
            except Exception:  # pragma: no cover - progress must never break judging
                logger.warning("Progress callback failed", exc_info=True)

    compile_outcome: CompileOutcome | None = None
    tests: list[TestRunOutcome] = []
    acl_detail = "not attempted"
    final_status = SubmissionStatus.INTERNAL_ERROR
    error_summary: str | None = None

    try:
        acl_ok, acl_detail = harden_workspace_acl(workspace.path)
        if not acl_ok:
            logger.info("Workspace ACL hardening unavailable: %s", acl_detail)

        progress(SubmissionStatus.COMPILING)
        compile_outcome = compilation.compile(workspace, source_code, lang)
        if not compile_outcome.success:
            final_status = SubmissionStatus.COMPILATION_ERROR
            error_summary = {
                "compiler_unavailable": "No usable compiler is configured on the server.",
                "source_too_large": "Source code exceeds the server size limit.",
                "compilation_timeout": "Compilation exceeded the server time limit.",
            }.get(compile_outcome.failure_reason or "", "Compilation failed.")
            monitor.record(
                EventType.EXECUTION_ERROR,
                source=EventSource.OBSERVED,
                details={"phase": "compile", "reason": compile_outcome.failure_reason},
            )
        else:
            progress(SubmissionStatus.RUNNING)
            exe_path: Path = compile_outcome.executable_path  # type: ignore[assignment]
            compiler_bin = compilation.compiler_bin_dir(lang)
            extra_paths = [compiler_bin] if compiler_bin else None
            for spec in test_cases:
                run, run_monitor = run_program(
                    workspace,
                    exe_path,
                    spec.input_data,
                    policy,
                    language=lang.value,
                    settings=settings,
                    monitor=monitor,  # share one chronological event stream
                    extra_env_paths=extra_paths,
                )
                status, test_error = classify_run(run, spec.expected_output)
                tests.append(
                    TestRunOutcome(
                        test_case_id=spec.id,
                        test_index=spec.index,
                        is_public=spec.is_public,
                        is_generated=spec.is_generated,
                        status=status,
                        run=run,
                        output_summary=fingerprint_service_summarize(run.stdout),
                        output_hash=hardcoding_analyzer.hash_output(normalize_output(run.stdout)),
                        error_summary=test_error,
                    )
                )

            first_failure = next(
                (t for t in tests if t.status != TestStatus.ACCEPTED), None
            )
            final_status = (
                _TEST_TO_SUBMISSION[first_failure.status]
                if first_failure
                else SubmissionStatus.ACCEPTED
            )
            error_summary = first_failure.error_summary if first_failure else None

        progress(SubmissionStatus.ANALYZING)
    finally:
        cleanup_ok, cleanup_detail = ws_manager.cleanup(workspace)

    # -- analysis modules ---------------------------------------------------
    fingerprint = fingerprint_service.build_fingerprint(
        submission_id,
        monitor,
        [t.run for t in tests],
        [t.status.value for t in tests],
    )
    sequence = sequence_analyzer.analyze(monitor.events()).as_dict()

    hardcoding_dict: dict[str, Any] | None = None
    hardcoding_finding = None
    if enable_hardcoding_analysis and test_cases and any(
        (not t.is_public) or t.is_generated for t in test_cases
    ):
        observations = [
            hardcoding_analyzer.TestObservation(
                test_index=t.test_index,
                status=t.status.value,
                is_public=t.is_public,
                is_generated=t.is_generated,
                output_hash=t.output_hash,
                input_hash=hardcoding_analyzer.hash_output(
                    _spec_by_index(test_cases, t.test_index).input_data
                ),
                expected_hash=hardcoding_analyzer.hash_output(
                    _spec_by_index(test_cases, t.test_index).expected_output or ""
                ),
                label=_spec_by_index(test_cases, t.test_index).label,
            )
            for t in tests
        ]
        hardcoding_finding = hardcoding_analyzer.analyze(observations)
        hardcoding_dict = hardcoding_finding.as_dict()

    analysis_risk = sequence_analyzer.RiskLevel(sequence["risk_level"])
    trust_factors = trust_service.factors_for_judge_outcome(
        final_status=final_status.value,
        any_time_limit=any(t.run.timed_out or t.run.cpu_time_exceeded for t in tests),
        any_memory_limit=any(t.run.memory_limit_hit for t in tests),
        any_process_limit=any(t.run.process_limit_hit for t in tests),
        any_output_limit=any(t.run.output_limit_hit for t in tests),
        sequence_risk=analysis_risk,
    )

    return JudgeOutcome(
        status=final_status,
        error_summary=error_summary,
        compile=compile_outcome,
        tests=tests,
        fingerprint=fingerprint,
        sequence_analysis=sequence,
        hardcoding=hardcoding_dict,
        trust_factors=trust_factors,
        policy=policy,
        acl_detail=acl_detail,
        cleanup_ok=cleanup_ok,
        cleanup_detail=cleanup_detail,
        workspace_name=workspace.path.name,
        monitor=monitor,
    )


def _spec_by_index(tests: Sequence[TestCaseSpec], index: int) -> TestCaseSpec:
    for spec in tests:
        if spec.index == index:
            return spec
    raise KeyError(index)


def fingerprint_service_summarize(output: str) -> str:
    return hardcoding_analyzer.summarize_output(normalize_output(output))
