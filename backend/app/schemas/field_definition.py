"""FieldDefinition request/response schemas (Step 12)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.db.models.field_definition import FIELD_TYPES
from app.schemas.base import CamelBaseModel

# ASCII snake_case slug — no spaces / Thai / uppercase (Spec §3.7).
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FieldDefinitionCreate(CamelBaseModel):
    """Admin creates CUSTOM fields only — is_core is forced False server-side."""
    key: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=255)
    field_type: str = Field(..., max_length=32)
    required: bool = False
    options_source: str | None = Field(None, max_length=128)
    options: list[Any] = Field(default_factory=list)
    list_default: bool = False
    order_index: int = 0

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        v = v.strip().lower()
        if not _KEY_RE.match(v):
            raise ValueError("key ต้องเป็น snake_case ASCII (a-z, 0-9, _) และขึ้นต้นด้วยตัวอักษร")
        return v

    @field_validator("field_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in FIELD_TYPES:
            raise ValueError(f"field_type ไม่ถูกต้อง — ต้องเป็นหนึ่งใน {', '.join(FIELD_TYPES)}")
        return v


class FieldDefinitionUpdate(CamelBaseModel):
    """key / field_type / is_core are immutable — not editable here."""
    label: str | None = Field(None, min_length=1, max_length=255)
    required: bool | None = None
    options_source: str | None = Field(None, max_length=128)
    options: list[Any] | None = None
    list_default: bool | None = None
    order_index: int | None = None
    active: bool | None = None


class FieldDefinitionRead(CamelBaseModel):
    id: UUID
    key: str
    label: str
    field_type: str
    required: bool
    options_source: str | None
    options: list[Any]
    is_core: bool
    list_default: bool
    order_index: int
    active: bool
    created_at: datetime
    updated_at: datetime
