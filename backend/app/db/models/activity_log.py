"""ActivityLog — merged audit + user_activity (v3.0).

One table, 3 event categories distinguished by flags:
- is_mutation:        create / update / delete on business data
- is_sensitive_read:  export / view PII / view other users
- is_security_event:  login / logout / permission denied / role change

Schema must stay in sync with docs/logging.md §1.2 and the matching
alembic migration (partitioned by RANGE(created_at)).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"primary_key": [id, created_at]}

    # Actor
    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    user_email_masked: Mapped[str | None] = mapped_column(String(255))

    # Action classification
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # "create" | "update" | "delete" | "read_sensitive" | "export" |
    # "login" | "login_failed" | "logout" | "permission_denied" | "role_change"

    action: Mapped[str] = mapped_column(String(100), nullable=False)

    # Target
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))

    # Flags (snake_case DB; CamelBaseModel renders camelCase JSON)
    is_mutation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sensitive_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_security_event: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), default="low", nullable=False)
    # "low" | "medium" | "high"

    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(64))
    endpoint: Mapped[str | None] = mapped_column(String(200))
    http_method: Mapped[str | None] = mapped_column(String(10))
    http_status: Mapped[int | None]

    # PII-scrubbed diff / details
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    # NOTE: SQLAlchemy reserves `metadata` — column is named "metadata" in DB
    # but accessed as `.extra_metadata` in Python to avoid the name clash.

    __table_args__ = (
        Index("ix_activity_user_created", "user_id", "created_at"),
        Index("ix_activity_action_type", "action_type", "created_at"),
        Index("ix_activity_resource", "resource_type", "resource_id"),
        Index("ix_activity_sensitive_created", "is_sensitive_read", "created_at"),
        Index("ix_activity_security_created", "is_security_event", "created_at"),
        Index("ix_activity_risk_created", "risk_level", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
