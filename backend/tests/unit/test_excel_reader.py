"""Minimal XLSX reader (round 7.5) — round-trips our own inline-string
workbooks AND parses the shared-string + numeric shapes real Excel emits.
DB-free, no spreadsheet dependency.
"""
from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.services.excel_reader import ExcelParseError, read_first_sheet
from app.services.excel_workbook import build_xlsx


def test_reads_inline_string_workbook_our_own_template_shape() -> None:
    content = build_xlsx([("plots", [
        ["action", "plotCode", "plantCount"],
        ["create_plot_with_cycle", "P001", 1000],
    ])])
    headers, rows = read_first_sheet(content)
    assert headers == ["action", "plotCode", "plantCount"]
    assert len(rows) == 1
    row_no, values = rows[0]
    assert row_no == 2
    assert values["action"] == "create_plot_with_cycle"
    assert values["plotCode"] == "P001"
    # Numbers come back as their raw literal for the import service to parse.
    assert values["plantCount"] == "1000"


def test_blank_rows_are_skipped() -> None:
    content = build_xlsx([("plots", [
        ["action", "plotCode"],
        [None, None],
        ["start_new_cycle", "P002"],
    ])])
    _headers, rows = read_first_sheet(content)
    assert [v["plotCode"] for _n, v in rows] == ["P002"]


def _shared_string_xlsx(header: list[str], row: list[str]) -> bytes:
    """Hand-build the shared-string shape Excel produces (t="s" cells indexing
    xl/sharedStrings.xml) so the reader's shared-string path is exercised."""
    strings = header + row
    si = "".join(f"<si><t>{s}</t></si>" for s in strings)
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{si}</sst>'
    )

    def cells(values: list[str], base: int, r: int) -> str:
        out = []
        for i, _v in enumerate(values):
            col = chr(65 + i)
            idx = base + i
            out.append(f'<c r="{col}{r}" t="s"><v>{idx}</v></c>')
        return f'<row r="{r}">{"".join(out)}</row>'

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + cells(header, 0, 1)
        + cells(row, len(header), 2)
        + "</sheetData></worksheet>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_reads_shared_strings_what_excel_emits_after_a_resave() -> None:
    content = _shared_string_xlsx(
        ["action", "supplierCode", "plotCode"],
        ["update_current_cycle", "SUP001", "P003"],
    )
    headers, rows = read_first_sheet(content)
    assert headers == ["action", "supplierCode", "plotCode"]
    _n, values = rows[0]
    assert values["action"] == "update_current_cycle"
    assert values["supplierCode"] == "SUP001"
    assert values["plotCode"] == "P003"


def test_non_xlsx_bytes_raise_excel_parse_error() -> None:
    with pytest.raises(ExcelParseError):
        read_first_sheet(b"this is not a zip file")


def test_empty_sheet_returns_no_rows() -> None:
    content = build_xlsx([("plots", [["action", "plotCode"]])])
    headers, rows = read_first_sheet(content)
    assert headers == ["action", "plotCode"]
    assert rows == []


def test_reader_does_not_skip_the_template_description_row() -> None:
    # The generic reader returns EVERY non-blank data row, including a row
    # whose action is the template's guidance marker. Skipping row 2 is
    # plot_import's job (round 8-2.1), not the reader's — this pins that split.
    from app.services.plot_import import TEMPLATE_DESCRIPTION_ACTION

    content = build_xlsx([("plots", [
        ["action", "plotCode"],
        [TEMPLATE_DESCRIPTION_ACTION, "คำอธิบาย"],
        ["create_plot_with_cycle", "P001"],
    ])])
    _headers, rows = read_first_sheet(content)
    assert [n for n, _v in rows] == [2, 3]
    assert rows[0][1]["action"] == TEMPLATE_DESCRIPTION_ACTION
