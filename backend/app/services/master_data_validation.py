"""Shared crop/variety-vs-Master-Data validation (round 8-15D).

Business rule: a NEW planting cycle, and any CHANGE to an existing active
cycle's crop/variety, must reference values that exist in `master_data` and
are `active=true`; a variety must also belong to the chosen crop (`parent`).
Existing history is never invalidated retroactively — if a cycle's current
crop/variety pair is left unchanged, it's always allowed through even if the
value has since been deactivated (§ round 8-15D rule 4/5).

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

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.master_data import MasterData
from app.repositories import master_data_repository as master_data_repo

CROP_TYPE = "crop"
VARIETY_TYPE = "variety"


@dataclass(frozen=True)
class CropVarietyLookup:
    """Pre-fetched Master Data rows keyed by value, for O(1) per-row checks."""

    crops: dict[str, MasterData]
    varieties: dict[str, MasterData]


async def load_crop_variety_lookup(
    db: AsyncSession, crop_values: set[str], variety_values: set[str]
) -> CropVarietyLookup:
    """Batch-fetch every crop/variety value that might be needed — ONE query
    per type regardless of how many rows/callers reference them."""
    crops = await master_data_repo.list_by_type_values(db, CROP_TYPE, crop_values)
    varieties = await master_data_repo.list_by_type_values(db, VARIETY_TYPE, variety_values)
    return CropVarietyLookup(
        crops={c.value: c for c in crops},
        varieties={v.value: v for v in varieties},
    )


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def crop_variety_errors(
    lookup: CropVarietyLookup,
    crop: str | None,
    variety: str | None,
    *,
    current_crop: str | None = None,
    current_variety: str | None = None,
) -> list[str]:
    """Thai validation-error messages (empty list = valid) for the EFFECTIVE
    crop/variety pair a mutation would end up storing.

    `current_crop`/`current_variety` is the pair already stored on the cycle
    being updated (leave both None for a brand-new cycle). If the effective
    pair is IDENTICAL to the current one, it's a no-op for crop/variety and
    always allowed — even if the value has since been deactivated. Any
    actual change re-validates the WHOLE effective pair from scratch (not
    just the field that changed), so e.g. changing crop but leaving a
    variety string untouched still re-checks that variety's parent against
    the NEW crop.
    """
    crop = _norm(crop)
    variety = _norm(variety)
    current_crop = _norm(current_crop)
    current_variety = _norm(current_variety)

    if crop == current_crop and variety == current_variety:
        return []

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


async def assert_crop_variety_valid(
    db: AsyncSession,
    crop: str | None,
    variety: str | None,
    *,
    current_crop: str | None = None,
    current_variety: str | None = None,
) -> None:
    """Single-pair convenience wrapper for Lifecycle API endpoints — fetches
    only the rows needed for this one pair, then raises HTTPException(422)
    if invalid. Excel import (many rows per file) should call
    `load_crop_variety_lookup` once + `crop_variety_errors` per row instead,
    to avoid one query pair per row."""
    values_crop = {crop} if _norm(crop) else set()
    values_variety = {variety} if _norm(variety) else set()
    lookup = await load_crop_variety_lookup(db, values_crop, values_variety)
    errors = crop_variety_errors(
        lookup, crop, variety, current_crop=current_crop, current_variety=current_variety,
    )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
