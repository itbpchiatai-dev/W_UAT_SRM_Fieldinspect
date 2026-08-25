"""P.Code Master Data — the "one variety owns one P.Code" rule (round 8-26A).

Business shape, confirmed with the user before this round:

  P.Code lives in `master_data` as type='p_code', with `parent` set to the
  VARIETY value it belongs to — one level BELOW variety (crop → variety →
  p_code), not beside it. That is what the live data says: on UAT the crop
  'พริก' already carries TWO different P.Codes (WM-111 under พริกขี้หนู,
  WM-141 under พริกจินดา), so a P.Code can never be a property of the crop.

  A variety owns AT MOST ONE **ACTIVE** P.Code. Deactivated rows are
  deliberately NOT counted: replacing a variety's P.Code means deactivating
  the old row and adding a new one, and the old row has to survive because
  cycles that already embedded it in their Lot No still reference that exact
  string (see services/lot_number.py — a lot number is stored, never
  recomputed). Counting only ACTIVE rows is what makes replacement possible
  at all; counting every row would permanently burn a variety after its
  first P.Code.

No schema change was needed for any of this: `master_data.parent` already
carries variety → crop exactly this way, and the table's own
UNIQUE(type, value) index (migration 0019, uq_master_data_type_value) gives
a P.Code value global uniqueness for free — the create/update endpoints'
existing duplicate handling covers that half, and this module only adds the
part the index cannot express (one ACTIVE row per parent).

Deliberately NOT checked here: whether the parent variety is still active.
A deactivated variety must stay editable — blocking on it would make an
existing P.Code impossible to fix or turn off, which is the same
"never invalidate history retroactively" rule
services/master_data_validation.py already follows for crop/variety.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.master_data import MasterData
from app.repositories import master_data_repository as repo

P_CODE_TYPE = "p_code"
VARIETY_TYPE = "variety"

_MSG_PARENT_REQUIRED = "กรุณาระบุพันธุ์ที่ P.Code นี้สังกัด"
_MSG_PARENT_UNKNOWN = 'ไม่พบพันธุ์ "{variety}" ใน Master Data'
_MSG_ALREADY_TAKEN = (
    'พันธุ์ "{variety}" มี P.Code "{existing}" อยู่แล้ว — 1 พันธุ์มีได้เพียง 1 P.Code '
    "กรุณาปิดใช้งานรายการเดิมก่อน"
)


async def active_p_code_for_variety(
    db: AsyncSession, variety: str, *, exclude_id: UUID | None = None
) -> MasterData | None:
    """The ONE active P.Code of `variety`, or None. `exclude_id` skips the row
    being edited, so re-saving a P.Code never collides with itself."""
    rows = await repo.list_items(db, type=P_CODE_TYPE, parent=variety, active_only=True)
    for row in rows:
        if exclude_id is not None and row.id == exclude_id:
            continue
        return row
    return None


async def p_code_assignment_errors(
    db: AsyncSession, variety: str | None, *, exclude_id: UUID | None = None
) -> list[str]:
    """Thai error messages (empty list = OK) for pointing a P.Code at
    `variety`. Batch/loop callers (round 8-26B's Excel import) use this form;
    single-row API callers use assert_p_code_assignable below."""
    variety = (variety or "").strip()
    if not variety:
        return [_MSG_PARENT_REQUIRED]
    if await repo.get_by_type_value(db, VARIETY_TYPE, variety) is None:
        return [_MSG_PARENT_UNKNOWN.format(variety=variety)]
    taken = await active_p_code_for_variety(db, variety, exclude_id=exclude_id)
    if taken is not None:
        return [_MSG_ALREADY_TAKEN.format(variety=variety, existing=taken.value)]
    return []


async def assert_p_code_assignable(
    db: AsyncSession, variety: str | None, *, exclude_id: UUID | None = None
) -> None:
    """Raise HTTPException(422) if a P.Code cannot point at `variety`. Same
    service-raises-HTTPException shape as
    master_data_validation.assert_crop_variety_valid."""
    errors = await p_code_assignment_errors(db, variety, exclude_id=exclude_id)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
