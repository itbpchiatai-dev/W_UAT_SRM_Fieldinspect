"""Supplier request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, SkipValidation

from app.schemas.base import CamelBaseModel


class SupplierCreate(CamelBaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    tax_id: str | None = Field(None, max_length=20)
    contact_name: str | None = Field(None, max_length=255)
    contact_email: str | None = Field(None, max_length=255)
    contact_phone: str | None = Field(None, max_length=50)
    address: str | None = None


class SupplierUpdate(CamelBaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    tax_id: str | None = Field(None, max_length=20)
    contact_name: str | None = Field(None, max_length=255)
    contact_email: str | None = Field(None, max_length=255)
    contact_phone: str | None = Field(None, max_length=50)
    address: str | None = None
    is_active: bool | None = None


class SupplierRead(CamelBaseModel):
    id: UUID
    code: str
    name: str
    tax_id: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierSearchRequest(CamelBaseModel):
    """POST /suppliers/search body (round 8-20D).

    Exists because ONE of its filters is PII: `contact_phone_digits`. A phone
    fragment in a GET query string (`?contactPhone=...`) would land verbatim
    in Uvicorn's access log on every request — the same reason
    plots.py's search-by-phone is POST-and-body-only (round 8-17A.2). Every
    OTHER filter here could safely have stayed on the GET list endpoint; they
    travel together simply so one request answers the whole filter row.

    GET /suppliers is deliberately left untouched and still serves every
    existing caller (round 8-20D keeps it for backward compatibility).

    `contact_phone_digits` is `SkipValidation[str | None]`: Pydantic reports
    the declared type in the OpenAPI schema but performs ZERO runtime
    validation on it, so a malformed value can never be rejected by Pydantic
    itself — FastAPI's automatic RequestValidationError handler echoes the
    rejected value back in each error's `input` key, which for a phone
    fragment is exactly the leak this endpoint must never produce. The real
    check (ASCII digits, 4-10 of them) is hand-written in the endpoint and
    always answers with one fixed generic message. Same treatment, same
    reason, as PlotPhoneSearchRequest.phone.

    `q`/`contact_name` are plot/contact IDENTITY text, not PII in the
    protected sense, so ordinary Field bounds are fine for them — a Pydantic
    auto-422 echoing a supplier name is harmless. `limit`/`offset` likewise.
    """

    model_config = ConfigDict(extra="forbid")

    # Same free-text semantics GET /suppliers?q= already has: partial,
    # case-insensitive, matched against code OR name. Unchanged by this round.
    q: str | None = Field(None, max_length=255)
    contact_name: str | None = Field(None, max_length=255)
    contact_phone_digits: SkipValidation[str | None] = None
    status: Literal["active", "inactive", "all"] = "active"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class SupplierSummary(CamelBaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    contact_name: str | None
    contact_email: str | None
