"""PlotAccessPhone — a phone number authorized to inspect a Plot (round 8-3A).

Domain (locked for this round):
  - A phone is an ACCESS KEY of a Plot, nothing more — there is NO name, owner,
    or worker/farmer master tied to it. Who filled the form is still the
    record's free-text submitted_by_name.
  - A plot has AT MOST ONE active 'primary' phone and any number of active
    'additional' phones; both types have equal inspection rights.
  - The SAME phone may exist on many plots and many suppliers — uniqueness is
    scoped per plot, and only among ACTIVE rows (a deactivated row can be
    reactivated later; history is never hard-deleted).

phone_normalized is always the canonical ^0[689][0-9]{8}$ shape produced by
app.core.phone.normalize_thai_mobile at the API boundary — the DB CHECK is the
backstop. Constraint/index names match the ORM naming convention
(app/db/base.py) so this metadata and migration 0039 agree exactly.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

# Access types. primary → at most one active per plot; additional → many.
ACCESS_TYPE_PRIMARY = "primary"
ACCESS_TYPE_ADDITIONAL = "additional"
ACCESS_TYPES: tuple[str, ...] = (ACCESS_TYPE_PRIMARY, ACCESS_TYPE_ADDITIONAL)


class PlotAccessPhone(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "plot_access_phones"
    __table_args__ = (
        # `ck` naming convention (app/db/base.py) expands these to
        # ck_plot_access_phones_<name>; migration 0039 uses those exact names.
        CheckConstraint(
            "access_type IN ('primary', 'additional')", name="access_type_allowed"
        ),
        # Canonical Thai mobile — the app normalizes first, this is the backstop.
        CheckConstraint(
            "phone_normalized ~ '^0[689][0-9]{8}$'", name="phone_normalized_format"
        ),
        # AT MOST ONE active primary per plot. Partial unique index: only
        # active primary rows participate, so inactive history coexists freely.
        Index(
            "uq_plot_access_phones_active_primary_per_plot",
            "plot_id",
            unique=True,
            postgresql_where=text("is_active = true AND access_type = 'primary'"),
        ),
        # No duplicate ACTIVE phone within the same plot (a number can't be both
        # primary and additional, nor listed twice). The same phone on a
        # DIFFERENT plot is unaffected — the index is per (plot_id, phone).
        Index(
            "uq_plot_access_phones_active_phone_per_plot",
            "plot_id",
            "phone_normalized",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        # Exact-match lookup by phone across plots — the round 8-3B public flow
        # resolves an entered phone to the plots that authorize it.
        Index("ix_plot_access_phones_phone_normalized", "phone_normalized"),
    )

    plot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("plots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone_normalized: Mapped[str] = mapped_column(String(20), nullable=False)
    access_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
