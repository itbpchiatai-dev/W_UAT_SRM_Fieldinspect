"""SystemLog — jobs, integrations, and system events (v3.0).

Distinct from activity_logs: no user actor, no PII. Captures background
job lifecycle, outbound integration calls, and scheduler/system events.

Schema must stay in sync with docs/logging.md §2.1 and the matching
alembic migration (partitioned by RANGE(created_at)).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"primary_key": [id, created_at]}

    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # "job" | "integration" | "scheduler" | "system" | "migration"
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # "started" | "success" | "failure" | "warning" | "info"

    duration_ms: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(String(100))
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    # SQLAlchemy reserves `metadata` — DB column is "metadata", Python attr
    # is `extra_metadata`. Same pattern as ActivityLog.

    __table_args__ = (
        Index("ix_system_logs_created", "created_at"),
        Index("ix_system_logs_category_event", "category", "event"),
        Index("ix_system_logs_status_created", "status", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
