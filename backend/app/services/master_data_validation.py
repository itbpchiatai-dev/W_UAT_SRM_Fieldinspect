"""Shared crop/variety/P.Code-vs-Master-Data validation (round 8-15D; P.Code
added round 8-26C).

Business rule: a NEW planting cycle, and any CHANGE to an existing active
cycle's crop/variety, must reference values that exist in `master_data` and
are `active=true`; a variety must also belong to the chosen crop (`parent`).
Existing history is never invalidated retroactively — if a cycle's current
crop/variety pair is left unchanged, it's always allowed through even if the
value has since been deactivated (§ round 8-15D rule 4/5).

Round 8-26C applies the SAME two rules to `p_code`, one level down
(crop → variety → p_code): a P.Code must exist, be active, and belong to the
cycle's effective variety — but an UNCHANGED P.Code always passes, which is
what keeps every cycle created before P.Code became master data editable.
That escape hatch is evaluated per-field: changing only the crop never
re-validates an untouched legacy P.Code, and changing only the P.Code never
re-validates an untouched legacy crop/variety pair.

Two call shapes:
- `assert_crop_variety_valid` — single-pair convenience wrapper for Lifecycle
  API endpoints (one row, raises HTTPException 422 on failure).
- `load_crop_variety_lookup` + `crop_variety_errors` — batch shape for Plot
  Excel Import: fetch ALL crop/variety values touched by a file in exactly
  two queries (`master_data_repository.list_by_type_values`), then check
  each row against the in-memory lookup with zero additional queries (no
  N+1 — see `plot_import.py`'s `_apply_cycle_label_history_checks` for the
  same two-pass batching pattern this mirrors).
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


@dataclass(frozen=True)
class CropVarietyLookup:
    """Pre-fetched Master Data rows keyed by value, for O(1) per-row checks."""

    crops: dict[str, MasterData]
    varieties: dict[str, MasterData]
    # Round 8-26C. Defaulted so an existing caller that only needs
    # crop/variety keeps working unchanged — an empty dict simply means "no
    # P.Code was asked about", never "no P.Code exists".
    p_codes: dict[str, MasterData] = field(default_factory=dict)


async def load_crop_variety_lookup(
    db: AsyncSession,
    crop_values: set[str],
    variety_values: set[str],
    p_code_values: set[str] | None = None,
) -> CropVarietyLookup:
    """Batch-fetch every crop/variety/P.Code value that might be needed — ONE
    query per type regardless of how many rows/callers reference them. An
    empty/omitted `p_code_values` short-circuits its query entirely (see
    master_data_repository.list_by_type_values)."""
    crops = await master_data_repo.list_by_type_values(db, CROP_TYPE, crop_values)
    varieties = await master_data_repo.list_by_type_values(db, VARIETY_TYPE, variety_values)
    p_codes = await master_data_repo.list_by_type_values(db, P_CODE_TYPE, p_code_values or set())
    return CropVarietyLookup(
        crops={c.value: c for c in crops},
        varieties={v.value: v for v in varieties},
        p_codes={p.value: p for p in p_codes},
    )


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _crop_variety_pair_errors(
    lookup: CropVarietyLookup, crop: str | None, variety: str | None,
) -> list[str]:
    errors: list[str] = []
    if variety and not crop:
        errors.append("กรุณาระบุชนิดพืชก่อนเลือกพันธุ์")
        return errors

    if crop:
        crop_row = lookup.crops.get(crop)
        if crop_row is None:
            errors.append(f'ไม่พบชนิดพืช "{crop}" ใน Master Data')
        elif not crop_row.active:
            errors.append(f'ชนิดพืช "{crop}" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน')

    if variety:
        variety_row = lookup.varieties.get(variety)
        if variety_row is None:
            errors.append(f'ไม่พบพันธุ์ "{variety}" ใน Master Data')
        elif not variety_row.active:
            errors.append(f'พันธุ์ "{variety}" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน')
        elif crop and variety_row.parent != crop:
            errors.append(f'พันธุ์ "{variety}" ไม่ได้อยู่ภายใต้ชนิดพืช "{crop}"')

    return errors


def _p_code_errors(
    lookup: CropVarietyLookup, p_code: str | None, variety: str | None,
) -> list[str]:
    """Round 8-26C — the same exists/active/belongs-to-parent shape as
    variety, one level down. A blank P.Code is never an error here: the DB
    only demands one for an Auto Lot, and that requirement is enforced by
    plot_cycle_repository's own AutoLotMissingComponentError, not by Master
    Data validation."""
    if not p_code:
        return []
    if not variety:
        return ["กรุณาระบุพันธุ์ก่อนกำหนด P.Code"]
    p_code_row = lookup.p_codes.get(p_code)
    if p_code_row is None:
        return [f'ไม่พบ P.Code "{p_code}" ใน Master Data']
    if not p_code_row.active:
        return [f'P.Code "{p_code}" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน']
    if p_code_row.parent != variety:
        return [f'P.Code "{p_code}" ไม่ได้อยู่ภายใต้พันธุ์ "{variety}"']
    return []


def crop_variety_errors(
    lookup: CropVarietyLookup,
    crop: str | None,
    variety: str | None,
    *,
    current_crop: str | None = None,
    current_variety: str | None = None,
    p_code: str | None = None,
    current_p_code: str | None = None,
) -> list[str]:
    """Thai validation-error messages (empty list = valid) for the EFFECTIVE
    crop/variety pair — and, since round 8-26C, the effective P.Code — a
    mutation would end up storing.

    `current_*` is what the cycle being updated already stores (leave them
    None for a brand-new cycle). If the effective crop/variety pair is
    IDENTICAL to the current one, it's a no-op for crop/variety and always
    allowed — even if the value has since been deactivated. Any actual change
    re-validates the WHOLE effective pair from scratch (not just the field
    that changed), so e.g. changing crop but leaving a variety string
    untouched still re-checks that variety's parent against the NEW crop.

    The P.Code gets its OWN unchanged-is-allowed check rather than sharing
    the pair's: every cycle that predates round 8-26A carries a free-text
    P.Code that is not in Master Data, and those cycles must stay editable.
    Re-parenting the escape hatch onto the crop/variety pair would mean
    editing a legacy cycle's planting date (crop/variety untouched, P.Code
    untouched) is fine, but fixing its crop would suddenly demand the user
    also fix a P.Code they never touched. Keeping the two independent is
    what the user asked for: old values stay as they are, new values must be
    correct.
    """
    crop = _norm(crop)
    variety = _norm(variety)
    current_crop = _norm(current_crop)
    current_variety = _norm(current_variety)
    p_code = _norm(p_code)
    current_p_code = _norm(current_p_code)

    errors: list[str] = []
    if not (crop == current_crop and variety == current_variety):
        errors.extend(_crop_variety_pair_errors(lookup, crop, variety))
    if p_code != current_p_code:
        errors.extend(_p_code_errors(lookup, p_code, variety))
    return errors


async def assert_crop_variety_valid(
    db: AsyncSession,
    crop: str | None,
    variety: str | None,
    *,
    current_crop: str | None = None,
    current_variety: str | None = None,
    p_code: str | None = None,
    current_p_code: str | None = None,
) -> None:
    """Single-pair convenience wrapper for Lifecycle API endpoints — fetches
    only the rows needed for this one pair, then raises HTTPException(422)
    if invalid. Excel import (many rows per file) should call
    `load_crop_variety_lookup` once + `crop_variety_errors` per row instead,
    to avoid one query pair per row."""
    values_crop = {crop} if _norm(crop) else set()
    values_variety = {variety} if _norm(variety) else set()
    values_p_code = {p_code} if _norm(p_code) else set()
    lookup = await load_crop_variety_lookup(db, values_crop, values_variety, values_p_code)
    errors = crop_variety_errors(
        lookup, crop, variety,
        current_crop=current_crop, current_variety=current_variety,
        p_code=p_code, current_p_code=current_p_code,
    )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
