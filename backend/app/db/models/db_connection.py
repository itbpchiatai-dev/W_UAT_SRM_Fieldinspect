"""DbConnection — an admin-managed PostgreSQL connection target.

The host app can register multiple external PostgreSQL databases at
runtime (via Setup → Database Connections) and run ad-hoc queries against
them in the Query Sandbox — no code change or redeploy. The connection
password is stored Fernet-encrypted in `password_encrypted` (never
plaintext, never returned by the API). See app/core/crypto.py.

`allow_write` is a per-connection guard: even when a Sandbox request opts
out of read-only mode, writes are only executed if the connection itself
permits them. Defaults to read-only for safety.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class DbConnection(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "db_connections"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5432)
    database: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    # Fernet ciphertext — NEVER expose in any response schema.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # libpq-style sslmode the UI lets the admin pick; mapped to asyncpg's
    # `ssl` connect arg in the service. disable | prefer | require | verify-ca | verify-full
    ssl_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="prefer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-connection write guard for the sandbox (defaults OFF = read-only).
    allow_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "success" | "failed" — last connection-test outcome, surfaced in the list.
    last_test_status: Mapped[str | None] = mapped_column(String(20))

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
