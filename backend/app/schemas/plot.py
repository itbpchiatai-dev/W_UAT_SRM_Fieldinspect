"""Plot request/response schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelBaseModel


class AssignedUserSummary(CamelBaseModel):
    user_id: UUID
    email: str
    full_name: str
    assigned_at: datetime


class PlotCreate(CamelBaseModel):
    supplier_id: UUID
    plot_code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    village: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    province: str | None = Field(None, max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    rai: Decimal | None = Field(None, ge=0)


class PlotUpdate(CamelBaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    village: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    province: str | None = Field(None, max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    rai: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None


class PlotRead(CamelBaseModel):
    id: UUID
    supplier_id: UUID
    plot_code: str
    name: str
    village: str | None
    district: str | None
    province: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    rai: Decimal | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    assigned_users: list[AssignedUserSummary] = []


class PlotSummary(CamelBaseModel):
    id: UUID
    supplier_id: UUID
    plot_code: str
    name: str
    province: str | None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    is_active: bool
    assigned_count: int = 0


class PlotAssignRequest(CamelBaseModel):
    user_ids: list[UUID]
