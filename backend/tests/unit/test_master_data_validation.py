"""Shared crop/P.Code-vs-Master-Data validation (round 8-15D, rewritten for
round Y).

Round 8-15D wrote this around a crop/variety PAIR the caller supplied, with
an "unchanged is still allowed" escape hatch for edits. Both halves are gone:
round V made crop/variety/cycleLabel/P.Code one-time (an edit cannot change
them, so there is nothing to re-validate and nothing to exempt), and round Y
took the variety out of the caller's hands entirely — a new cycle is a crop
plus a P.Code, and the variety is read off the P.Code.

What survives, and is tested here: the batching guarantees and the 422
wrapper. The per-rule cases live in test_variety_from_p_code_round_y.py,
where the rule itself is defined.

DB-free: `crop_p_code_errors` is pure (no I/O); `load_crop_p_code_lookup` /
`assert_crop_p_code_valid` are exercised with `master_data_repository.
list_by_type_values` patched — never a real database. These tests carry the
`nodefault_crop_variety` marker so tests/unit/conftest.py's permissive
autouse default doesn't shadow the very functions under test.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import master_data_validation as mdv

pytestmark = pytest.mark.nodefault_crop_variety


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


def _lookup(crops=None, varieties=None, p_codes=None) -> mdv.CropTaxonomyLookup:
    return mdv.CropTaxonomyLookup(
        crops={c.value: c for c in (crops or [])},
        varieties={v.value: v for v in (varieties or [])},
        p_codes={p.value: p for p in (p_codes or [])},
    )


# --- crop_p_code_errors — pure logic ---------------------------------------

def test_active_crop_alone_passes() -> None:
    # A crop with no P.Code yet is not this validator's problem: pCode
    # requiredness belongs to PlotCycleCreate and the import's own check.
    lookup = _lookup(crops=[_md("crop", "พริก")])
    assert mdv.crop_p_code_errors(lookup, "พริก", None) == []


def test_missing_crop_rejected() -> None:
    assert mdv.crop_p_code_errors(_lookup(), "พริก", None) == [
        'ไม่พบชนิดพืช "พริก" ใน Master Data'
    ]


def test_inactive_crop_rejected() -> None:
    lookup = _lookup(crops=[_md("crop", "พริก", active=False)])
    assert mdv.crop_p_code_errors(lookup, "พริก", None) == [
        'ชนิดพืช "พริก" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน'
    ]


def test_both_blank_passes() -> None:
    assert mdv.crop_p_code_errors(_lookup(), None, None) == []


def test_whitespace_only_normalizes_like_blank() -> None:
    assert mdv.crop_p_code_errors(_lookup(), "  ", "  ") == []


# --- load_crop_p_code_lookup — batching / N+1 ------------------------------

async def test_load_lookup_issues_one_query_per_type() -> None:
    """THREE queries for a whole file no matter how many values: crops,
    P.Codes, then the varieties those P.Codes name. The third is round Y's —
    the varieties are no longer an input, so they have to be fetched from
    what the P.Codes point at."""
    call_count = {"n": 0}

    async def fake(db, type_, values):
        call_count["n"] += 1
        if type_ == "crop":
            return [_md("crop", v) for v in values]
        if type_ == "p_code":
            return [_md("p_code", v, parent="พริกขี้หนู") for v in values]
        return [_md("variety", v, parent="พริก") for v in values]

    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(side_effect=fake),
    ):
        lookup = await mdv.load_crop_p_code_lookup(
            object(), {"พริก", "เมล่อน", "มะเขือเทศ"}, {"WM-111", "WM-141"},
        )
    assert call_count["n"] == 3  # ONE per type, regardless of value-set size
    assert set(lookup.crops) == {"พริก", "เมล่อน", "มะเขือเทศ"}
    assert set(lookup.p_codes) == {"WM-111", "WM-141"}
    assert set(lookup.varieties) == {"พริกขี้หนู"}


async def test_load_lookup_empty_sets_short_circuit_with_no_query() -> None:
    """Empty value-sets never touch the database at all — the short-circuit
    lives in master_data_repository.list_by_type_values itself (`if not
    values: return []`), so this calls the REAL repo function with a bare
    `object()` db and relies on it never reaching `db.execute(...)`."""
    lookup = await mdv.load_crop_p_code_lookup(object(), set(), set())
    assert lookup.crops == {} and lookup.varieties == {} and lookup.p_codes == {}


# --- assert_crop_p_code_valid — API convenience wrapper --------------------

async def test_assert_valid_pair_returns_the_derived_variety() -> None:
    async def fake(db, type_, values):
        if type_ == "crop":
            return [_md("crop", "พริก")]
        if type_ == "p_code":
            return [_md("p_code", "WM-111", parent="พริกขี้หนู")]
        return [_md("variety", "พริกขี้หนู", parent="พริก")]

    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(side_effect=fake),
    ):
        variety = await mdv.assert_crop_p_code_valid(object(), "พริก", "WM-111")
    # The endpoint stores exactly this — see
    # test_cycle_variety_from_p_code_api_round_y.py.
    assert variety == "พริกขี้หนู"


async def test_assert_invalid_pair_raises_422_with_thai_message() -> None:
    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(return_value=[]),
    ):
        with pytest.raises(HTTPException) as exc:
            await mdv.assert_crop_p_code_valid(object(), "พริก", None)
    assert exc.value.status_code == 422
    assert "ไม่พบ" in exc.value.detail
    # No stack trace / internal id ever in the detail.
    assert "Traceback" not in exc.value.detail
