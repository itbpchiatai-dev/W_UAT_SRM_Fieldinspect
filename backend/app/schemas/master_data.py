"""MasterData request/response schemas (Step 12.5)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelBaseModel


class MasterDataCreate(CamelBaseModel):
    type: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=255)
    parent: str | None = Field(None, max_length=255)
    order_index: int = 0


class MasterDataUpdate(CamelBaseModel):
    value: str | None = Field(None, min_length=1, max_length=255)
    parent: str | None = Field(None, max_length=255)
    order_index: int | None = None
    active: bool | None = None


class MasterDataRead(CamelBaseModel):
    id: UUID
    type: str
    value: str
    parent: str | None
    order_index: int
    active: bool
    created_at: datetime
    updated_at: datetime
