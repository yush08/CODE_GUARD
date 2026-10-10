"""Import all model modules so ``Base.metadata`` knows every table."""

from app.models.behavior_event import BehaviorEvent
from app.models.behavior_fingerprint import BehaviorFingerprint
from app.models.problem import Problem
from app.models.security_alert import SecurityAlert
from app.models.submission import Submission
from app.models.test_case import TestCase
from app.models.test_result import TestResult
from app.models.trust_history import TrustHistory

__all__ = [
    "BehaviorEvent",
    "BehaviorFingerprint",
    "Problem",
    "SecurityAlert",
    "Submission",
    "TestCase",
    "TestResult",
    "TrustHistory",
]
