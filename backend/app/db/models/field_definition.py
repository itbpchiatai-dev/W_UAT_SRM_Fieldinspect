"""FieldDefinition — schema-driven form field catalog (Step 12, Spec §3.7).

`is_core=True` rows mirror the typed `records` columns; their `key`/`field_type`
are immutable. `is_core=False` rows are admin-created custom fields whose values
live in `records.custom_fields` JSONB under `key` (an immutable ASCII slug).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin

# Allowed widget types — mirrored on the frontend field registry.
FIELD_TYPES: tuple[str, ...] = (
    "score", "percent", "photo", "geo", "plot_picker",
    "list", "date", "text", "multiline", "number", "boolean",
)


class FieldDefinition(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "field_definitions"

    # Immutable ASCII slug — core: maps to a records column; custom: custom_fields key.
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    options_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    options: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    is_core: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    list_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
