"""Application-wide constants and enumerations.

All enumerations are string-valued so they serialize cleanly to JSON and are
stored as plain strings in PostgreSQL (portable, human readable in psql).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Judge domain enums
# ---------------------------------------------------------------------------
class Language(str, Enum):
    C = "c"
    CPP = "cpp"


class SubmissionStatus(str, Enum):
    QUEUED = "QUEUED"
    COMPILING = "COMPILING"
    RUNNING = "RUNNING"
    ANALYZING = "ANALYZING"
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    COMPILATION_ERROR = "COMPILATION_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    MEMORY_LIMIT_EXCEEDED = "MEMORY_LIMIT_EXCEEDED"
    PROCESS_LIMIT_EXCEEDED = "PROCESS_LIMIT_EXCEEDED"
    SECURITY_VIOLATION = "SECURITY_VIOLATION"  # reserved; see README feature status
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


#: Statuses that mean "no more processing will happen".
TERMINAL_STATUSES: frozenset[SubmissionStatus] = frozenset(
    {
        SubmissionStatus.ACCEPTED,
        SubmissionStatus.WRONG_ANSWER,
        SubmissionStatus.COMPILATION_ERROR,
        SubmissionStatus.RUNTIME_ERROR,
        SubmissionStatus.TIME_LIMIT_EXCEEDED,
        SubmissionStatus.MEMORY_LIMIT_EXCEEDED,
        SubmissionStatus.PROCESS_LIMIT_EXCEEDED,
        SubmissionStatus.SECURITY_VIOLATION,
        SubmissionStatus.CANCELLED,
        SubmissionStatus.INTERNAL_ERROR,
    }
)

#: Statuses a test result may carry.
class TestStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    WRONG_ANSWER = "WRONG_ANSWER"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    TIME_LIMIT_EXCEEDED = "TIME_LIMIT_EXCEEDED"
    MEMORY_LIMIT_EXCEEDED = "MEMORY_LIMIT_EXCEEDED"
    PROCESS_LIMIT_EXCEEDED = "PROCESS_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# Behavior monitoring enums
# ---------------------------------------------------------------------------
class EventType(str, Enum):
    """Normalized runtime event types.

    Only event types that the current implementation can genuinely observe are
    emitted today. ACCESS_DENIED is reserved for a future access-monitoring
    provider and is never fabricated.
    """

    PROCESS_START = "PROCESS_START"
    PROCESS_EXIT = "PROCESS_EXIT"
    CHILD_PROCESS_CREATED = "CHILD_PROCESS_CREATED"
    PROCESS_LIMIT_EVENT = "PROCESS_LIMIT_EVENT"
    TIME_LIMIT_EVENT = "TIME_LIMIT_EVENT"
    MEMORY_LIMIT_EVENT = "MEMORY_LIMIT_EVENT"
    WORKSPACE_FILE_CREATED = "WORKSPACE_FILE_CREATED"
    ACCESS_DENIED = "ACCESS_DENIED"  # reserved: no provider emits it yet
    OUTPUT_LIMIT_EVENT = "OUTPUT_LIMIT_EVENT"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class EventSource(str, Enum):
    """Provenance of an observation or finding."""

    OBSERVED = "OBSERVED"      # directly measured/recorded by the engine
    INFERRED = "INFERRED"      # derived from observations by a rule/heuristic
    SIMULATED = "SIMULATED"    # demo/synthetic data — never used for real runs


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AlertType(str, Enum):
    SEQUENCE_RULE_MATCH = "SEQUENCE_RULE_MATCH"
    POSSIBLE_HARDCODING = "POSSIBLE_HARDCODING"
    RESOURCE_VIOLATION = "RESOURCE_VIOLATION"
    TRUST_THRESHOLD = "TRUST_THRESHOLD"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class HardcodingStatus(str, Enum):
    NO_SIGNAL = "NO_SIGNAL"
    REVIEW_RECOMMENDED = "REVIEW_RECOMMENDED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class AnalysisType(str, Enum):
    FINGERPRINT = "FINGERPRINT"
    SEQUENCE_ANALYSIS = "SEQUENCE_ANALYSIS"
    HARDCODING_HEURISTIC = "HARDCODING_HEURISTIC"
    TRUST_ASSESSMENT = "TRUST_ASSESSMENT"


# ---------------------------------------------------------------------------
# Problem enums
# ---------------------------------------------------------------------------
class Visibility(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


class Difficulty(str, Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


# ---------------------------------------------------------------------------
# System / capability reporting
# ---------------------------------------------------------------------------
class CapabilityStatus(str, Enum):
    """Truthful capability reporting for system/status."""

    ACTIVE = "ACTIVE"                # enforced by the OS, verified by tests
    MEASURED_ONLY = "MEASURED_ONLY"  # measured but NOT enforced
    PARTIAL = "PARTIAL"              # enforced with documented caveats
    NOT_AVAILABLE = "NOT_AVAILABLE"  # not implemented / unavailable here
    RESERVED = "RESERVED"            # defined for the future, never emitted yet


class EnforcementKind(str, Enum):
    ENFORCED = "ENFORCED"
    MEASURED = "MEASURED"
    UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Sequence rules (transparent, configurable baseline patterns)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SequenceRule:
    """A rule matches when the ordered event-type sequence contains the
    pattern as a contiguous subsequence (evaluated chronologically)."""

    rule_id: str
    pattern: tuple[EventType, ...]
    risk_level: RiskLevel
    explanation: str
    creates_alert: bool = False


#: Default baseline rules. More rules may be supplied via
#: SEQUENCE_RULES_PATH (a JSON file with the same structure) without code
#: changes; see README.
DEFAULT_SEQUENCE_RULES: tuple[SequenceRule, ...] = (
    SequenceRule(
        rule_id="NORMAL_SINGLE_PROCESS",
        pattern=(EventType.PROCESS_START, EventType.PROCESS_EXIT),
        risk_level=RiskLevel.NONE,
        explanation=(
            "Baseline normal pattern: a single process started and exited "
            "without any recorded limit violation or child-process activity."
        ),
    ),
    SequenceRule(
        rule_id="WORKSPACE_WRITES",
        pattern=(
            EventType.PROCESS_START,
            EventType.WORKSPACE_FILE_CREATED,
            EventType.PROCESS_EXIT,
        ),
        risk_level=RiskLevel.LOW,
        explanation=(
            "Program wrote files inside its own execution workspace. This is "
            "normal for many programs; monitored only at workspace scope."
        ),
    ),
    SequenceRule(
        rule_id="CHILD_PROCESS_ACTIVITY",
        pattern=(EventType.PROCESS_START, EventType.CHILD_PROCESS_CREATED),
        risk_level=RiskLevel.LOW,
        explanation=(
            "Program created at least one child process. Not malicious by "
            "itself; recorded because child processes expand the blast radius."
        ),
    ),
    SequenceRule(
        rule_id="REPEATED_CHILD_PROCESS_CREATION",
        pattern=(
            EventType.PROCESS_START,
            EventType.CHILD_PROCESS_CREATED,
            EventType.CHILD_PROCESS_CREATED,
            EventType.PROCESS_LIMIT_EVENT,
        ),
        risk_level=RiskLevel.MEDIUM,
        explanation=(
            "Repeated process creation reached the configured active-process "
            "limit. The kernel blocked further creation and the judge "
            "terminated the process tree."
        ),
        creates_alert=True,
    ),
    SequenceRule(
        rule_id="TIME_LIMIT_TERMINATION",
        pattern=(EventType.TIME_LIMIT_EVENT, EventType.PROCESS_EXIT),
        risk_level=RiskLevel.MEDIUM,
        explanation=(
            "Execution exceeded the configured wall-clock/CPU deadline and was "
            "terminated by the judge. Common for infinite loops; not proof of "
            "misconduct."
        ),
        creates_alert=True,
    ),
    SequenceRule(
        rule_id="MEMORY_LIMIT_TERMINATION",
        pattern=(EventType.MEMORY_LIMIT_EVENT, EventType.PROCESS_EXIT),
        risk_level=RiskLevel.MEDIUM,
        explanation=(
            "Program reached the configured job memory limit and was "
            "terminated or failed allocations. Not proof of misconduct."
        ),
        creates_alert=True,
    ),
    SequenceRule(
        rule_id="OUTPUT_LIMIT_STORM",
        pattern=(EventType.OUTPUT_LIMIT_EVENT, EventType.PROCESS_EXIT),
        risk_level=RiskLevel.MEDIUM,
        explanation=(
            "Program produced more output than the configured limit within a "
            "single test run and was terminated."
        ),
        creates_alert=True,
    ),
    SequenceRule(
        rule_id="PROCESS_LIMIT_THEN_TERMINATION",
        pattern=(EventType.PROCESS_LIMIT_EVENT, EventType.PROCESS_EXIT),
        risk_level=RiskLevel.HIGH,
        explanation=(
            "The configured active-process limit was exceeded and the process "
            "tree was terminated by the judge."
        ),
        creates_alert=True,
    ),
)


# ---------------------------------------------------------------------------
# Adaptive trust scoring
# ---------------------------------------------------------------------------
INITIAL_TRUST_SCORE = 80
TRUST_SCORE_MIN = 0
TRUST_SCORE_MAX = 100


@dataclass(frozen=True)
class TrustFactor:
    factor: str
    delta: int
    description: str


#: The scoring formula is deliberately plain and auditable.
#: A submission can contribute at most one entry per factor (deduplicated per
#: submission), so a single run never double-penalises.
TRUST_FACTORS: dict[str, TrustFactor] = {f.factor: f for f in (
    TrustFactor("CLEAN_ACCEPTED", 2,
                "Accepted with no resource-limit violation (capped at max)."),
    TrustFactor("TIME_LIMIT_VIOLATION", -5,
                "Execution hit the configured time limit (buggy or runaway code)."),
    TrustFactor("MEMORY_LIMIT_VIOLATION", -5,
                "Execution hit the configured memory limit."),
    TrustFactor("OUTPUT_LIMIT_VIOLATION", -5,
                "Execution exceeded the configured output limit."),
    TrustFactor("PROCESS_LIMIT_VIOLATION", -15,
                "Execution exceeded the configured active-process limit."),
    TrustFactor("SEQUENCE_ALERT_MEDIUM", -10,
                "A MEDIUM-risk runtime event-sequence rule matched."),
    TrustFactor("SEQUENCE_ALERT_HIGH", -25,
                "A HIGH-risk runtime event-sequence rule matched."),
    TrustFactor("FACULTY_CONFIRMED_FINDING", -15,
                "A faculty member confirmed a security finding."),
    TrustFactor("FACULTY_DISMISSED_FINDING", 15,
                "A faculty member dismissed a previously flagged finding."),
    TrustFactor("WRONG_ANSWER", 0,
                "Ordinary wrong answer: NO trust penalty by design."),
    TrustFactor("COMPILATION_ERROR", 0,
                "Ordinary compilation error: NO trust penalty by design."),
)}

#: monitoring_level thresholds derived from the trust score.
TRUST_NORMAL_THRESHOLD = 70   # score >= 70  -> NORMAL
TRUST_ELEVATED_THRESHOLD = 50  # 50 <= score < 70 -> ELEVATED, else HIGH

#: An open (unreviewed) faculty alert always forces at least ELEVATED
#: monitoring, independent of the numeric score.
MIN_MONITORING_WITH_OPEN_ALERT = "ELEVATED"
