"""Structured logging with automatic PII / secret masking.

Use `get_logger()` instead of `structlog.get_logger()` directly — the
wrapper redacts known-sensitive keys and free-form text patterns before
the log record leaves the process.

See docs/patterns/tooling.md §4 and AGENTS.md §B.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.pii import mask_email, mask_phone, mask_pii_in_text

SENSITIVE_KEYS = {
    "password", "password_hash", "token", "access_token", "refresh_token",
    "api_key", "secret", "client_secret", "credit_card", "national_id",
    "passport", "jwt", "auth_header",
}
EMAIL_KEYS = {"email", "user_email", "from_email", "to_email"}
PHONE_KEYS = {"phone", "phone_number", "mobile", "tel"}
TEXT_SCAN_MIN_LENGTH = 50


def _mask_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
    for key, value in kwargs.items():
        key_lower = key.lower()
        if key_lower in SENSITIVE_KEYS:
            masked[key] = "***"
        elif key_lower in EMAIL_KEYS and isinstance(value, str):
            masked[key] = mask_email(value)
        elif key_lower in PHONE_KEYS and isinstance(value, str):
            masked[key] = mask_phone(value)
        elif isinstance(value, str) and len(value) >= TEXT_SCAN_MIN_LENGTH:
            masked[key] = mask_pii_in_text(value)
        elif isinstance(value, dict):
            masked[key] = _mask_kwargs(value)
        else:
            masked[key] = value
    return masked


class StructuredLogger:
    """structlog wrapper that auto-masks PII / secrets in kwargs."""

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def info(self, event: str, **kwargs: Any) -> None:
        self._logger.info(event, **_mask_kwargs(kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._logger.warning(event, **_mask_kwargs(kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._logger.error(event, **_mask_kwargs(kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        self._logger.debug(event, **_mask_kwargs(kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._logger.exception(event, **_mask_kwargs(kwargs))

    def bind(self, **kwargs: Any) -> "StructuredLogger":
        return StructuredLogger(self._logger.bind(**_mask_kwargs(kwargs)))


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog. Call once at app startup."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> StructuredLogger:
    """Return a PII-masking logger. Use this instead of structlog.get_logger()."""
    return StructuredLogger(structlog.get_logger(name))
