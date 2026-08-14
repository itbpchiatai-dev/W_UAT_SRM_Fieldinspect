"""Shared crop/variety-vs-Master-Data validation (round 8-15D).

DB-free: `crop_variety_errors` is pure (no I/O); `load_crop_variety_lookup`/
`assert_crop_variety_valid` are exercised with `master_data_repository.
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


def _lookup(crops=None, varieties=None) -> mdv.CropVarietyLookup:
    crops = crops or []
    varieties = varieties or []
    return mdv.CropVarietyLookup(
        crops={c.value: c for c in crops}, varieties={v.value: v for v in varieties},
    )


# --- crop_variety_errors — pure logic --------------------------------------

def test_active_crop_alone_passes() -> None:
    lookup = _lookup(crops=[_md("crop", "พริก")])
    assert mdv.crop_variety_errors(lookup, "พริก", None) == []


def test_missing_crop_rejected() -> None:
    lookup = _lookup()
    errors = mdv.crop_variety_errors(lookup, "พริก", None)
    assert len(errors) == 1
    assert "ไม่พบ" in errors[0] and "พริก" in errors[0]


def test_inactive_crop_rejected() -> None:
    lookup = _lookup(crops=[_md("crop", "พริก", active=False)])
    errors = mdv.crop_variety_errors(lookup, "พริก", None)
    assert len(errors) == 1
    assert "ปิดใช้งาน" in errors[0]


def test_active_variety_under_correct_crop_passes() -> None:
    lookup = _lookup(
        crops=[_md("crop", "พริก")],
        varieties=[_md("variety", "พริกขี้หนู", parent="พริก")],
    )
    assert mdv.crop_variety_errors(lookup, "พริก", "พริกขี้หนู") == []


def test_missing_variety_rejected() -> None:
    lookup = _lookup(crops=[_md("crop", "พริก")])
    errors = mdv.crop_variety_errors(lookup, "พริก", "พริกขี้หนู")
    assert any("ไม่พบพันธุ์" in e for e in errors)


def test_inactive_variety_rejected() -> None:
    lookup = _lookup(
        crops=[_md("crop", "พริก")],
        varieties=[_md("variety", "พริกขี้หนู", parent="พริก", active=False)],
    )
    errors = mdv.crop_variety_errors(lookup, "พริก", "พริกขี้หนู")
    assert any("ปิดใช้งาน" in e for e in errors)


def test_variety_wrong_parent_rejected() -> None:
    lookup = _lookup(
        crops=[_md("crop", "พริก"), _md("crop", "เมล่อน")],
        varieties=[_md("variety", "พริกขี้หนู", parent="พริก")],
    )
    errors = mdv.crop_variety_errors(lookup, "เมล่อน", "พริกขี้หนู")
    assert any("ไม่ได้อยู่ภายใต้" in e for e in errors)


def test_variety_without_crop_rejected() -> None:
    lookup = _lookup(varieties=[_md("variety", "พริกขี้หนู", parent="พริก")])
    errors = mdv.crop_variety_errors(lookup, None, "พริกขี้หนู")
    assert errors == ["กรุณาระบุชนิดพืชก่อนเลือกพันธุ์"]


def test_both_blank_passes() -> None:
    assert mdv.crop_variety_errors(_lookup(), None, None) == []
    assert mdv.crop_variety_errors(_lookup(), "", "") == []


# --- "unchanged legacy value" exemption (update semantics) -----------------

def test_unchanged_pair_passes_even_when_inactive_or_missing() -> None:
    """Rule 4/5 — a cycle's existing crop/variety, left untouched, is never
    invalidated retroactively even if since deactivated (or the lookup finds
    nothing at all, e.g. an inactive value not returned by the batch query)."""
    lookup = _lookup()  # empty — as if deactivated/removed from the active set
    errors = mdv.crop_variety_errors(
        lookup, "พริก", "พริกขี้หนู", current_crop="พริก", current_variety="พริกขี้หนู",
    )
    assert errors == []


def test_editing_unrelated_field_is_equivalent_to_unchanged_pair() -> None:
    """A cycle edit that doesn't touch crop/variety at all passes the SAME
    (unchanged) crop/variety through as both 'new' and 'current' — covers
    'แก้ field อื่นโดยไม่เปลี่ยน Crop/Variety ได้' even when the legacy value is
    inactive."""
    lookup = _lookup(crops=[_md("crop", "พริก", active=False)])
    errors = mdv.crop_variety_errors(
        lookup, "พริก", None, current_crop="พริก", current_variety=None,
    )
    assert errors == []


def test_crop_changed_variety_string_unchanged_still_revalidates_parent() -> None:
    """'ถ้าเปลี่ยน crop แต่ไม่ส่ง variety และ variety เดิมไม่เข้ากับ crop ใหม่ ต้อง 422' —
    variety's raw string is IDENTICAL to current, but crop changed, so the
    WHOLE effective pair is re-validated, catching the now-mismatched parent."""
    lookup = _lookup(
        crops=[_md("crop", "เมล่อน")],
        varieties=[_md("variety", "พริกขี้หนู", parent="พริก")],  # still parented to the OLD crop
    )
    errors = mdv.crop_variety_errors(
        lookup, "เมล่อน", "พริกขี้หนู", current_crop="พริก", current_variety="พริกขี้หนู",
    )
    assert any("ไม่ได้อยู่ภายใต้" in e for e in errors)


def test_changed_to_inactive_value_rejected() -> None:
    lookup = _lookup(crops=[_md("crop", "เมล่อน", active=False)])
    errors = mdv.crop_variety_errors(
        lookup, "เมล่อน", None, current_crop="พริก", current_variety=None,
    )
    assert any("ปิดใช้งาน" in e for e in errors)


def test_clearing_to_blank_is_allowed() -> None:
    """Changing FROM a (possibly now-inactive) value TO blank is always fine
    — blank needs no Master Data membership."""
    lookup = _lookup()
    errors = mdv.crop_variety_errors(
        lookup, None, None, current_crop="พริก", current_variety="พริกขี้หนู",
    )
    assert errors == []


def test_whitespace_only_normalizes_like_blank() -> None:
    assert mdv.crop_variety_errors(_lookup(), "  ", None, current_crop=None) == []


# --- load_crop_variety_lookup — batching / N+1 ------------------------------

async def test_load_lookup_issues_exactly_two_queries() -> None:
    call_count = {"n": 0}

    async def fake(db, type_, values):
        call_count["n"] += 1
        if type_ == "crop":
            return [_md("crop", v) for v in values]
        return [_md("variety", v, parent="พริก") for v in values]

    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(side_effect=fake),
    ):
        lookup = await mdv.load_crop_variety_lookup(
            object(), {"พริก", "เมล่อน", "มะเขือเทศ"}, {"พริกขี้หนู", "ญี่ปุ่น"},
        )
    assert call_count["n"] == 2  # ONE per type, regardless of value-set size
    assert set(lookup.crops) == {"พริก", "เมล่อน", "มะเขือเทศ"}
    assert set(lookup.varieties) == {"พริกขี้หนู", "ญี่ปุ่น"}


async def test_load_lookup_empty_sets_short_circuit_with_no_query() -> None:
    """Empty value-sets never touch the database at all — the short-circuit
    lives in master_data_repository.list_by_type_values itself (`if not
    values: return []`), so this calls the REAL repo function with a bare
    `object()` db and relies on it never reaching `db.execute(...)`."""
    lookup = await mdv.load_crop_variety_lookup(object(), set(), set())
    assert lookup.crops == {} and lookup.varieties == {}


# --- assert_crop_variety_valid — API convenience wrapper --------------------

async def test_assert_valid_pair_does_not_raise() -> None:
    async def fake(db, type_, values):
        if type_ == "crop":
            return [_md("crop", "พริก")]
        return [_md("variety", "พริกขี้หนู", parent="พริก")]

    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(side_effect=fake),
    ):
        await mdv.assert_crop_variety_valid(object(), "พริก", "พริกขี้หนู")


async def test_assert_invalid_pair_raises_422_with_thai_message() -> None:
    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(return_value=[]),
    ):
        with pytest.raises(HTTPException) as exc:
            await mdv.assert_crop_variety_valid(object(), "พริก", None)
    assert exc.value.status_code == 422
    assert "ไม่พบ" in exc.value.detail
    # No stack trace / internal id ever in the detail.
    assert "Traceback" not in exc.value.detail


async def test_assert_unchanged_legacy_pair_skips_lookup_entirely() -> None:
    """An update that doesn't touch crop/variety shouldn't even need to
    query Master Data — the pair-equality short-circuit in
    crop_variety_errors makes load_crop_variety_lookup's result irrelevant,
    but the query itself still runs today (no querying-skip optimization at
    this wrapper level); this test locks in that it never raises regardless
    of what the lookup would have found."""
    with patch(
        "app.services.master_data_validation.master_data_repo.list_by_type_values",
        AsyncMock(return_value=[]),  # would fail if actually checked
    ):
        await mdv.assert_crop_variety_valid(
            object(), "พริก", "พริกขี้หนู", current_crop="พริก", current_variety="พริกขี้หนู",
        )
