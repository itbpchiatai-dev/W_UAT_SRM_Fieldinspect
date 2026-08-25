"""Cycle P.Code vs Master Data (round 8-26C).

A cycle's P.Code must now exist in master_data, be active, and belong to the
cycle's effective VARIETY — the same three rules variety already had against
crop, one level down.

The rule this file exists to pin down is the ESCAPE HATCH. Every cycle
created before round 8-26A carries a free-text P.Code that is not in Master
Data, and those cycles must stay editable forever. So the
unchanged-is-allowed check is evaluated PER FIELD: the P.Code has its own,
independent of the crop/variety pair's. Without that, fixing a legacy
cycle's crop would suddenly demand the user also fix a P.Code they never
touched — which is exactly what the user said must NOT happen ("ของเก่าให้
เป็นค่าเดิม แต่ของใหม่ต้องถูกต้อง").
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import master_data_validation as mdv

# This file tests the real validator, so it must opt out of
# tests/unit/conftest.py's permissive default (which patches
# crop_variety_errors to always return []) — same as
# test_master_data_validation.py.
pytestmark = pytest.mark.nodefault_crop_variety


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


def _lookup(*, p_codes=None, varieties=None, crops=None) -> mdv.CropVarietyLookup:
    return mdv.CropVarietyLookup(
        crops={c.value: c for c in (crops or [_md("crop", "พริก")])},
        varieties={
            v.value: v for v in (varieties or [_md("variety", "พริกขี้หนู", parent="พริก")])
        },
        p_codes={
            p.value: p for p in (p_codes or [_md("p_code", "WM-111", parent="พริกขี้หนู")])
        },
    )


def _errors(**kw) -> list[str]:
    kw.setdefault("crop", "พริก")
    kw.setdefault("variety", "พริกขี้หนู")
    lookup = kw.pop("lookup", None) or _lookup()
    crop = kw.pop("crop")
    variety = kw.pop("variety")
    return mdv.crop_variety_errors(lookup, crop, variety, **kw)


# --- the three rules -----------------------------------------------------


def test_a_valid_active_p_code_under_the_right_variety_passes() -> None:
    assert _errors(p_code="WM-111") == []


def test_a_blank_p_code_is_never_an_error() -> None:
    """Only an Auto Lot needs one, and that requirement belongs to
    plot_cycle_repository's AutoLotMissingComponentError, not here."""
    for blank in (None, "", "   "):
        assert _errors(p_code=blank) == []


def test_an_unknown_p_code_is_rejected() -> None:
    errors = _errors(p_code="NOPE")
    assert len(errors) == 1
    assert 'ไม่พบ P.Code "NOPE"' in errors[0]


def test_a_deactivated_p_code_is_rejected() -> None:
    lookup = _lookup(p_codes=[_md("p_code", "WM-111", parent="พริกขี้หนู", active=False)])
    errors = _errors(p_code="WM-111", lookup=lookup)
    assert len(errors) == 1
    assert "ถูกปิดใช้งาน" in errors[0]


def test_a_p_code_belonging_to_a_different_variety_is_rejected() -> None:
    lookup = _lookup(
        p_codes=[_md("p_code", "WM-141", parent="พริกจินดา")],
        varieties=[
            _md("variety", "พริกขี้หนู", parent="พริก"),
            _md("variety", "พริกจินดา", parent="พริก"),
        ],
    )
    errors = _errors(p_code="WM-141", lookup=lookup)
    assert len(errors) == 1
    assert 'ไม่ได้อยู่ภายใต้พันธุ์ "พริกขี้หนู"' in errors[0]


def test_a_p_code_without_a_variety_is_rejected() -> None:
    errors = _errors(crop="พริก", variety=None, p_code="WM-111")
    assert "กรุณาระบุพันธุ์ก่อนกำหนด P.Code" in errors


# --- the escape hatch: legacy cycles stay editable ----------------------


