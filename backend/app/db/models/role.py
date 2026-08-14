"""Role — named bundle of permissions.

`provider_scope` constrains which auth_provider users can hold the role:
"internal" → azure_ad only, "external" → local only, "any" → either. See
docs/auth.md §2.1.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.permission import Permission
    from app.db.models.user import User


class Role(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    provider_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="any")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(500))

    permissions: Mapped[list["Permission"]] = relationship(
        "Permission", secondary="role_permissions", lazy="selectin"
    )
    users: Mapped[list["User"]] = relationship(
        "User", secondary="user_roles", back_populates="roles"
    )
