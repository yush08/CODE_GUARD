"""Application logging with credential redaction.

All log records pass through a redaction filter that masks anything that
looks like a database connection string password before it reaches a handler.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

# postgresql+psycopg://user:SECRET@host  ->  user:****
_URL_CREDENTIALS = re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)")


def redact(text: str) -> str:
    """Mask credentials embedded in connection strings."""
    return _URL_CREDENTIALS.sub(r"\1****\3", text)


class RedactingFilter(logging.Filter):
    """Masks connection-string credentials in messages and formatted args."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    """Configure console + rotating file logging exactly once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    redactor = RedactingFilter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(redactor)
    root.addHandler(console)

    try:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path / "codeguard.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - logging must never break the app
        logging.getLogger(__name__).warning("Could not open log file; console only.")

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _CONFIGURED = True
