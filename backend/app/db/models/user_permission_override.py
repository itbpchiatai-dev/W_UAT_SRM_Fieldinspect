"""Per-user permission grant/revoke (Pattern B inside L1 baseline).

`granted=True` adds a perm the user's roles don't have; `granted=False`
explicitly revokes a perm a role would otherwise grant. Resolution
happens in app/auth/dependencies.py:_compute_effective_permissions.
See docs/auth.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.permission import Permission
    from app.db.models.user import User


class UserPermissionOverride(Base, UUIDMixin):
    __tablename__ = "user_permission_overrides"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(
        "User", back_populates="permission_overrides", foreign_keys=[user_id]
    )
    permission: Mapped["Permission"] = relationship("Permission", lazy="joined")
