"""Inspection protocol criteria repository (round 5.5).

Two readers with different audiences:
- load_active_protocol_map: what the record forms / snapshot builder consume —
  ACTIVE criteria only, grouped by stage, ordered, as plain {slot,label} dicts.
  Completeness (exactly-4-slots-per-stage) is enforced one layer up in the
  service, which also owns the built-in fallback.
- list_all_criteria: what the admin editor consumes — every row (incl.
  inactive) with its id, so a label can be PATCHed.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.inspection_protocol import InspectionProtocolCriterion


async def load_active_protocol_map(db: AsyncSession) -> dict[str, list[dict]]:
    """Active criteria grouped by growth_stage, ordered by order_index, as
    [{"slot": ..., "label": ...}]. Empty dict when the table is unseeded (the
    service then falls back to the built-in registry)."""
    result = await db.execute(
        select(InspectionProtocolCriterion)
        .where(InspectionProtocolCriterion.active.is_(True))
        .order_by(
            InspectionProtocolCriterion.growth_stage,
            InspectionProtocolCriterion.order_index,
        )
    )
    grouped: dict[str, list[dict]] = {}
    for row in result.scalars().all():
        grouped.setdefault(row.growth_stage, []).append(
            {"slot": row.slot, "label": row.label}
        )
    return grouped


async def list_all_criteria(db: AsyncSession) -> list[InspectionProtocolCriterion]:
    """Every criterion (active or not), ordered stage then slot-order — for
    the admin editor."""
    result = await db.execute(
        select(InspectionProtocolCriterion).order_by(
            InspectionProtocolCriterion.growth_stage,
            InspectionProtocolCriterion.order_index,
        )
    )
    return list(result.scalars().all())


async def get_criterion(
    db: AsyncSession, criterion_id: UUID
) -> InspectionProtocolCriterion | None:
    result = await db.execute(
        select(InspectionProtocolCriterion).where(
            InspectionProtocolCriterion.id == criterion_id
        )
    )
    return result.scalar_one_or_none()


async def get_criteria_by_ids(
    db: AsyncSession, ids: list[UUID]
) -> list[InspectionProtocolCriterion]:
    """Fetch the criteria named by `ids` — for the atomic bulk update. May
    return fewer than requested; the endpoint fails the whole batch if any id
    is missing."""
    if not ids:
        return []
    result = await db.execute(
        select(InspectionProtocolCriterion).where(
            InspectionProtocolCriterion.id.in_(ids)
        )
    )
    return list(result.scalars().all())


async def update_criterion_label(
    db: AsyncSession, criterion: InspectionProtocolCriterion, label: str
) -> InspectionProtocolCriterion:
    """Label-only edit (round 5.5 doesn't add/remove/reslot criteria). The
    slot binding and the 4-per-stage shape are immutable here."""
    criterion.label = label
    await db.flush()
    return criterion
