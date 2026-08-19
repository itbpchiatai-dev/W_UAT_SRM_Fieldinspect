"""MasterData request/response schemas (Step 12.5)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelBaseModel

_MSG_VALUE_REQUIRED = "กรุณาระบุค่า"


class MasterDataCreate(CamelBaseModel):
    type: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=255)
    parent: str | None = Field(None, max_length=255)
    order_index: int = 0

    # Round 8-22B — trims leading/trailing whitespace here, once, at the API
    # boundary, so create AND update always see the SAME normalized value
    # (repo.create/repo.update, and the duplicate pre-check in
    # app/api/v1/masterdata.py, all read payload.value AFTER this runs —
    # no other .strip() call needs to exist). min_length=1 above only checks
    # the RAW string, so "   " (whitespace-only) would otherwise pass it and
    # insert a value indistinguishable from blank; stripping first and then
    # requiring non-empty here closes that gap with a clear 422 instead of a
    # DB constraint surprise. Never changes case — only whitespace.
    @field_validator("value")
    @classmethod
    def _strip_and_require_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError(_MSG_VALUE_REQUIRED)
        return v


class MasterDataUpdate(CamelBaseModel):
    value: str | None = Field(None, min_length=1, max_length=255)
    parent: str | None = Field(None, max_length=255)
    order_index: int | None = None
    active: bool | None = None

    # Same normalization as MasterDataCreate.value — only runs when a value
    # is actually being changed (None means "leave value as-is").
    @field_validator("value")
    @classmethod
    def _strip_and_require_value(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError(_MSG_VALUE_REQUIRED)
        return v


class MasterDataRead(CamelBaseModel):
    id: UUID
    type: str
    value: str
    parent: str | None
    order_index: int
    active: bool
    created_at: datetime
    updated_at: datetime


class PublicMasterDataItem(CamelBaseModel):
    """Minimal shape for the unauthenticated /api/v1/public/masterdata
    endpoint (round 19.1) — deliberately excludes id/type/orderIndex/
    active/timestamps. `type` is redundant (the caller already knows what
    they asked for) and the rest is internal bookkeeping a public dropdown
    has no use for. Server-side ordering (repository's order_index, then
    value) is preserved in list order — the client never needs the sort
    key itself."""

    value: str
    parent: str | None
