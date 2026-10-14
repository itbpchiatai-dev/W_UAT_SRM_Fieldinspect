"""Round 39 — the province cell is normalised, then checked against Master Data.

Until this round plot_import only LENGTH-checked the column, so the Excel path
accepted anything while the web form was restricted to a 77-value dropdown. On
UAT that produced one province stored two ways: 217 rows of "จ.ขอนแก่น" beside
64 of "ขอนแก่น". It split the Dashboard province filter into two entries, each
hiding most of the province, and forced round 38 to normalise on READ just to
draw a map.

The contract these tests pin down is "normalise FIRST, then validate": a row
that writes the province the way people actually write it is accepted and
stored canonically, and only a value that survives normalisation without
matching a real province is refused.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import master_data_validation as mdv

_M = "app.services.master_data_validation"

# These tests ARE the validation, so they opt out of conftest.py's permissive
# default — which exists so the dozens of plot_import tests that pass arbitrary
# province strings against a sentinel `db` keep working.
pytestmark = pytest.mark.nodefault_province


def _row(value: str, *, active: bool = True):
    return SimpleNamespace(value=value, active=active, parent=None)


# --- normalisation -----------------------------------------------------------


class TestNormalizeProvinceName:
    def test_strips_the_prefix_the_excel_import_let_through(self) -> None:
        # The exact pair that exists on UAT today.
        assert mdv.normalize_province_name("จ.ขอนแก่น") == "ขอนแก่น"
        assert mdv.normalize_province_name("ขอนแก่น") == "ขอนแก่น"

    def test_strips_the_spelled_out_prefix(self) -> None:
        assert mdv.normalize_province_name("จังหวัดเลย") == "เลย"

    def test_trims_and_collapses_whitespace(self) -> None:
        assert mdv.normalize_province_name("  จ.ขอนแก่น  ") == "ขอนแก่น"

    def test_resolves_the_bangkok_short_forms(self) -> None:
        for written in ("กรุงเทพ", "กรุงเทพฯ", "กทม", "กทม."):
            assert mdv.normalize_province_name(written) == "กรุงเทพมหานคร"

    def test_blank_and_none_become_none(self) -> None:
        assert mdv.normalize_province_name(None) is None
        assert mdv.normalize_province_name("") is None
        assert mdv.normalize_province_name("   ") is None

    def test_never_corrects_a_misspelling(self) -> None:
        # Correcting this would file the row under a province nobody typed.
        # Leaving it alone is what lets province_errors refuse it by name.
        assert mdv.normalize_province_name("ขอนแกน") == "ขอนแกน"


# --- validation --------------------------------------------------------------


class TestProvinceErrors:
    def test_blank_is_allowed_because_the_column_is_optional(self) -> None:
        assert mdv.province_errors({}, None) == []

    def test_known_active_province_passes(self) -> None:
        assert mdv.province_errors({"ขอนแก่น": _row("ขอนแก่น")}, "ขอนแก่น") == []

    def test_unknown_province_is_refused_and_named(self) -> None:
        errors = mdv.province_errors({}, "ขอนแกน")
        assert len(errors) == 1
        # The value has to appear, or the row cannot be found in a file of
        # thousands.
        assert "ขอนแกน" in errors[0]

    def test_inactive_province_is_refused_with_a_different_reason(self) -> None:
        lookup = {"ขอนแก่น": _row("ขอนแก่น", active=False)}
        errors = mdv.province_errors(lookup, "ขอนแก่น")
        assert len(errors) == 1
        assert "ปิดใช้งาน" in errors[0]


class TestLoadProvinceLookup:
    @pytest.mark.asyncio
    async def test_is_one_query_keyed_by_value(self) -> None:
        rows = [_row("ขอนแก่น"), _row("เลย")]
        with patch(
            f"{_M}.master_data_repo.list_by_type_values",
            AsyncMock(return_value=rows),
        ) as m:
            lookup = await mdv.load_province_lookup(object(), {"ขอนแก่น", "เลย"})

        assert m.await_count == 1
        assert m.await_args.args[1] == mdv.PROVINCE_TYPE
        assert set(lookup) == {"ขอนแก่น", "เลย"}


# --- the two halves together -------------------------------------------------


class TestNormaliseThenValidate:
    """What the importer actually does, in order."""

    def test_the_written_form_is_accepted_and_the_canonical_one_is_what_matches(self) -> None:
        lookup = {"ขอนแก่น": _row("ขอนแก่น")}
        normalized = mdv.normalize_province_name("จ.ขอนแก่น")

        assert normalized == "ขอนแก่น"
        assert mdv.province_errors(lookup, normalized) == []

    def test_without_normalising_first_the_same_row_would_be_refused(self) -> None:
        # Guards the ORDER, not just the parts: validating the raw cell against
        # Master Data is exactly the mistake that would reject every file
        # anyone has ever uploaded.
        lookup = {"ขอนแก่น": _row("ขอนแก่น")}
        assert mdv.province_errors(lookup, "จ.ขอนแก่น") != []
