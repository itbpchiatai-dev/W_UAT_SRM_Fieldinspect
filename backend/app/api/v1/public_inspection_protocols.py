"""Public (unauthenticated) inspection-protocol read — round 5.1.

The same version + stage/criteria labels as the logged-in
GET /api/v1/inspection-protocols, for the public /public/inspect flow so its
score inputs show the correct stage-specific labels (mirrors how
public_masterdata.py exposes the dropdown options to that same flow). No
login, no RLS (protocol data is global reference — no supplier/user scope),
and no mutation route in this router at all. Response carries only version +
stage + slot/label — no secret, token, or hash. Rate-limited like the
sibling public master-data endpoint.

Note: this module deliberately does NOT use `from __future__ import
annotations`, same reason as public_masterdata.py / public_records.py —
slowapi's @limiter.limit wraps the route with functools.wraps, which would
break FastAPI's forward-ref resolution under PEP 563.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.db.session import get_db
from app.schemas.inspection_protocol import InspectionProtocolList
from app.services import inspection_protocols as protocols

router = APIRouter(tags=["public"])


@router.get("/inspection-protocols", response_model=InspectionProtocolList)
@limiter.limit("30/minute")
async def list_public_inspection_protocols(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InspectionProtocolList:
    # Same admin-editable config (round 5.5) as the logged-in endpoint, with
    # the built-in registry as fallback.
    protocol_map = await protocols.get_protocol_map(db)
    return InspectionProtocolList(
        version=protocols.PROTOCOL_VERSION,
        stages=protocols.list_protocols_from_map(protocol_map),
    )
