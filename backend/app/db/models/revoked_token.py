"""RevokedToken — refresh-token blocklist by jti.

Why a table, not Redis:
  - Postgres is already in the stack; one more table keeps the runtime
    surface minimal.
  - Refresh tokens are low-volume (one row per logout + one per
    rotation) and rows TTL out via a periodic cleanup keyed on
    `expires_at` (run by scheduler `revoked_token_cleanup`).

Lookup hot path:
  `SELECT 1 FROM revoked_tokens WHERE jti = :jti LIMIT 1` — gated by the
  unique index on `jti`, so it stays O(log n) even at millions of rows.

See docs/security.md §6 (token lifecycle) for the full revocation flow.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class RevokedToken(Base, UUIDMixin):
    __tablename__ = "revoked_tokens"

    # Refresh-token jti (UUID4 hex) — set by jwt_service.encode_refresh_token.
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Subject (user id) — for forensics + bulk revoke (e.g. password change).
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # When the underlying JWT would have naturally expired. We can drop
    # rows past this point — they\'re no longer enforceable anyway.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # "logout" | "rotation" | "password_change" | "deactivated"
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="logout")
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
