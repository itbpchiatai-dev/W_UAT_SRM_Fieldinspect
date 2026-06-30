"""Record request/response schemas (Step 12.5: yield/list-driven)."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelBaseModel


class RecordCreate(CamelBaseModel):
    plot_id: UUID
    supplier_id: UUID
    record_date: datetime.date

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    planting_date: datetime.date | None = None

    # Yield % 0–150 (default 100)
    yield_pct: Decimal | None = Field(Decimal("100"), ge=0, le=150)

    weather_condition: str | None = Field(None, max_length=100)
    field_prep_level: str | None = Field(None, max_length=50)
    care_level: str | None = Field(None, max_length=50)
    pest_status: str | None = Field(None, max_length=50)
    disease_status: str | None = Field(None, max_length=50)
    weed_status: str | None = Field(None, max_length=50)
    irrigation_method: str | None = Field(None, max_length=100)
    fertilizer: str | None = Field(None, max_length=100)

    recommendation: str | None = None
    notes: str | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] = Field(default_factory=list)

    # Dynamic custom fields (Step 12) — keyed by FieldDefinition.key (slug).
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class RecordUpdate(CamelBaseModel):
    record_date: datetime.date | None = None

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    planting_date: datetime.date | None = None

    yield_pct: Decimal | None = Field(None, ge=0, le=150)

    weather_condition: str | None = Field(None, max_length=100)
    field_prep_level: str | None = Field(None, max_length=50)
    care_level: str | None = Field(None, max_length=50)
    pest_status: str | None = Field(None, max_length=50)
    disease_status: str | None = Field(None, max_length=50)
    weed_status: str | None = Field(None, max_length=50)
    irrigation_method: str | None = Field(None, max_length=100)
    fertilizer: str | None = Field(None, max_length=100)

    recommendation: str | None = None
    notes: str | None = None
    is_active: bool | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class RecordRead(CamelBaseModel):
    id: UUID
    plot_id: UUID
    supplier_id: UUID
    recorded_by_id: UUID
    recorded_by_email: str = ""
    recorded_by_name: str = ""
    plot_code: str = ""
    plot_name: str = ""
    supplier_name: str = ""

    record_date: datetime.date
    crop: str | None
    variety: str | None
    growth_stage: str | None
    planting_date: datetime.date | None
    yield_pct: Decimal | None

    weather_condition: str | None
    field_prep_level: str | None
    care_level: str | None
    pest_status: str | None
    disease_status: str | None
    weed_status: str | None
    irrigation_method: str | None
    fertilizer: str | None

    recommendation: str | None
    notes: str | None

    latitude: Decimal | None
    longitude: Decimal | None
    photo_urls: list[str]

    custom_fields: dict[str, Any]
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RecordSummary(CamelBaseModel):
    id: UUID
    plot_id: UUID
    supplier_id: UUID
    recorded_by_id: UUID
    record_date: datetime.date
    crop: str | None
    variety: str | None
    growth_stage: str | None
    yield_pct: Decimal | None
    pest_status: str | None
    disease_status: str | None
    is_active: bool
    created_at: datetime.datetime
    # Denormalised display fields (populated by API layer from relationships)
    plot_code: str = ""
    plot_name: str = ""
    supplier_name: str = ""
