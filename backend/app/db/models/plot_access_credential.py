"""PlotAccessCredential — the inspection password ("รหัสยืนยันแปลง") of a Plot
(round 8-9A).

Domain (locked for this round):
  - The credential belongs to the PLOT, not the supplier and not a person. It
    is a SHARED access credential — anyone holding the plot's inspection phone
    number plus this password may inspect it. It is never a name/identity.
  - A plot has AT MOST ONE credential row (UNIQUE(plot_id)); changing the
    password updates that row in place and bumps credential_version.
  - Several plots MAY deliberately share the same password — hence NO unique
    constraint on password_lookup_digest/password_hash. Changing one plot's
    password can never affect another plot that happened to use the same one.

Storage:
  - password_hash — bcrypt (app.auth.plot_access_password). NEVER plaintext,
    and never returned by any API.
  - password_lookup_digest — HMAC-SHA256(server pepper, PIN), lowercase hex(64).
    A BLIND INDEX: round 8-9C resolves "phone + password" to the matching plots
    with one indexed lookup instead of bcrypt-verifying every candidate.

There is deliberately NO relationship from Plot to this model — the credential
must never be eager-loaded into a Plot list/read model. Callers go through
plot_access_credential_repository explicitly.

Constraint/index names match the ORM naming convention (app/db/base.py) so this
metadata and migration 0046 agree exactly.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import CHAR, Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class PlotAccessCredential(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "plot_access_credentials"
    __table_args__ = (
        # `ck` naming convention (app/db/base.py) expands these to
        # ck_plot_access_credentials_<name>; migration 0046 uses those names.
        CheckConstraint(
            "credential_version >= 1", name="credential_version_positive"
        ),
        # Blind index is always lowercase hex(64) — the helper emits
        # hexdigest(); this is the backstop.
        CheckConstraint(
            "password_lookup_digest ~ '^[0-9a-f]{64}$'",
            name="password_lookup_digest_format",
        ),
    )

    plot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("plots.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_lookup_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False, index=True)
    credential_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # SET NULL so deleting the admin who set a password never deletes the
    # plot's credential.
    updated_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
