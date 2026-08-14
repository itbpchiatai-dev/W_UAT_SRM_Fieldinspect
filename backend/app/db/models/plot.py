"""Plot — agricultural field/แปลง belonging to a Supplier."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.plot_access_phone import PlotAccessPhone
    from app.db.models.plot_assignment import PlotAssignment
    from app.db.models.plot_cycle import PlotCycle
    from app.db.models.record import Record
    from app.db.models.supplier import Supplier

# Round 8-6H Part G — machine-readable contract values for Plot.is_active,
# mirroring PlotCycle's CYCLE_STATUS_* constants (app/db/models/plot_cycle.py)
# so a future read-model/Excel column can use an explicit `plotStatus` name
# (never a bare, ambiguous "status") without inventing a second vocabulary.
# is_active itself stays a plain boolean column — these are for display/API
# read-models only, never a DB column or a new field this round.
PLOT_STATUS_ACTIVE = "active"
PLOT_STATUS_INACTIVE = "inactive"


class Plot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "plots"

    supplier_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plot_code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    village: Mapped[str | None] = mapped_column(String(255), nullable=True)
    district: Mapped[str | None] = mapped_column(String(255), nullable=True)
    province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    rai: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Yield-planning base/master data (round 17, migration 0025) — a
    # denormalised MIRROR of the plot's active PlotCycle (round 8.0.4
    # ownership lock: PlotCycle is the only writer, via
    # plot_cycle_repository.create_cycle/update_cycle +
    # sync_plot_mirror_from_cycle; PlotCreate/PlotUpdate no longer carry
    # these fields at all). Never touched by sync_current_status_from_record
    # (same treatment as current_lot_no below: no records column sources
    # these). current_expected_yield is deliberately not a stored column —
    # compute expected_yield_full * current_yield_pct / 100 on demand instead.
    plant_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_yield_full: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expected_yield_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Opaque per-plot QR locator (round 20 QR hardening, migration
    # 0026_plots_qr_key) — replaces supplierCode+plotCode in printed QR
    # deep links, which were guessable/enumerable. Stored in plaintext, not
    # hashed: see the migration's docstring for why (short version — this
    # is a locator, not a save credential; the real gate is the supplier's
    # inspection_code (migration 0027), and admins need to reprint the
    # identical value for an already-affixed sticker without invalidating
    # it). Nullable — plots created before this round are backfilled by
    # app/db/backfill_plot_qr_key.py; every new plot gets one at create
    # time (plot_repository.create_plot), never client-supplied.
    qr_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)

    # Plot MASTER / planting-cycle data (round 11 columns; ownership
    # relocated round 8.0.4) — despite the `current_` prefix (kept as-is;
    # see plot_repository.sync_current_status_from_record's docstring),
    # these four are a denormalised MIRROR of the plot's active PlotCycle,
    # kept in sync exclusively by plot_cycle_repository (create_cycle/
    # update_cycle + sync_plot_mirror_from_cycle) — PlotCreate/PlotUpdate no
    # longer carry them (round 17.1 originally let Plot Create/Edit set them
    # directly; round 8.0.4 locked that down to close the dual-write gap
    # against the active cycle). NEVER overwritten by inspection-record
    # sync — same treatment as plant_count/expected_yield_full/
    # expected_yield_unit above. records.crop/variety/planting_date is a
    # separate snapshot copy taken at inspection time (never fed back into
    # these columns) — round 20.2 locked the public inspection flow down to
    # always copy these verbatim from here, so a field worker can no longer
    # report a different crop/variety/planting date than what's set here.
    current_crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_variety: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_lot_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_planting_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # True inspection-derived current-status snapshot (round 11 columns,
    # round 12 sync-on-record-create) — denormalized from the latest
    # record so a plot's latest known state is readable without joining/
    # aggregating records; overwritten every time a new record is created
    # (see sync_current_status_from_record). latitude/longitude above stay
    # the plot's registered/reference location — current_gps_lat/lng is
    # the GPS captured at the last visit, a distinct concept that can
    # drift from the reference point.
    current_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    current_field_prep_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    current_weather_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    current_care_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    current_variety_resistance_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    current_gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    current_gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)

    # Last-inspection tracking — no dedicated photo column; look up
    # last_inspection_record.photo_urls instead of denormalizing a URL that
    # could go stale if the record's own photos are later edited.
    last_inspected_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_inspected_by_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_inspection_record_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    supplier: Mapped["Supplier"] = relationship("Supplier", lazy="select")
    last_inspection_record: Mapped["Record | None"] = relationship(
        "Record", lazy="select", foreign_keys=[last_inspection_record_id],
        viewonly=True,
    )
    assignments: Mapped[list["PlotAssignment"]] = relationship(
        "PlotAssignment",
        back_populates="plot",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    # Planting cycles (round 7.1) — the plot's history of รอบปลูก. At most one
    # is 'active' at a time (PlotCycle.uq_plot_cycles_active_per_plot). lazy=
    # "select" (not eager): most plot reads don't need the full cycle history;
    # the active cycle is fetched on demand via plot_cycle_repository.
    cycles: Mapped[list["PlotCycle"]] = relationship(
        "PlotCycle",
        back_populates="plot",
        lazy="select",
    )
    # The plot's single ACTIVE cycle (round 7.3.1) — a read-only, filtered
    # view of `cycles` above so PlotSummary/PlotRead can carry the
    # active-cycle truth (loaded via selectinload, one IN-query, no N+1)
    # instead of the frontend inferring it from the current_* mirror. The
    # partial unique index guarantees at most one active cycle, so
    # uselist=False is exact. viewonly + overlaps: it shares
    # plot_cycles.plot_id with `cycles` but never writes through this path.
    active_cycle: Mapped["PlotCycle | None"] = relationship(
        "PlotCycle",
        primaryjoin="and_(PlotCycle.plot_id == Plot.id, PlotCycle.status == 'active')",
        viewonly=True,
        uselist=False,
        lazy="select",
        overlaps="cycles",
    )
    # Active access phones (round 8-3A) — read-only view of the plot's ACTIVE
    # plot_access_phones, ordered primary-first then deterministically (created_
    # at, id) so PlotRead/PlotSummary render primaryPhone/additionalPhones the
    # same way every time. viewonly + filtered (is_active only): writes go
    # through plot_access_phone_repository.replace_plot_access_phones, which sees
    # inactive rows too; this read view never does. Loaded via selectinload
    # (one IN-query per page, no N+1), same as active_cycle above. access_type
    # DESC puts 'primary' before 'additional' (only two values; 'primary' >
    # 'additional' lexically).
    access_phones: Mapped[list["PlotAccessPhone"]] = relationship(
        "PlotAccessPhone",
        primaryjoin="and_(PlotAccessPhone.plot_id == Plot.id, "
        "PlotAccessPhone.is_active == True)",
        order_by="(PlotAccessPhone.access_type.desc(), "
        "PlotAccessPhone.created_at.asc(), PlotAccessPhone.id.asc())",
        viewonly=True,
        lazy="select",
    )
