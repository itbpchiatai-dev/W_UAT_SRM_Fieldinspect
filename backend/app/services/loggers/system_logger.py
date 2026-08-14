"""SystemLogger — persists system events to system_logs.

Wire into background jobs (start/end/error), outbound integration
calls (success/error), and system-level events. Caller commits the
session.

Exception messages are run through mask_pii_in_text() before insert —
outbound HTTP errors routinely echo back Authorization headers or
query-string tokens that would otherwise land in the DB.

See docs/logging.md §2.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_pii_in_text
from app.db.models.system_log import SystemLog


class SystemLogger:
    """Persist job / integration / system events."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log_job(
        self,
        *,
        job_name: str,
        status: str,
        duration_ms: int | None = None,
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        await self._log(
            category="job",
            event=f"job.{status}",
            status=status,
            duration_ms=duration_ms,
            error=error,
            metadata={"job_name": job_name, **(metadata or {})},
            correlation_id=correlation_id,
        )

    async def log_integration(
        self,
        *,
        provider: str,
        operation: str,
        status: str,
        duration_ms: int | None = None,
        error: Exception | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        await self._log(
            category="integration",
            event=f"integration.{provider}.{operation}",
            status=status,
            duration_ms=duration_ms,
            error=error,
            metadata={"provider": provider, "operation": operation, **(metadata or {})},
            correlation_id=correlation_id,
        )

    async def log_system_event(
        self,
        *,
        event: str,
        status: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._log(
            category="system",
            event=event,
            status=status,
            metadata=metadata or {},
        )

    async def _log(
        self,
        *,
        category: str,
        event: str,
        status: str,
        metadata: dict[str, Any],
        duration_ms: int | None = None,
        error: Exception | None = None,
        correlation_id: str | None = None,
    ) -> None:
        entry = SystemLog(
            id=uuid4(),
            category=category,
            event=event,
            status=status,
            duration_ms=duration_ms,
            error_message=mask_pii_in_text(str(error))[:2000] if error else None,
            error_type=type(error).__name__ if error else None,
            correlation_id=correlation_id,
            extra_metadata=metadata,
        )
        self.db.add(entry)
        # Caller commits.
