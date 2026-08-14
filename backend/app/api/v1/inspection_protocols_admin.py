"""Inspection protocol admin editor (round 5.5).

View and edit the growth-stage → 4-criteria-label config the record forms and
snapshot builder read. Kept separate from the read-only
GET /api/v1/inspection-protocols so that endpoint stays purely read; the
admin surface lives under /api/v1/admin/inspection-protocols, gated by the
existing master-data permissions (no new permission).

Round 5.5 edits LABELS only — the slot binding and the 4-criteria-per-stage
shape are immutable (no create/delete/reslot here). Records freeze their
protocol at create time, so an edit here never rewrites history.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.auth.permissions import PermissionKey
from app.db.session import get_db
from app.repositories import inspection_protocol_repository as repo
from app.schemas.inspection_protocol import (
    InspectionProtocolAdminCriterion,
    InspectionProtocolAdminList,
    InspectionProtocolAdminStage,
    InspectionProtocolBulkUpdate,
    InspectionProtocolCriterionUpdate,
)
from app.services import inspection_protocols as protocols

router = APIRouter(tags=["inspection-protocols-admin"])


@router.get("", response_model=InspectionProtocolAdminList, dependencies=[
    Depends(require_permission(PermissionKey.MASTERDATA_READ))
])
async def list_admin_inspection_protocols(
    db: AsyncSession = Depends(get_db),
) -> InspectionProtocolAdminList:
    """Every criterion (with its id/order/active) grouped by stage — the
    admin editor's data. Reads the real rows, not the fallback registry."""
    rows = await repo.list_all_criteria(db)
    stages: dict[str, list[InspectionProtocolAdminCriterion]] = {}
    for row in rows:
        stages.setdefault(row.growth_stage, []).append(
            InspectionProtocolAdminCriterion.model_validate(row)
        )
    return InspectionProtocolAdminList(
        version=protocols.PROTOCOL_VERSION,
        stages=[
            InspectionProtocolAdminStage(growth_stage=stage, criteria=criteria)
            for stage, criteria in stages.items()
        ],
    )


@router.patch(
    "/criteria",
    response_model=list[InspectionProtocolAdminCriterion],
    dependencies=[Depends(require_permission(PermissionKey.MASTERDATA_UPDATE))],
)
async def bulk_update_inspection_protocol_criteria(
    payload: InspectionProtocolBulkUpdate,
    db: AsyncSession = Depends(get_db),
) -> list[InspectionProtocolAdminCriterion]:
    """Atomic multi-label edit — all items succeed or none do. If any id is
    missing the whole batch fails (404) before anything is written, so a
    stage's 4 labels never partially save. Labels are trimmed + non-blank
    validated by the schema."""
    ids = [item.id for item in payload.items]
    found = {c.id: c for c in await repo.get_criteria_by_ids(db, ids)}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Protocol criteria not found: {', '.join(missing)}",
        )
    for item in payload.items:
        found[item.id].label = item.label
    await db.flush()
    return [InspectionProtocolAdminCriterion.model_validate(found[item.id]) for item in payload.items]


@router.patch(
    "/criteria/{criterion_id}",
    response_model=InspectionProtocolAdminCriterion,
    dependencies=[Depends(require_permission(PermissionKey.MASTERDATA_UPDATE))],
)
async def update_inspection_protocol_criterion(
    criterion_id: UUID,
    payload: InspectionProtocolCriterionUpdate,
    db: AsyncSession = Depends(get_db),
) -> InspectionProtocolAdminCriterion:
    criterion = await repo.get_criterion(db, criterion_id)
    if criterion is None:
        raise HTTPException(status_code=404, detail="Protocol criterion not found")
    await repo.update_criterion_label(db, criterion, payload.label.strip())
    return InspectionProtocolAdminCriterion.model_validate(criterion)
