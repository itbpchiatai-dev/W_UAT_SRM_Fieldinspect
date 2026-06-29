"""Record — FarmLog field inspection record (20 fixed typed columns)."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, SmallInteger, String, Text
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
    crop_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    growth_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    area_rai: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    plant_height_cm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)

    pest_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    pest_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    pest_severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    disease_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    disease_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    disease_severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    weed_severity: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fertilizer_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fertilizer_amount_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    irrigation_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    weather_condition: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
