"""Shared crop/P.Code-vs-Master-Data validation (round 8-15D; P.Code added
round 8-26C; rewritten round Y).

Business rule: a NEW planting cycle names a crop and a P.Code, and both must
exist in `master_data` and be `active=true`. The VARIETY is not named at all
— it is read off the P.Code, whose `parent` IS its variety (round 8-26A's
crop → variety → p_code chain) — and that derived variety must itself be
active and sit under the chosen crop.

Round 8-26C read the same chain the other way (pick a variety, derive its one
active P.Code). Round Y turned it around because that is the pair people work
with, "WM" and "CTT-507", and because upward there is exactly one answer: a
P.Code belongs to one variety, while a crop owns many.

Only CREATE reaches here. Round V made crop/variety/cycleLabel/P.Code
one-time (an edit cannot change them, so there is nothing to re-validate),
which is why round 8-15D's "an unchanged pair is always allowed" escape hatch
is gone: nothing can be unchanged-but-invalid any more.

Two call shapes:
- `assert_crop_p_code_valid` — single-row convenience wrapper for Lifecycle
  API endpoints: raises HTTPException 422 on failure, and returns the derived
  variety for the caller to store.
- `load_crop_p_code_lookup` + `crop_p_code_errors` + `variety_for_p_code` —
  batch shape for Plot Excel Import: fetch every value a file touches in
  exactly three queries (`master_data_repository.list_by_type_values`), then
  check each row against the in-memory lookup with zero additional queries
  (no N+1 — see `plot_import.py`'s `_apply_cycle_label_history_checks` for
  the same two-pass batching pattern this mirrors).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.master_data import MasterData
from app.repositories import master_data_repository as master_data_repo
from app.services.p_code_master import P_CODE_TYPE

CROP_TYPE = "crop"
VARIETY_TYPE = "variety"

# PlotCycle.variety is String(100) while master_data.value allows 255, so a
# variety name can be legal in Master Data and still too long to store on a
# cycle. Round Y moved this guard here, next to the derivation: nobody types
# the variety any more, so the P.Code that pulls it in is the last place the
# length can be reported as an error instead of a DataError at INSERT.
_MAX_STORED_VARIETY = 100


VARIETY_IS_DERIVED_MESSAGE = (
    "พันธุ์/สายพันธุ์มาจาก P.Code ที่เลือก ระบบเติมให้เอง "
    "กรุณาเลือกชนิดพืชกับ P.Code เท่านั้น"
)


@dataclass(frozen=True)
class CropTaxonomyLookup:
    """Pre-fetched Master Data rows keyed by value, for O(1) per-row checks.

    All three levels of crop → variety → p_code, because round Y's one check
    spans all three: the P.Code is what the user picked, the variety is what
    it derives to, and the crop is what that variety has to sit under."""

    crops: dict[str, MasterData]
    varieties: dict[str, MasterData]
    p_codes: dict[str, MasterData] = field(default_factory=dict)


async def load_crop_p_code_lookup(
    db: AsyncSession,
    crop_values: set[str],
    p_code_values: set[str],
) -> CropTaxonomyLookup:
    """Batch-fetch what a file's crop + P.Code values need — a fixed THREE
    queries regardless of how many rows reference them.

    The varieties are not among the inputs any more (round Y took the column
    away), so they are fetched in a second pass: the P.Code rows name their
    varieties in `parent`, and those rows are what the crop check below
    compares against. A file mentioning no P.Code at all skips that query —
    `list_by_type_values` short-circuits on an empty set."""
    crops = await master_data_repo.list_by_type_values(db, CROP_TYPE, crop_values)
    p_codes = await master_data_repo.list_by_type_values(db, P_CODE_TYPE, p_code_values)
    variety_values = {p.parent for p in p_codes if p.parent}
    varieties = await master_data_repo.list_by_type_values(db, VARIETY_TYPE, variety_values)
    return CropTaxonomyLookup(
        crops={c.value: c for c in crops},
        varieties={v.value: v for v in varieties},
        p_codes={p.value: p for p in p_codes},
    )


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _crop_errors(lookup: CropTaxonomyLookup, crop: str) -> list[str]:
    crop_row = lookup.crops.get(crop)
    if crop_row is None:
        return [f'ไม่พบชนิดพืช "{crop}" ใน Master Data']
    if not crop_row.active:
        return [
            f'ชนิดพืช "{crop}" ถูกปิดใช้งาน '
            "กรุณาเปิดใช้งานใน Master Data ก่อน"
        ]
    return []


def variety_for_p_code(lookup: CropTaxonomyLookup, p_code: str | None) -> str | None:
    """The variety a P.Code belongs to — its Master Data `parent` (round Y).

    This is the ONE place the variety of a new cycle comes from. It answers
    "which variety is this?" and deliberately not "may this be used?": a
    DEACTIVATED P.Code still names its variety, which is what lets
    crop_p_code_errors below say which variety a refused P.Code belonged to.
    An unknown P.Code derives nothing rather than guessing — the caller
    reports it as unknown instead of storing a variety nobody chose."""
    p_code = _norm(p_code)
    if not p_code:
        return None
    row = lookup.p_codes.get(p_code)
    return row.parent if row is not None else None


def crop_p_code_errors(
    lookup: CropTaxonomyLookup, crop: str | None, p_code: str | None,
) -> list[str]:
    """Thai validation-error messages (empty list = valid) for the crop +
    P.Code a NEW cycle would be created with (round Y).

    Three things have to hold, and the middle one is the reason this function
    spans all three Master Data levels: the crop exists and is active; the
    P.Code exists and is active; and the VARIETY the P.Code derives to is
    itself active and sits under that crop. Checking the derived variety
    rather than a typed one is the whole point of the round — the user picks
    WM + CTT-507, and the pairing of those two is exactly what can be wrong.

    A blank P.Code is not an error here. Requiredness lives where it always
    has — PlotCycleCreate for the API, "ต้องระบุ pCode" for the Excel import —
    and repeating it would mean two places to change when it moves."""
    crop = _norm(crop)
    p_code = _norm(p_code)

    if p_code and not crop:
        return ["กรุณาระบุชนิดพืชก่อนเลือก P.Code"]

    errors: list[str] = []
    if crop:
        errors.extend(_crop_errors(lookup, crop))
    if not p_code:
        return errors

    p_code_row = lookup.p_codes.get(p_code)
    if p_code_row is None:
        errors.append(f'ไม่พบ P.Code "{p_code}" ใน Master Data')
        return errors
    if not p_code_row.active:
        errors.append(
            f'P.Code "{p_code}" ถูกปิดใช้งาน '
            "กรุณาเปิดใช้งานใน Master Data ก่อน"
        )
        return errors

    variety = p_code_row.parent
    variety_row = lookup.varieties.get(variety) if variety else None
    if variety_row is None:
        errors.append(f'ไม่พบพันธุ์ "{variety}" ใน Master Data')
    elif not variety_row.active:
        errors.append(
            f'พันธุ์ "{variety}" ถูกปิดใช้งาน '
            "กรุณาเปิดใช้งานใน Master Data ก่อน"
        )
    elif len(variety) > _MAX_STORED_VARIETY:
        errors.append(
            f'พันธุ์ของ P.Code "{p_code}" ยาวเกิน {_MAX_STORED_VARIETY} ตัวอักษร '
            "จึงบันทึกลงรอบปลูกไม่ได้ กรุณาแก้ชื่อพันธุ์ให้สั้นลงใน Master Data"
        )
    elif crop and variety_row.parent != crop:
        # Named by the P.Code, not by the variety: the P.Code is what the
        # user chose, so it is what they can act on.
        errors.append(f'P.Code "{p_code}" ไม่ได้อยู่ภายใต้ชนิดพืช "{crop}"')
    return errors


async def assert_crop_p_code_valid(
    db: AsyncSession, crop: str | None, p_code: str | None,
) -> str | None:
    """Single-row convenience wrapper for the Lifecycle API endpoints —
    fetches only the rows this one cycle needs, raises HTTPException(422) if
    the pair is invalid, and RETURNS THE DERIVED VARIETY for the caller to
    store. Returning it here is what keeps derivation and validation from
    drifting apart: there is no way to save a cycle whose variety was not
    just checked.

    Excel import (many rows per file) calls `load_crop_p_code_lookup` once +
    `crop_p_code_errors` + `variety_for_p_code` per row instead, to avoid one
    set of queries per row."""
    lookup = await load_crop_p_code_lookup(
        db,
        {crop} if _norm(crop) else set(),
        {p_code} if _norm(p_code) else set(),
    )
    errors = crop_p_code_errors(lookup, crop, p_code)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    return variety_for_p_code(lookup, p_code)
