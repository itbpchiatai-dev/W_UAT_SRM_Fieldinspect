"""The variety is READ OFF the P.Code, never typed (round Y).

What the user asked for, in their words: picking a planting cycle's plant is
picking two things — the crop ("WM") and the P.Code ("CTT-507"). The
variety, which is what "แตงโม RWA 412 (D-12)" names, is a DETAIL OF the
P.Code, so offering it as a third choice invites two answers to one question
and lets them disagree.

Master Data already holds the hierarchy that makes this possible:
crop → variety → p_code, where a P.Code's `parent` is its variety (round
8-26A). Round 8-26C read that chain DOWNWARD — pick a variety, derive its one
active P.Code. This round reads the same chain UPWARD — pick a P.Code, derive
the variety it belongs to — and that direction is the one that survives,
because a variety owns at most one active P.Code but a crop owns many
varieties: upward there is exactly one answer, never a choice.

Nothing about storage changes: PlotCycle.variety still holds the variety
string, every report still reads it, and every cycle created before this
round keeps the value it was given.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import master_data_validation as mdv

# The real validator is what this file is about, so it opts out of
# tests/unit/conftest.py's permissive default — same as
# test_master_data_validation.py and test_p_code_cycle_validation_8_26c.py.
pytestmark = pytest.mark.nodefault_crop_variety


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


_DEFAULT = object()  # an explicit [] must mean "none", not "use the default"


def _lookup(*, crops=_DEFAULT, varieties=_DEFAULT, p_codes=_DEFAULT) -> mdv.CropTaxonomyLookup:
    """The Master Data the screenshot shows: crop WM owns variety
    "แตงโม RWA 412 (D-12)", which owns P.Code CTT-507."""
    if crops is _DEFAULT:
        crops = [_md("crop", "WM")]
    if varieties is _DEFAULT:
        varieties = [_md("variety", "แตงโม RWA 412 (D-12)", parent="WM")]
    if p_codes is _DEFAULT:
        p_codes = [_md("p_code", "CTT-507", parent="แตงโม RWA 412 (D-12)")]
    return mdv.CropTaxonomyLookup(
        crops={c.value: c for c in crops},
        varieties={v.value: v for v in varieties},
        p_codes={p.value: p for p in p_codes},
    )


# --- reading the variety off the P.Code ----------------------------------


def test_the_variety_is_the_p_codes_parent() -> None:
    assert mdv.variety_for_p_code(_lookup(), "CTT-507") == "แตงโม RWA 412 (D-12)"


def test_an_unknown_p_code_derives_no_variety() -> None:
    # No guess, no blank-but-plausible string: the caller reports the P.Code
    # as unknown (below) rather than storing a variety nobody chose.
    assert mdv.variety_for_p_code(_lookup(), "NOPE-1") is None


def test_no_p_code_derives_no_variety() -> None:
    assert mdv.variety_for_p_code(_lookup(), None) is None
    assert mdv.variety_for_p_code(_lookup(), "   ") is None


def test_a_p_code_is_matched_after_trimming() -> None:
    assert mdv.variety_for_p_code(_lookup(), "  CTT-507 ") == "แตงโม RWA 412 (D-12)"


def test_an_inactive_p_code_still_names_its_variety() -> None:
    # Derivation answers "which variety is this?", never "may this be used?".
    # Keeping the two apart is what lets the error below say WHICH variety a
    # deactivated P.Code belonged to.
    lookup = _lookup(p_codes=[_md("p_code", "CTT-507", parent="แตงโม RWA 412 (D-12)", active=False)])
    assert mdv.variety_for_p_code(lookup, "CTT-507") == "แตงโม RWA 412 (D-12)"


# --- what makes a crop + P.Code pair valid for a NEW cycle ---------------


def _errors(crop="WM", p_code="CTT-507", lookup=None) -> list[str]:
    return mdv.crop_p_code_errors(lookup or _lookup(), crop, p_code)


def test_an_active_p_code_under_the_chosen_crop_passes() -> None:
    assert _errors() == []


def test_an_unknown_p_code_is_refused_by_name() -> None:
    assert _errors(p_code="CTT-999") == ['ไม่พบ P.Code "CTT-999" ใน Master Data']


def test_a_deactivated_p_code_is_refused() -> None:
    lookup = _lookup(p_codes=[_md("p_code", "CTT-507", parent="แตงโม RWA 412 (D-12)", active=False)])
    assert _errors(lookup=lookup) == [
        'P.Code "CTT-507" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน'
    ]


def test_a_p_code_belonging_to_another_crop_is_refused() -> None:
    # The one check that needs BOTH levels: CTT-507's variety sits under
    # "พริก", so it cannot be used on a WM cycle even though every row is
    # active and exists.
    lookup = _lookup(
        crops=[_md("crop", "WM"), _md("crop", "พริก")],
        varieties=[_md("variety", "พริกขี้หนู", parent="พริก")],
        p_codes=[_md("p_code", "CTT-507", parent="พริกขี้หนู")],
    )
    assert _errors(lookup=lookup) == ['P.Code "CTT-507" ไม่ได้อยู่ภายใต้ชนิดพืช "WM"']


def test_a_p_code_whose_variety_is_deactivated_is_refused() -> None:
    lookup = _lookup(
        varieties=[_md("variety", "แตงโม RWA 412 (D-12)", parent="WM", active=False)]
    )
    assert _errors(lookup=lookup) == [
        'พันธุ์ "แตงโม RWA 412 (D-12)" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน'
    ]


def test_a_p_code_whose_variety_is_missing_is_refused() -> None:
    lookup = _lookup(varieties=[])
    assert _errors(lookup=lookup) == ['ไม่พบพันธุ์ "แตงโม RWA 412 (D-12)" ใน Master Data']


def test_an_unknown_crop_is_refused() -> None:
    assert 'ไม่พบชนิดพืช "XX" ใน Master Data' in _errors(crop="XX")


def test_a_deactivated_crop_is_refused() -> None:
    lookup = _lookup(crops=[_md("crop", "WM", active=False)])
    assert 'ชนิดพืช "WM" ถูกปิดใช้งาน กรุณาเปิดใช้งานใน Master Data ก่อน' in _errors(lookup=lookup)


def test_a_p_code_without_a_crop_is_refused() -> None:
    assert _errors(crop=None) == ["กรุณาระบุชนิดพืชก่อนเลือก P.Code"]


def test_a_blank_p_code_is_not_this_validators_problem() -> None:
    # Requiredness lives where it always has: the API schema (PlotCycleCreate)
    # and the Excel import's own "ต้องระบุ pCode" check. Repeating it here
    # would mean two places to change when it moves.
    assert _errors(p_code=None) == []
    assert _errors(p_code="  ") == []


# --- the lookup has to carry the varieties the P.Codes name ---------------


@pytest.mark.asyncio
async def test_the_lookup_loads_the_varieties_its_p_codes_point_at() -> None:
    """The batch fetch is given crops and P.Codes — the varieties are not in
    the file at all any more, so it must go and get the ones the P.Codes
    name, or every row would fail with "ไม่พบพันธุ์"."""
    asked: dict[str, set[str]] = {}

    async def fake_list(db, type_, values):
        asked[type_] = set(values)
        rows = {
            "crop": [_md("crop", "WM")],
            "variety": [_md("variety", "แตงโม RWA 412 (D-12)", parent="WM")],
            "p_code": [_md("p_code", "CTT-507", parent="แตงโม RWA 412 (D-12)")],
        }[type_]
        return [r for r in rows if r.value in values]

    from unittest.mock import patch

    with patch(
        "app.repositories.master_data_repository.list_by_type_values", side_effect=fake_list
    ):
        lookup = await mdv.load_crop_p_code_lookup(None, {"WM"}, {"CTT-507"})

    assert asked["crop"] == {"WM"}
    assert asked["p_code"] == {"CTT-507"}
    # The second variety query is what makes derivation possible.
    assert asked["variety"] == {"แตงโม RWA 412 (D-12)"}
    assert mdv.crop_p_code_errors(lookup, "WM", "CTT-507") == []


def test_a_variety_too_long_to_store_is_refused_not_truncated() -> None:
    """Master Data allows a 255-character value; PlotCycle.variety holds 100.
    Before this round the length was checked where the user typed it; now
    nobody types it, so the check belongs to the P.Code that pulls it in —
    otherwise a legitimate-looking row would die at INSERT with a DataError."""
    long_name = "ก" * 101
    lookup = _lookup(
        varieties=[_md("variety", long_name, parent="WM")],
        p_codes=[_md("p_code", "CTT-507", parent=long_name)],
    )
    errors = _errors(lookup=lookup)
    assert len(errors) == 1
    assert "100" in errors[0]
    assert "CTT-507" in errors[0]


def test_a_variety_of_exactly_the_limit_is_fine() -> None:
    name = "ก" * 100
    lookup = _lookup(
        varieties=[_md("variety", name, parent="WM")],
        p_codes=[_md("p_code", "CTT-507", parent=name)],
    )
    assert _errors(lookup=lookup) == []
