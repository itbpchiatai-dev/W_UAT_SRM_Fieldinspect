"""Record — FarmLog field inspection record (Step 12.5: yield/list-driven).

Most assessment fields are list-coded (values picked from master_data) for fast
on-site capture; `yield_pct` is the headline Yield % (0–9999.9 — 150 is only a
non-blocking warning threshold the frontend shows, round 8-8B.1; 9999.9 is the
column's own NUMERIC(5,1) storage capacity). Only recommendation and notes are
free text. Custom fields live in `custom_fields` (Step 12).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.plot import Plot
    from app.db.models.plot_access_phone import PlotAccessPhone
    from app.db.models.plot_cycle import PlotCycle
    from app.db.models.supplier import Supplier
    from app.db.models.user import User

# Who the field visitor is, chosen per-record (round 8-3A). NULL for existing
# records and for the logged-in flow, which doesn't collect it this round.
#
# Round 8-11A — "extension" was renamed to "chiatai" (migration 0047 rewrote
# the existing dev rows and the CHECK constraint). These are the CANONICAL
# API/DB values and the single source of truth for the allowlist; the Thai
# UI labels ("เกษตรกร" / "บริษัทผู้ผลิต" / "Chiatai") live only in the
# frontend — never store a label here.
INSPECTOR_TYPE_FARMER = "farmer"
INSPECTOR_TYPE_SUPPLIER = "supplier"
INSPECTOR_TYPE_CHIATAI = "chiatai"
INSPECTOR_TYPES: tuple[str, ...] = (
    INSPECTOR_TYPE_FARMER,
    INSPECTOR_TYPE_SUPPLIER,
    INSPECTOR_TYPE_CHIATAI,
)


class Record(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "records"
    __table_args__ = (
        # Phone-access attribution bounds (round 8-3A, migration 0039) — mirror
        # the migration's CHECKs so model metadata and the DB agree. All four
        # columns are nullable (existing records read without backfill; the
        # public flow starts populating them in round 8-3B).
        CheckConstraint(
            "submitted_phone_type IS NULL "
            "OR submitted_phone_type IN ('primary', 'additional')",
            name="submitted_phone_type_allowed",
        ),
        # Round 8-11A (migration 0047) — 'extension' → 'chiatai'. The DB-side
        # name resolves through NAMING_CONVENTION's "ck" pattern to
        # ck_records_inspector_type_allowed, which is the literal name
        # migration 0039 created and 0047 drops/recreates.
        CheckConstraint(
            "inspector_type IS NULL "
            "OR inspector_type IN ('farmer', 'supplier', 'chiatai')",
            name="inspector_type_allowed",
        ),
        # Offline-submission idempotency backstop (round 8-4A, migration 0041).
        # Partial UNIQUE over client_submission_id excluding NULLs: only real
        # offline draft keys are constrained, so the many online/historical rows
        # with a NULL key never collide. Name matches migration 0041 exactly.
        Index(
            "uq_records_client_submission_id",
            "client_submission_id",
            unique=True,
            postgresql_where=text("client_submission_id IS NOT NULL"),
        ),
        # Yield-in-kg foundation (round 8-8A, migration 0044) — mirror the
        # migration's CHECKs so model metadata and the DB agree.
        CheckConstraint(
            "yield_quantity_kg IS NULL OR yield_quantity_kg >= 0",
            name="yield_quantity_kg_non_negative",
        ),
        CheckConstraint(
            "yield_target_kg_snapshot IS NULL OR yield_target_kg_snapshot >= 0",
            name="yield_target_kg_snapshot_non_negative",
        ),
    )

    plot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("plots.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    supplier_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # Planting cycle this record was captured under (round 7.1). Server-derived
    # from the plot's ACTIVE cycle at create time (never client-supplied) — see
    # records._create_record / public_records._verify_and_resolve. NOT NULL:
    # migration 0034 adds it nullable, backfills every existing record to its
    # plot's cycle_no=1, then flips it to NOT NULL (record.plot_id always
    # matches plot_cycle.plot_id by construction). ondelete RESTRICT mirrors
    # plot_id — a cycle with records can't be hard-deleted.
    plot_cycle_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("plot_cycles.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    recorded_by_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    # Field attribution — who actually filled the form on-site. Free text,
    # not tied to a login account, and never used for authorization; that's
    # what recorded_by_id (above) is for. submitted_by_code is retired (round
    # 8-3G, migration 0040 dropped its NOT NULL) — no create flow collects it
    # anymore; nullable so existing rows keep reading their historical value
    # unbackfilled. submitted_by_name (optional) is the sole remaining
    # field-attribution input.
    submitted_by_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    submitted_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Client IP at creation (migration 0031) — audit aid, resolved
    # server-side via rate_limit.get_client_ip (same trusted-proxy rules
    # as the rate limiter), NEVER from the request body. 45 chars fits the
    # longest IPv6 textual form; NULL for pre-0031 rows.
    submitted_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    record_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    # Crop identity (list ← master_data)
    crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    variety: Mapped[str | None] = mapped_column(String(100), nullable=True)
    growth_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    planting_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # Yield — headline metric, % 0–9999.9 (default 100). No DB CHECK
    # constraint on this column (bound only at the Pydantic schema layer,
    # app/schemas/record.py) — 150 is a non-blocking warning threshold the
    # frontend shows (round 8-8B.1), never a hard limit.
    yield_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), nullable=True, default=Decimal("100"), server_default="100")

    # Yield-in-kg foundation (round 8-8A, migration 0044). A kg-first client
    # sends yield_quantity_kg; the Backend derives yield_pct from it (see
    # app/services/yield_calculation.py) by comparing against the active
    # PlotCycle's expected_yield_full/expected_yield_unit AT CREATE TIME —
    # yield_target_kg_snapshot freezes that comparison target so the record
    # stays self-explanatory even if the cycle's plan is edited later. Both
    # NULL for a legacy client that only ever sends yieldPct, and for every
    # record created before this round (no backfill). yield_target_kg_snapshot
    # is SERVER-DERIVED ONLY — deliberately absent from RecordCreate/
    # PublicRecordCreate so a client can never forge it (same principle as
    # plot_cycle_id / the phone-access columns below).
    yield_quantity_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    yield_target_kg_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Assessment — multi-select on the forms: holds a ", "-joined list of
    # master-data weather values (kept a plain string; every reader shows it
    # as text). 255 fits all seeded options combined (migration 0030).
    weather_condition: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Condition scores, 1-10 (Step 12.6: replaced the list/status fields above)
    field_prep_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    weather_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    care_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    variety_resistance_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Free text
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    photo_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    # Phone-access attribution (round 8-3A, migration 0039). All nullable —
    # existing records read without backfill, the logged-in flow doesn't use
    # phone access, and the public flow starts populating these in round 8-3B.
    # These are SERVER-DERIVED there (from the verified access phone), never
    # client-supplied: deliberately absent from RecordCreate/PublicRecordCreate/
    # RecordUpdate so a client can't forge them.
    #   plot_access_phone_id     — the plot_access_phones row that authorized this
    #                              inspection. ON DELETE SET NULL: deactivating an
    #                              access phone never removes its records' history,
    #                              and submitted_phone_snapshot keeps the number.
    #   submitted_phone_snapshot — the canonical phone as it was at submit time,
    #                              so the record stays readable even if the access
    #                              phone row is later changed/removed.
    #   submitted_phone_type     — 'primary' | 'additional' at submit time.
    #   inspector_type           — 'farmer' | 'supplier' | 'chiatai' (round
    #                              8-11A), chosen per record.
    plot_access_phone_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("plot_access_phones.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    submitted_phone_snapshot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    submitted_phone_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inspector_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Offline submission (round 8-4A, migration 0041). Both nullable —
    # existing rows and every ONLINE submission leave them NULL.
    #   client_submission_id — client-generated id standing for ONE offline
    #                          draft across its whole life; the public create
    #                          endpoint uses it as an idempotency key (a retry
    #                          with the same key + same identity returns the
    #                          already-created record). Server-accepted from the
    #                          client body but constrained by the partial UNIQUE
    #                          index above so a duplicate can never insert twice.
    #   captured_at          — when the form was actually filled on-site
    #                          (offline), reported by the client after
    #                          validation. created_at stays server receive/
    #                          commit time, so plot.current_* snapshot ordering
    #                          (which uses created_at) is never skewed by a
    #                          backdated draft.
    client_submission_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True,
    )
    captured_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # foreign_keys= required: plots.last_inspection_record_id (round 11) adds
    # a second FK path between records/plots, so SQLAlchemy can no longer
    # auto-detect which column this relationship should join on.
    plot: Mapped["Plot"] = relationship("Plot", lazy="select", foreign_keys=[plot_id])
    plot_cycle: Mapped["PlotCycle"] = relationship(
        "PlotCycle", back_populates="records", lazy="select",
        foreign_keys=[plot_cycle_id],
    )
    supplier: Mapped["Supplier"] = relationship("Supplier", lazy="select")
    recorded_by: Mapped["User"] = relationship("User", lazy="select")
    # The access phone that authorized this inspection (round 8-3A). Read-only
    # here; the record is bound by setting plot_access_phone_id directly (round
    # 8-3B), same as plot_cycle_id. foreign_keys is explicit for consistency
    # with the file's other relationships (only one FK path exists to
    # plot_access_phones, so it's unambiguous either way).
    plot_access_phone: Mapped["PlotAccessPhone | None"] = relationship(
        "PlotAccessPhone", lazy="select", foreign_keys=[plot_access_phone_id],
    )
