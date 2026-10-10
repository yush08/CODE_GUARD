"""Analysis endpoints: fingerprint, events, sequence, hardcoding, report.

These endpoints expose what the backend actually produced for a stored
submission. Missing data is reported as null / UNAVAILABLE — never invented.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.constants import AnalysisType, EventType, EventSource, Severity
from app.core.exceptions import NotFoundError
from app.models.behavior_event import BehaviorEvent
from app.models.behavior_fingerprint import BehaviorFingerprint
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.schemas.analysis import (
    FingerprintResponse,
    HardcodingAnalysisResponse,
    ReportResponse,
    SequenceAnalysisResponse,
    EventResponse,
)
from app.schemas.common import Page, build_page
from app.services import hardcoding_analyzer, sequence_analyzer
from app.services.behavior_monitor import EventRecord
from app.services.fingerprint_service import compare_fingerprints
from app.services.report_service import build_submission_report

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _get_submission(session: Session, submission_id: int) -> Submission:
    sub = session.get(Submission, submission_id)
    if sub is None:
        raise NotFoundError(f"Submission {submission_id} does not exist.")
    return sub


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
@router.get(
    "/submissions/{submission_id}/fingerprint",
    response_model=FingerprintResponse,
    summary="Behavior fingerprint of one submission",
)
def get_fingerprint(
    submission_id: int, session: Session = Depends(get_db)
) -> FingerprintResponse:
    _get_submission(session, submission_id)
    row = session.scalar(
        select(BehaviorFingerprint).where(
            BehaviorFingerprint.submission_id == submission_id
        )
    )
    if row is None:
        raise NotFoundError(
            "No behavior fingerprint exists for this submission yet (it may "
            "still be queued/running, or it failed before execution started)."
        )
    data = row.fingerprint_json or {}
    return FingerprintResponse(
        submission_id=data.get("submission_id", submission_id),
        analysis_type=data.get("analysis_type", AnalysisType.FINGERPRINT.value),
        measurement_source=row.measurement_source,
        fingerprint=data.get("fingerprint", {}),
        measurements=data.get("measurements", {}),
        notes=data.get("notes", []),
    )


@router.get(
    "/fingerprints/compare",
    summary="Informational comparison of two fingerprints (not a plagiarism verdict)",
)
def compare(left_id: int = Query(..., ge=1), right_id: int = Query(..., ge=1), session: Session = Depends(get_db)):
    rows = session.scalars(
        select(BehaviorFingerprint).where(
            BehaviorFingerprint.submission_id.in_([left_id, right_id])
        )
    ).all()
    by_sub = {r.submission_id: r for r in rows}
    missing = [i for i in (left_id, right_id) if i not in by_sub]
    if missing:
        raise NotFoundError(
            "No fingerprint recorded for submission(s): "
            + ", ".join(str(m) for m in missing)
        )
    comparison = compare_fingerprints(
        by_sub[left_id].fingerprint_json, by_sub[right_id].fingerprint_json
    )
    return {
        "left_submission_id": left_id,
        "right_submission_id": right_id,
        "comparison": asdict(comparison),
    }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@router.get(
    "/submissions/{submission_id}/events",
    response_model=Page[EventResponse],
    summary="Chronological behavior events of one submission",
)
def list_events(
    submission_id: int,
    session: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: str | None = Query(None, description="Filter by event type."),
    source: str | None = Query(
        None, description="OBSERVED | INFERRED | SIMULATED (default: all)."
    ),
) -> Page[EventResponse]:
    _get_submission(session, submission_id)
    stmt = select(BehaviorEvent).where(BehaviorEvent.submission_id == submission_id)
    if event_type:
        stmt = stmt.where(BehaviorEvent.event_type == event_type)
    if source:
        stmt = stmt.where(BehaviorEvent.source == source)
    total = int(
        session.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
    )
    rows = list(
        session.scalars(
            stmt.order_by(BehaviorEvent.occurred_at.asc(), BehaviorEvent.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = [
        EventResponse(
            id=r.id,
            event_type=r.event_type,
            occurred_at=r.occurred_at,
            source=r.source,
            severity=r.severity,
            details=r.details_json or {},
        )
        for r in rows
    ]
    return build_page(items, total, page, page_size)


# ---------------------------------------------------------------------------
# Sequence analysis
# ---------------------------------------------------------------------------
def _records_from_rows(session: Session, submission_id: int) -> list[EventRecord]:
    rows = list(
        session.scalars(
            select(BehaviorEvent)
            .where(BehaviorEvent.submission_id == submission_id)
            .order_by(BehaviorEvent.occurred_at.asc(), BehaviorEvent.id.asc())
        )
    )
    records: list[EventRecord] = []
    for r in rows:
        try:
            event_type = EventType(r.event_type)
        except ValueError:
            continue  # unknown stored type: skip rather than invent analysis
        records.append(
            EventRecord(
                event_type=event_type,
                occurred_at=r.occurred_at,
                source=EventSource(r.source),
                severity=Severity(r.severity),
                details=r.details_json or {},
                seq=r.id,
            )
        )
    return records


@router.get(
    "/submissions/{submission_id}/sequence",
    response_model=SequenceAnalysisResponse,
    summary="Sequence-aware analysis of the recorded event stream",
)
def get_sequence(
    submission_id: int, session: Session = Depends(get_db)
) -> SequenceAnalysisResponse:
    sub = _get_submission(session, submission_id)
    details = sub.judge_details_json or {}
    payload = details.get("sequence_analysis")
    if payload is None:
        # Fallback: recompute from the stored event stream (pure function of
        # the recorded events + the currently configured rules).
        records = _records_from_rows(session, submission_id)
        payload = sequence_analyzer.analyze(records).as_dict()
    return SequenceAnalysisResponse(**payload)


# ---------------------------------------------------------------------------
# Hardcoding heuristic
# ---------------------------------------------------------------------------
def _recompute_hardcoding(session: Session, submission_id: int) -> dict:
    rows = list(
        session.scalars(
            select(TestResult)
            .where(TestResult.submission_id == submission_id)
            .order_by(TestResult.test_index)
        )
    )
    observations: list[hardcoding_analyzer.TestObservation] = []
    for r in rows:
        case = session.get(TestCase, r.test_case_id) if r.test_case_id else None
        observations.append(
            hardcoding_analyzer.TestObservation(
                test_index=r.test_index,
                status=r.status,
                is_public=r.test_is_public,
                is_generated=r.test_is_generated,
                output_hash=r.output_hash,
                input_hash=(
                    hardcoding_analyzer.hash_output(case.input_data) if case else None
                ),
                expected_hash=(
                    hardcoding_analyzer.hash_output(case.expected_output or "")
                    if case
                    else None
                ),
                label=case.label if case else None,
            )
        )
    return hardcoding_analyzer.analyze(observations).as_dict()


@router.get(
    "/submissions/{submission_id}/hardcoding",
    response_model=HardcodingAnalysisResponse,
    summary="Hardcoding/overfitting heuristic (never an accusation)",
)
def get_hardcoding(
    submission_id: int, session: Session = Depends(get_db)
) -> HardcodingAnalysisResponse:
    sub = _get_submission(session, submission_id)
    details = sub.judge_details_json or {}
    payload = details.get("hardcoding")
    if payload is None:
        payload = _recompute_hardcoding(session, submission_id)
    return HardcodingAnalysisResponse(**payload)


# ---------------------------------------------------------------------------
# Combined report
# ---------------------------------------------------------------------------
@router.get(
    "/submissions/{submission_id}/report",
    response_model=ReportResponse,
    summary="Combined stored report (status, resources, analysis, alerts, trust)",
)
def get_report(
    submission_id: int, session: Session = Depends(get_db)
) -> ReportResponse:
    sub = _get_submission(session, submission_id)
    return build_submission_report(session, sub, include_hidden_outputs=False)
