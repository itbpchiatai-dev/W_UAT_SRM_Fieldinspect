"""Record — FarmLog field inspection record (Step 12.5: yield/list-driven).

Most assessment fields are list-coded (values picked from master_data) for fast
on-site capture; `yield_pct` is the headline Yield % (0–150). Only recommendation
and notes are free text. Custom fields live in `custom_fields` (Step 12).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.db.models.plot import Plot
    from app.db.models.supplier import Supplier
    from app.db.models.user import User


class Record(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "records"

    plot_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("plots.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    supplier_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    recorded_by_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    record_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    # Crop identity (list ← master_data)
    crop: Mapped[str | None] = mapped_column(String(100), nullable=True)
    variety: Mapped[str | None] = mapped_column(String(100), nullable=True)
    growth_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    planting_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)

    # Yield — headline metric, % 0–150 (default 100)
    yield_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 1), nullable=True, default=Decimal("100"), server_default="100")

    # Assessment (list ← master_data)
    weather_condition: Mapped[str | None] = mapped_column(String(100), nullable=True)
    field_prep_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    care_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pest_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    disease_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    weed_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    irrigation_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fertilizer: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Free text
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    photo_urls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    plot: Mapped["Plot"] = relationship("Plot", lazy="select")
    supplier: Mapped["Supplier"] = relationship("Supplier", lazy="select")
    recorded_by: Mapped["User"] = relationship("User", lazy="select")
