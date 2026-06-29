"""Record request/response schemas — 20 fixed inspection fields."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelBaseModel


class RecordCreate(CamelBaseModel):
    plot_id: UUID
    supplier_id: UUID
    record_date: datetime.date

    crop_type: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    area_rai: Decimal | None = Field(None, ge=0)
    plant_height_cm: Decimal | None = Field(None, ge=0)

    pest_found: bool = False
    pest_detail: str | None = None
    pest_severity: int | None = Field(None, ge=1, le=5)

    disease_found: bool = False
    disease_detail: str | None = None
    disease_severity: int | None = Field(None, ge=1, le=5)

    weed_severity: int | None = Field(None, ge=0, le=5)
    fertilizer_used: str | None = Field(None, max_length=255)
    fertilizer_amount_kg: Decimal | None = Field(None, ge=0)

    irrigation_method: str | None = Field(None, max_length=100)
    weather_condition: str | None = Field(None, max_length=100)
    recommendation: str | None = None
    notes: str | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _clear_detail_when_not_found(self) -> "RecordCreate":
        if not self.pest_found:
            self.pest_detail = None
            self.pest_severity = None
        if not self.disease_found:
            self.disease_detail = None
            self.disease_severity = None
        return self


class RecordUpdate(CamelBaseModel):
    record_date: datetime.date | None = None

    crop_type: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    area_rai: Decimal | None = Field(None, ge=0)
    plant_height_cm: Decimal | None = Field(None, ge=0)

    pest_found: bool | None = None
    pest_detail: str | None = None
    pest_severity: int | None = Field(None, ge=1, le=5)

    disease_found: bool | None = None
    disease_detail: str | None = None
    disease_severity: int | None = Field(None, ge=1, le=5)

    weed_severity: int | None = Field(None, ge=0, le=5)
    fertilizer_used: str | None = Field(None, max_length=255)
    fertilizer_amount_kg: Decimal | None = Field(None, ge=0)

    irrigation_method: str | None = Field(None, max_length=100)
    weather_condition: str | None = Field(None, max_length=100)
    recommendation: str | None = None
    notes: str | None = None
    is_active: bool | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] | None = None


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
    crop_type: str | None
    growth_stage: str | None
    area_rai: Decimal | None
    plant_height_cm: Decimal | None

    pest_found: bool
    pest_detail: str | None
    pest_severity: int | None

    disease_found: bool
    disease_detail: str | None
    disease_severity: int | None

    weed_severity: int | None
    fertilizer_used: str | None
    fertilizer_amount_kg: Decimal | None

    irrigation_method: str | None
    weather_condition: str | None
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
    crop_type: str | None
    growth_stage: str | None
    pest_found: bool
    disease_found: bool
    is_active: bool
    created_at: datetime.datetime
    # Denormalised display fields (populated by API layer from relationships)
    plot_code: str = ""
    plot_name: str = ""
    supplier_name: str = ""
