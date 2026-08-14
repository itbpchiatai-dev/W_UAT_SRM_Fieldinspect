"""PlotCycle — one planting cycle (รอบปลูก) within a Plot (round 7.1).

New Supplier → Plot → PlotCycle → Record hierarchy. A Plot is the PERMANENT
physical field (supplier, plot_code, coordinates, QR key, permanent
is_active flag); a PlotCycle is one planting season on it (crop/variety/lot/
planting date/plant count/expected yield), and an inspection Record binds to
the plot's ACTIVE cycle at the moment it is created (records.plot_cycle_id).

Lifecycle: a plot has AT MOST ONE 'active' cycle at a time (partial unique
index below). Closing a cycle (harvested/cancelled) preserves its history and
lets a fresh cycle start on the same plot WITHOUT reprinting the QR — the QR
stays bound to the Plot, not the cycle. plots.is_active means PERMANENT field
closure, NOT cycle closure.

Field ownership (round 7.1, extends round 17.1's plot split):
  - The ACTIVE cycle is the source of truth for the plot's master/planting
    mirror columns (plots.current_crop / current_variety / current_lot_no /
    current_planting_date / plant_count / expected_yield_full /
    expected_yield_unit) — kept in sync by
    plot_cycle_repository.sync_plot_mirror_from_cycle. Those columns are kept
    for now (no destructive drop this round) as a denormalised read mirror.
  - The inspection-derived snapshot on plots (current_stage /
    current_yield_pct / the 4 scores / current_gps_* / last_inspection*) is
    UNCHANGED — still synced from the latest record by
    plot_repository.sync_current_status_from_record.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.plot import Plot
    from app.db.models.record import Record
    from app.db.models.user import User

# Allowed lifecycle states. active → harvested | cancelled. Round 7.2 will
# expose the transitions via API; 7.1 only backfills + reads + guards create.
CYCLE_STATUS_ACTIVE = "active"
CYCLE_STATUS_HARVESTED = "harvested"
CYCLE_STATUS_CANCELLED = "cancelled"
CYCLE_STATUSES: tuple[str, ...] = (
    CYCLE_STATUS_ACTIVE,
    CYCLE_STATUS_HARVESTED,
    CYCLE_STATUS_CANCELLED,
)
# Terminal (closed) states — a cycle in one of these is history.
CYCLE_CLOSED_STATUSES: tuple[str, ...] = (CYCLE_STATUS_HARVESTED, CYCLE_STATUS_CANCELLED)

# Round 8-6H Part G — machine-readable "no active cycle" contract value,
# distinct from the three real PlotCycle.status values above (never stored
# on a row — no cycle ever has this as its actual status). For read-models
# that need to say "this plot currently has no active cycle" without
# overloading one of the three real statuses or inventing a bare ambiguous
# "status" field name — e.g. a future Excel cycleStatus column (round
# 8-6J will wire the template/UI; this round only reserves the constant).
CYCLE_STATUS_NONE = "none"

# How lot_no was derived (round 8-5A, migration 0042). 'auto' = server-
# generated — {cycleLabel}-{supplierCode}-{pCode}-{running} (V2, round 8-12A;
# see auto_lot_series_key below for how a V2 row is told apart from a V1 one).
# V1's original {PO}-{plotCode}-{running} formula is GONE from the code, but
# every row it already produced keeps its lot_no/lot_no_source/lot_running_no
# untouched — no backfill, no reformatting (see lot_number.py). 'manual' = a
# value supplied verbatim; 'legacy' = reserved for pre-8-5A/backfilled data
# (no code path writes it this round); NULL = no lot_no / a cycle predating
# the field.
LOT_SOURCE_AUTO = "auto"
LOT_SOURCE_MANUAL = "manual"
LOT_SOURCE_LEGACY = "legacy"
LOT_SOURCES: tuple[str, ...] = (LOT_SOURCE_AUTO, LOT_SOURCE_MANUAL, LOT_SOURCE_LEGACY)


class PlotCycle(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "plot_cycles"
    __table_args__ = (
        # cycle_no is sequential per plot (1, 2, 3, …).
        UniqueConstraint("plot_id", "cycle_no"),  # → uq_plot_cycles_plot_id_cycle_no
        # AT MOST ONE active cycle per plot. Partial unique index: only
        # status='active' rows participate, so any number of closed cycles
        # coexist. Explicit name (not the naming convention) so the model
        # metadata and migration 0034 agree.
        Index(
            "uq_plot_cycles_active_per_plot",
            "plot_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # `ck` naming convention (app/db/base.py) expands these to
        # ck_plot_cycles_<name>; migration 0034 uses those exact full names.
        CheckConstraint("cycle_no >= 1", name="cycle_no_positive"),
        CheckConstraint(
            "status IN ('active', 'harvested', 'cancelled')", name="status_allowed"
        ),
        CheckConstraint(
            "plant_count IS NULL OR plant_count >= 0", name="plant_count_non_negative"
        ),
        CheckConstraint(
            "expected_yield_full IS NULL OR expected_yield_full >= 0",
            name="expected_yield_full_non_negative",
        ),
        # Final estimated-yield snapshot bounds (round 8-2.8A, migration 0038,
        # range widened round 8-8B.1 migration 0045) — mirror the migration's
        # CHECKs so model metadata and the DB agree. 150% is a non-blocking
        # warning threshold only now (yield_calculation.py's YIELD_WARNING_PCT)
        # — 9999.9 is the real hard limit, the column's own NUMERIC(5,1)
        # storage capacity.
        CheckConstraint(
            "final_yield_pct IS NULL OR (final_yield_pct >= 0 AND final_yield_pct <= 9999.9)",
            name="final_yield_pct_range",
        ),
        CheckConstraint(
            "final_estimated_yield IS NULL OR final_estimated_yield >= 0",
            name="final_estimated_yield_non_negative",
        ),
        # PO / Auto Lot metadata (round 8-5A, migration 0042) — mirror the
        # migration's CHECKs and partial unique index so model metadata and the
        # DB agree. The `ck` naming convention expands names to
        # ck_plot_cycles_<name>; the index name is explicit (not the
        # convention) so it matches migration 0042 exactly.
        CheckConstraint(
            "lot_no_source IS NULL OR lot_no_source IN ('auto', 'manual', 'legacy')",
            name="lot_no_source_allowed",
        ),
        CheckConstraint(
            "lot_running_no IS NULL OR lot_running_no >= 1",
            name="lot_running_no_positive",
        ),
        # Round 8-12A (migration 0048), strengthened 8-12A.1 (migration 0049)
        # — an 'auto' row must carry lot_no + lot_running_no, AND satisfy the
        # branch for its formula version:
        #   V1 (series key NULL)     → po_number (the old leading component)
        #   V2 (series key NOT NULL) → nonblank cycle_label + p_code, the data
        #                              its own lot number was rendered from.
        CheckConstraint(
            "lot_no_source IS DISTINCT FROM 'auto' "
            "OR (lot_no IS NOT NULL AND lot_running_no IS NOT NULL "
            "AND ((auto_lot_series_key IS NULL AND po_number IS NOT NULL) "
            "OR (auto_lot_series_key IS NOT NULL "
            "AND cycle_label IS NOT NULL AND btrim(cycle_label) <> '' "
            "AND p_code IS NOT NULL AND btrim(p_code) <> '')))",
            name="auto_lot_requires_fields",
        ),
        # V1 concurrency backstop: at most one 'auto' cycle per
        # (plot_id, po_number, lot_running_no). Round 8-12A scoped it to V1 rows
        # (auto_lot_series_key IS NULL) — V2 rows still store a po_number and
        # would otherwise collide across different series on one plot.
        Index(
            "uq_plot_cycles_auto_lot_running",
            "plot_id",
            "po_number",
            "lot_running_no",
            unique=True,
            postgresql_where=text("lot_no_source = 'auto' AND auto_lot_series_key IS NULL"),
        ),
        # V2 concurrency backstop (round 8-12A): one running number per
        # (supplier, cycleLabel, pCode) series, ACROSS plots. Round 8-12A.1
        # made the 'auto' term explicit (migration 0049).
        Index(
            "uq_plot_cycles_auto_lot_series_running",
            "auto_lot_series_key",
            "lot_running_no",
            unique=True,
            postgresql_where=text(
                "lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL"
            ),
        ),
        # Round 8-12A.1 (migration 0049) — two DIFFERENT series can render the
        # same lot_no text, because cycleLabel/supplierCode/pCode may each
        # contain "-". The running number is unique per series; this makes the
        # printed lot number unique too. V2 auto rows only — legacy data
        # contains a duplicate lot_no and must stay valid.
        Index(
            "uq_plot_cycles_auto_lot_v2_lot_no",
            "lot_no",
            unique=True,
            postgresql_where=text(
                "lot_no_source = 'auto' AND auto_lot_series_key IS NOT NULL"
            ),
        ),
        # Actual-harvest fields (round 8-7A, migration 0043) — mirror the
        # migration's CHECKs so model metadata and the DB agree.
        CheckConstraint(
            "harvest_yield IS NULL OR harvest_yield >= 0",
            name="harvest_yield_non_negative",
        ),
        CheckConstraint(
            "final_yield_after_clean IS NULL OR final_yield_after_clean >= 0",
            name="final_yield_after_clean_non_negative",
        ),
        CheckConstraint(
            "(harvest_yield IS NULL AND final_yield_after_clean IS NULL "
            " AND final_yield_unit IS NULL AND harvest_date IS NULL)"
            " OR "
            "(harvest_yield IS NOT NULL AND final_yield_after_clean IS NOT NULL "
            " AND final_yield_unit IS NOT NULL AND harvest_date IS NOT NULL)",
            name="actual_harvest_all_or_none",
        ),
    )

    plot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("plots.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cycle_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Planting-cycle master data — the source of truth for the plot mirror
    # columns while this cycle is active (sync_plot_mirror_from_cycle).
    crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    variety: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # cycle_label (round 8.0, migration 0036) — the user/admin-chosen season
    # name for this รอบปลูก, e.g. "jun2026" / "may2026". Purely for display
    # (frontend uses it as the cycle's title, falling back to "รอบที่ N" when
    # NULL). NOT lot_no (a production/lot identifier) and NOT cycle_no (the
    # system's sequential per-plot number) — an independent free-text label.
    cycle_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lot_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # PO / Auto Lot metadata (round 8-5A, migration 0042). po_number/p_code are
    # cycle-level business identifiers (normalized by the app layer); lot_no_
    # source/lot_running_no are SERVER-derived bookkeeping for an Auto Lot and
    # are never client-supplied (absent from PlotCycleCreate/Update). See the
    # LOT_SOURCE_* constants above and lot_no_source's CHECK constraint.
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    p_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lot_no_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lot_running_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Round 8-12A (migration 0048) — the SUPPLIER's own lot identifier for this
    # cycle. Free-form, optional, not unique, and completely independent of
    # lot_no: it never feeds the Auto Lot formula, the running number, or the
    # Manual/Auto decision. Client-writable (PlotCycleCreate/Update).
    supplier_lot_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Round 8-21A (migration 0050) — three independent, OPTIONAL, free-text
    # back-office reference fields, one per cycle (may legitimately change
    # cycle to cycle, unlike a Plot-level field). Same normalization as
    # supplier_lot_no above (trim, blank -> NULL; see
    # app/services/cycle_reference_fields.py) and the same "no business
    # logic" independence: none of the three ever feeds the Auto Lot
    # formula, the running number, or any Manual/Auto decision.
    # Client-writable (PlotCycleCreate/Update). Never surfaced on Record,
    # Public Inspect, or the field inspection form — cycle-level admin data
    # only.
    oracle_supplier_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oracle_invoice: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ref_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Round 8-12A — INTERNAL Auto Lot V2 bookkeeping: the
    # (supplier, cycleLabel, pCode) series a V2 running number counts within
    # (build_auto_lot_series_key). SERVER-derived only — never in any request
    # schema and never returned by the API. NULL means "not a V2 auto row",
    # which is how every pre-8-12A row (V1 auto, manual, legacy, no-lot) is
    # identified without re-parsing its lot string.
    auto_lot_series_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    planting_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    plant_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_yield_full: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expected_yield_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # started_at defaults to now() for ORM-created cycles; the backfill sets it
    # explicitly (planting_date, else plots.created_at). closed_at/closed_by/
    # close_reason are only set when the cycle is closed (round 7.2).
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Final ESTIMATED-yield snapshot, frozen when the cycle closes (round
    # 8-2.8A, migration 0038). "ผลผลิตประมาณการสุดท้าย" — NOT actual harvested
    # yield; there is no actual-harvest field. All NULL for cycles closed
    # before 0038 (no backfill) and for a cycle closed with no inspection.
    #   final_yield_pct        — the closing inspection's yield %, else NULL.
    #   final_estimated_yield  — expected_yield_full × final_yield_pct / 100
    #                            (NULL when either input is NULL).
    #   final_inspection_record_id — the record the snapshot came from; kept
    #                            even when its yield_pct was NULL so the source
    #                            is still traceable. FK ON DELETE SET NULL adds a
    #                            SECOND FK path plot_cycles↔records, so the
    #                            existing `records` relationship keeps its
    #                            explicit foreign_keys=. No ORM relationship is
    #                            declared here — the read schema only needs the id.
    final_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    final_estimated_yield: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    final_inspection_record_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("records.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Actual harvest — the REAL figures recorded when the cycle is finalized
    # (round 8-7A, Excel action final_plot; migration 0043). Distinct from
    # final_estimated_yield above (an ESTIMATE from the last inspection):
    # these are what was actually weighed at harvest. All NULL for a cycle
    # closed by any other path (single-cycle close, rollover, start_next_
    # cycle, close_and_start_new_cycle) — only final_plot ever writes them.
    # The all-or-none CHECK (actual_harvest_all_or_none) guarantees the first
    # four are never half-recorded; final_note is independently optional.
    harvest_yield: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    final_yield_after_clean: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    final_yield_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    harvest_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    final_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    plot: Mapped["Plot"] = relationship("Plot", back_populates="cycles", lazy="select")
    records: Mapped[list["Record"]] = relationship(
        "Record",
        back_populates="plot_cycle",
        lazy="select",
        foreign_keys="Record.plot_cycle_id",
    )
    closed_by: Mapped["User | None"] = relationship("User", lazy="select")
