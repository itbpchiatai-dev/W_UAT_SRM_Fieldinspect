"""Public (unauthenticated) master-data endpoint — round 19.1.

Read-only dropdown options for the /public/inspect field-assistant flow
(crop, variety, growth_stage, weather) so free text there doesn't drift
from the same taxonomy the logged-in RecordForm/Plot admin forms already
use (round 19). No login, no RLS (master_data carries no supplier/user
scope — see migration 0019), and no mutation route in this router at all.

Security (round 19.1's brief):
  - `type` is a Literal allowlist, not a free string — FastAPI/Pydantic
    rejects anything outside {crop, variety, growth_stage, weather} with a
    422 before this function body even runs. No manual "if type not in
    ..." check to get wrong, and no way to reach master_data rows of an
    internal-only type (e.g. level/severity/irrigation/fertilizer, which
    stay authenticated-only via GET /api/v1/masterdata).
  - active_only is hardcoded True, never a caller-controlled parameter —
    inactive/retired options are never reachable here regardless of what a
    caller asks for.
  - Response is PublicMasterDataItem (value + parent only) — no id, no
    type, no order_index, no active flag, no timestamps.
  - Rate-limited (30/min/IP) — generous enough for a form loading crop
    once and variety on every crop change, but still bounded.
  - Goes through master_data_repository.list_items, the same
    parameterized-query repository the authenticated endpoint uses — no
    raw/string SQL anywhere in this path.

Note: this module deliberately does NOT use `from __future__ import
annotations`, same reason as public_plots.py/public_records.py — slowapi's
@limiter.limit wraps the route with functools.wraps, which would break
FastAPI's forward-ref resolution under PEP 563.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.db.session import get_db
from app.repositories import master_data_repository as repo
from app.schemas.master_data import PublicMasterDataItem

router = APIRouter(tags=["public"])

PublicMasterDataType = Literal["crop", "variety", "growth_stage", "weather"]


@router.get("/masterdata", response_model=list[PublicMasterDataItem])
@limiter.limit("30/minute")
async def list_public_master_data(
    request: Request,
    type: PublicMasterDataType,
    parent: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[PublicMasterDataItem]:
    items = await repo.list_items(db, type=type, parent=parent, active_only=True)
    return [PublicMasterDataItem(value=i.value, parent=i.parent) for i in items]
