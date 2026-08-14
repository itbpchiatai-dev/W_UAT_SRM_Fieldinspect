"""InspectionProtocolCriterion — admin-editable growth-stage inspection
protocol (round 5.5).

Makes the round-5.1 hardcoded protocol registry configurable: each row is
one criterion of one growth stage, bound to one of the 4 fixed score slots
(field_prep_score / weather_score / care_score / variety_resistance_score).
A stage's protocol is its (exactly 4) criteria, one per slot.

Deliberately a dedicated table, not master_data rows: master_data has no
column for the slot↔label binding, and packing it into value/order_index
would lose the structure this table makes explicit. The service still falls
back to the built-in registry (app/services/inspection_protocols.py's
DEFAULT_PROTOCOLS) when this table is empty, so an unseeded DB never breaks.

Records freeze their protocol into custom_fields at create time
(inspectionProtocolSnapshot), so editing a label here never rewrites history.
"""
from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class InspectionProtocolCriterion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "inspection_protocol_criteria"
    __table_args__ = (
        # Each stage carries each slot at most once — the 4 slots ARE the
        # stage's protocol.
        UniqueConstraint("growth_stage", "slot", name="uq_protocol_stage_slot"),
        # DB-level integrity (round 5.7 / migration 0033), kept in the model
        # so metadata matches the DB. No runtime behavior change. The `ck`
        # naming convention (app/db/base.py) expands these to
        # ck_inspection_protocol_criteria_<name> — the migration uses those
        # exact full names so model and DB agree.
        UniqueConstraint("growth_stage", "order_index", name="uq_protocol_stage_order"),
        CheckConstraint(
            "slot IN ('fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore')",
            name="slot_allowlist",
        ),
        CheckConstraint("order_index BETWEEN 0 AND 3", name="order_range"),
    )

    growth_stage: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # One of the 4 fixed camelCase score-field names (fieldPrepScore /
    # weatherScore / careScore / varietyResistanceScore) — matches the
    # snapshot/API slot vocabulary, never renamed.
    slot: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