def test_an_unchanged_legacy_p_code_passes_even_though_it_is_not_in_master_data() -> None:
    """The whole point. LEGACY-XYZ exists on the cycle and nowhere else."""
    assert _errors(
        p_code="LEGACY-XYZ", current_p_code="LEGACY-XYZ",
        current_crop="พริก", current_variety="พริกขี้หนู",
    ) == []


def test_changing_only_the_crop_does_not_re_validate_an_untouched_legacy_p_code() -> None:
    """Independent per-field escape hatches. Sharing the pair's hatch would
    make fixing a legacy cycle's crop demand fixing its P.Code too."""
    lookup = _lookup(
        crops=[_md("crop", "พริก"), _md("crop", "เมล่อน")],
        varieties=[_md("variety", "พริกขี้หนู", parent="เมล่อน")],
    )
    errors = mdv.crop_variety_errors(
        lookup, "เมล่อน", "พริกขี้หนู",
        current_crop="พริก", current_variety="พริกขี้หนู",
        p_code="LEGACY-XYZ", current_p_code="LEGACY-XYZ",
    )
    assert errors == []


def test_changing_only_the_p_code_does_not_re_validate_an_untouched_legacy_pair() -> None:
    """The mirror case: a cycle whose crop was deactivated long ago can still
    have its P.Code corrected."""
    lookup = _lookup(crops=[_md("crop", "พริก", active=False)])
    errors = mdv.crop_variety_errors(
        lookup, "พริก", "พริกขี้หนู",
        current_crop="พริก", current_variety="พริกขี้หนู",
        p_code="WM-111", current_p_code="LEGACY-XYZ",
    )
    assert errors == []


def test_changing_a_legacy_p_code_to_another_unknown_one_is_rejected() -> None:
    """"Old values stay" must not become "anything goes" — the moment the
    user edits the field, the new value has to be real."""
    errors = _errors(
        p_code="STILL-NOT-REAL", current_p_code="LEGACY-XYZ",
        current_crop="พริก", current_variety="พริกขี้หนู",
    )
    assert len(errors) == 1
    assert 'ไม่พบ P.Code "STILL-NOT-REAL"' in errors[0]


def test_whitespace_only_change_to_a_p_code_counts_as_unchanged() -> None:
    assert _errors(
        p_code="  LEGACY-XYZ  ", current_p_code="LEGACY-XYZ",
        current_crop="พริก", current_variety="พริกขี้หนู",
    ) == []


def test_clearing_a_legacy_p_code_is_allowed() -> None:
    """Blank is never an error, so removing an unresolvable legacy value is
    always a way out."""
    assert _errors(
        p_code=None, current_p_code="LEGACY-XYZ",
        current_crop="พริก", current_variety="พริกขี้หนู",
    ) == []


# --- crop/variety behaviour is unchanged --------------------------------


def test_the_crop_variety_rules_still_report_independently_of_p_code() -> None:
    """Both halves can fail at once and both messages must survive — the
    P.Code check appends to the same list, it never replaces it."""
    lookup = _lookup(crops=[_md("crop", "พริก", active=False)])
    errors = mdv.crop_variety_errors(
        lookup, "พริก", "พริกขี้หนู", p_code="NOPE",
    )
    assert any("ชนิดพืช" in e and "ถูกปิดใช้งาน" in e for e in errors)
    assert any('ไม่พบ P.Code "NOPE"' in e for e in errors)


def test_a_lookup_built_without_p_codes_still_works_for_crop_variety_callers() -> None:
    """p_codes defaults to an empty dict, so an older caller that only asks
    about crop/variety is unaffected."""
    lookup = mdv.CropVarietyLookup(
        crops={"พริก": _md("crop", "พริก")},
        varieties={"พริกขี้หนู": _md("variety", "พริกขี้หนู", parent="พริก")},
    )
    assert lookup.p_codes == {}
    assert mdv.crop_variety_errors(lookup, "พริก", "พริกขี้หนู") == []
