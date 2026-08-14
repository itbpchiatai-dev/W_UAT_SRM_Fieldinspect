"""AiCallLog — every AI provider call (v3.0).

Records prompt, response (PII-masked), token counts, cache hits, cost,
and outcome. Populated automatically by app.integrations.claude_ai
(or any other provider wrapper); never write directly from services
or endpoints — that bypasses the cost budget gate (AGENTS.md §16).

Schema must stay in sync with docs/logging.md §3.1 and the matching
alembic migration (partitioned by RANGE(created_at)).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AiCallLog(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"primary_key": [id, created_at]}

    user_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    endpoint: Mapped[str | None] = mapped_column(String(200))
    request_id: Mapped[str | None] = mapped_column(String(64))

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="anthropic")
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    # "messages" | "embeddings" | "completions"

    # Content (PII-masked before insert by AiCallLogger)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)

    # Metrics
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None]

    # Outcome
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # "success" | "error" | "timeout" | "rate_limited" | "budget_exceeded"
    error_type: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)

    extra_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    __table_args__ = (
        Index("ix_ai_logs_created", "created_at"),
        Index("ix_ai_logs_user_created", "user_id", "created_at"),
        Index("ix_ai_logs_model_created", "model", "created_at"),
        Index("ix_ai_logs_status_created", "status", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
