"""Permission — read-only catalog (seeded; not user-editable).

Permissions are global keys (e.g. `users.read`). Roles
attach via role_permissions m2m; users can override via
user_permission_overrides (per-user grant/revoke).
"""
from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin


class Permission(Base, UUIDMixin):
    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    is_menu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
