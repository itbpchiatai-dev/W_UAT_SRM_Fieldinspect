"""Inspection protocol contract — read-only reference for the record form
(logged-in). Round 5.1.

Gated by records.read, exactly like GET /api/v1/masterdata: it's the
option/label data the RecordForm needs to render its 4 stage-specific score
inputs, carrying no secrets. The protocol itself is the backend source of
truth (app/services/inspection_protocols.py); this endpoint only exposes the
version + stage/criteria labels. Read-only — no create/update/delete here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_permission
from app.auth.permissions import PermissionKey
from app.db.session import get_db
from app.schemas.inspection_protocol import InspectionProtocolList
from app.services import inspection_protocols as protocols

router = APIRouter(tags=["inspection-protocols"])


# Reference data the RecordForm needs — /farmlog/records/new is opened with
# records.create, so accept EITHER records.read or records.create (round 5.6),
# same pattern as the plots /lookup endpoint.
@router.get("", response_model=InspectionProtocolList, dependencies=[
    Depends(require_any_permission(PermissionKey.RECORDS_READ, PermissionKey.RECORDS_CREATE))
])
async def list_inspection_protocols(
    db: AsyncSession = Depends(get_db),
) -> InspectionProtocolList:
    # Reads the admin-editable config (round 5.5); falls back to the built-in
    # registry when the table is unseeded — see get_protocol_map.
    protocol_map = await protocols.get_protocol_map(db)
    return InspectionProtocolList(
        version=protocols.PROTOCOL_VERSION,
        stages=protocols.list_protocols_from_map(protocol_map),
    )
