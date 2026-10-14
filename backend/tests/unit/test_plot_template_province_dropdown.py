"""Round 39 — the province column of the import template is a dropdown.

The web form has written a master_data province through a dropdown since
migration 0028; the Excel path never had one, which is how UAT came to hold
"จ.ขอนแก่น" and "ขอนแก่น" as two spellings of one province. The importer now
refuses an unknown province, so offering the list in the file is what keeps
that refusal from being the first a user hears of it.

77 Thai names are far past the ~255-character cap on an inline list formula,
so this goes through a hidden sheet plus a defined name — the same route
master_data_crop_variety_import already takes for crops.
"""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.api.v1 import plots as plots_api
from app.services.plot_import import IMPORT_COLUMNS

SOME_PROVINCES = ["กระบี่", "ขอนแก่น", "เชียงใหม่"]


def _sheet1(**_: object) -> list[list]:
    return [[c for c in IMPORT_COLUMNS]]


def _parts(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()
                if name.endswith((".xml", ".rels"))}


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class TestProvinceColumnLetter:
    def test_the_hardcoded_sqref_column_still_matches_IMPORT_COLUMNS(self) -> None:
        """A data-validation sqref is POSITIONAL.

        The reader maps cells by header NAME, so reordering a column is
        normally harmless — but it would silently point this dropdown at the
        wrong column, and a dropdown on the wrong column is worse than none.
        """
        expected = _column_letter(IMPORT_COLUMNS.index("province"))
        assert plots_api._PROVINCE_COLUMN_LETTER == expected


class TestDropdownIsPresent:
    def test_a_hidden_reference_sheet_carries_the_list(self) -> None:
        content = plots_api._template_workbook(_sheet1(), "SUP001", SOME_PROVINCES)
        parts = _parts(content)
        workbook = parts["xl/workbook.xml"]

        # The sheet exists, is hidden, and the named range points at it.
        assert plots_api._SHEET_PROVINCE_REF in workbook
        assert "hidden" in workbook
        assert plots_api._PROVINCE_DEFINED_NAME in workbook

    def test_the_validation_sits_on_the_province_column_of_the_import_sheet(self) -> None:
        content = plots_api._template_workbook(_sheet1(), "SUP001", SOME_PROVINCES)
        sheet1 = _parts(content)["xl/worksheets/sheet1.xml"]
        col = plots_api._PROVINCE_COLUMN_LETTER

        assert "dataValidation" in sheet1
        assert f'sqref="{col}3:{col}5000"' in sheet1
        assert plots_api._PROVINCE_DEFINED_NAME in sheet1

    def test_every_province_reaches_the_reference_sheet(self) -> None:
        content = plots_api._template_workbook(_sheet1(), "SUP001", SOME_PROVINCES)
        joined = "".join(_parts(content).values())
        for name in SOME_PROVINCES:
            assert name in joined


class TestWithoutProvinces:
    def test_an_empty_list_produces_no_dropdown_at_all(self) -> None:
        """An empty named range is invalid OOXML.

        A brand-new install with no provinces seeded must still get a template
        that opens — without the convenience, not without the file.
        """
        content = plots_api._template_workbook(_sheet1(), "SUP001", [])
        parts = _parts(content)

        assert plots_api._PROVINCE_DEFINED_NAME not in "".join(parts.values())
        assert "dataValidation" not in parts["xl/worksheets/sheet1.xml"]

    def test_the_import_sheet_is_still_first(self) -> None:
        """excel_reader reads only the FIRST worksheet, so the reference sheet
        must never displace it."""
        for provinces in ([], SOME_PROVINCES):
            content = plots_api._template_workbook(_sheet1(), "SUP001", provinces)
            sheet1 = _parts(content)["xl/worksheets/sheet1.xml"]
            assert IMPORT_COLUMNS[0] in sheet1


# This class IS the lookup, so it opts out of conftest.py's empty default —
# which exists so template tests that are not about the dropdown keep running
# against their MagicMock db.
@pytest.mark.nodefault_template_provinces
class TestProvincesComeFromMasterData:
    @pytest.mark.asyncio
    async def test_only_active_provinces_are_offered(self) -> None:
        """An admin who retires a province must not keep seeing it offered —
        the importer would refuse it on upload."""
        from unittest.mock import AsyncMock, patch

        rows = [type("R", (), {"value": v})() for v in SOME_PROVINCES]
        with patch(
            "app.api.v1.plots.master_data_repository.list_items",
            AsyncMock(return_value=rows),
        ) as m:
            result = await plots_api._template_provinces(object())

        assert result == SOME_PROVINCES
        assert m.await_args.kwargs["active_only"] is True
        assert m.await_args.kwargs["type"] == "province"
