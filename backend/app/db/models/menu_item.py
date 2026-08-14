"""MenuItem — sidebar nav node (filtered server-side by permission)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    pass


class MenuItem(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "menu_items"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    label_th: Mapped[str] = mapped_column(String(150), nullable=False)
    label_en: Mapped[str] = mapped_column(String(150), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(50))
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE")
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_permission_key: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    children: Mapped[list["MenuItem"]] = relationship(
        "MenuItem",
        cascade="all, delete-orphan",
        backref="parent",
        remote_side="MenuItem.id",
        single_parent=True,
    )
