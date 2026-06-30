"""MasterData — editable dropdown option source (Step 12.5, Spec §3.5).

Rows are grouped by `type` (crop / variety / growth_stage / weather / level /
severity / irrigation / fertilizer / province / …). `parent` chains a value to
its owner (e.g. variety → crop). FieldDefinition.list fields point here via
`options_source = "masterdata:<type>"`.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class MasterData(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "master_data"

    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    parent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
