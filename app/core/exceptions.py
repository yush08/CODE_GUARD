"""Typed application exceptions with stable machine-readable error codes.

Every error raised through this module is converted by the FastAPI exception
handlers in ``app.main`` into a consistent JSON envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}}

Messages must never contain credentials, raw stack traces, or internal
absolute paths.
"""

from __future__ import annotations

from typing import Any


class CodeGuardError(Exception):
    """Base class for all expected application errors."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class DatabaseNotConfiguredError(CodeGuardError):
    code = "DATABASE_NOT_CONFIGURED"
    http_status = 503

    def __init__(self, reason: str) -> None:
        super().__init__(
            "The database is not configured; this operation requires persistent storage.",
            details={"reason": reason},
        )


class DatabaseUnavailableError(CodeGuardError):
    code = "DATABASE_UNAVAILABLE"
    http_status = 503

    def __init__(self, detail: str = "The database could not be reached.") -> None:
        super().__init__(detail)


class NotFoundError(CodeGuardError):
    code = "NOT_FOUND"
    http_status = 404


class ValidationFailedError(CodeGuardError):
    code = "VALIDATION_ERROR"
    http_status = 422


class UnsupportedLanguageError(CodeGuardError):
    code = "UNSUPPORTED_LANGUAGE"
    http_status = 422


class SourceTooLargeError(CodeGuardError):
    code = "SOURCE_TOO_LARGE"
    http_status = 413


class CompilerNotAvailableError(CodeGuardError):
    code = "COMPILER_NOT_AVAILABLE"
    http_status = 503


class QueueFullError(CodeGuardError):
    code = "EXECUTION_QUEUE_FULL"
    http_status = 503

    def __init__(self) -> None:
        super().__init__(
            "The execution queue is full. Retry after current submissions finish.",
            details={"retry_after_seconds": 5},
        )


class ExecutionEngineError(CodeGuardError):
    """An unexpected failure inside the execution engine (mapped to a failed
    submission with status INTERNAL_ERROR rather than an HTTP error)."""

    code = "EXECUTION_ENGINE_ERROR"
    http_status = 500
