"""User — auth identity.

`auth_provider` is the hard provider-stripe: "azure_ad" users never have
a password_hash; "local" users never participate in Azure AD SSO. See
docs/auth.md §2.1.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.role import Role
    from app.db.models.supplier import Supplier
    from app.db.models.user_permission_override import UserPermissionOverride


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    auth_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # is_approved: admin-gated. Default False so new users (SSO auto-provision
    # or local signup) wait for admin approval before they can sign in.
    # Bootstrap super-admin (seed) and admin-created users default True.
    # Toggleable per-user; can be bypassed globally via app_setting
    # `auth.auto_approve_new_users`.
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # is_rejected: admin explicitly declined. Mutually exclusive with
    # is_approved in practice. We keep both booleans (rather than a single
    # enum) so existing queries on is_approved stay intact.
    is_rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # One-time approval link token. We store sha256(token) so a DB leak
    # doesn\'t hand the attacker the live URLs; raw token only lives in
    # the email body. Token expires per
    # `notifications.approval_link_ttl_days` app_setting (default 7).
    approval_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    approval_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Round 8-23A — session generation counter. Every access/refresh token
    # carries the value that was live when it was minted (claim
    # `auth_version`); auth/dependencies.get_current_user and /auth/refresh
    # compare that claim against this column and reject on ANY mismatch.
    # Bumping it therefore invalidates every outstanding token for this user
    # at once — which the per-jti `revoked_tokens` blocklist cannot do,
    # because jtis are not recorded at mint time. Incremented under a row
    # lock in the same transaction as password_hash (see
    # api/v1/users.reset_user_password). A token with NO auth_version claim
    # is read as 0, so pre-8-23A sessions keep working until the first bump.
    auth_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    business_unit_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    # FarmLog: link to owning supplier (NULL for internal staff)
    supplier_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_supplier_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    roles: Mapped[list["Role"]] = relationship(
        "Role", secondary="user_roles", back_populates="users", lazy="selectin"
    )
    permission_overrides: Mapped[list["UserPermissionOverride"]] = relationship(
        "UserPermissionOverride",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="UserPermissionOverride.user_id",
    )
    supplier: Mapped["Supplier | None"] = relationship("Supplier", lazy="select")
