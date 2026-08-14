"""Round 8-6A.1 — the hand-rolled XLSX writer's blank/styled-blank cell
semantics (app.services.excel_workbook). Round 8-6A's styled-cell support
had a bug: _sheet_xml's `if value is None: continue` skipped the cell
entirely even when a StyledCell carried a style, so a "please fill in"
yellow cell with nothing typed yet (or a red example-row cell that happens
to be blank) rendered with NO fill at all in Excel. Fixed by writing an
empty, valueless <c r="..." s="N"/> for exactly that case; every other shape
(plain None, StyledCell(None, None), a real value) is unchanged.
"""
from __future__ import annotations

import re
from io import BytesIO
from zipfile import ZipFile

from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import CellStyle, StyledCell, _sheet_xml, build_xlsx

_YELLOW = CellStyle(bg="FFFFF9C4")


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _cellxfs_fill_colors(styles_xml: str) -> dict[int, str | None]:
    fill_blocks = re.findall(r"<fill>.*?</fill>", styles_xml)
    fill_colors: list[str | None] = []
    for block in fill_blocks:
        m = re.search(r'fgColor rgb="([0-9A-F]+)"', block)
        fill_colors.append(m.group(1) if m else None)
    cellxfs_section = styles_xml.split("<cellXfs", 1)[1]
    xf_blocks = re.findall(r"<xf [^>]*/>", cellxfs_section)
    result: dict[int, str | None] = {}
    for idx, xf in enumerate(xf_blocks):
        m = re.search(r'fillId="(\d+)"', xf)
        fill_id = int(m.group(1)) if m else 0
        result[idx] = fill_colors[fill_id] if fill_id < len(fill_colors) else None
    return result


def _cell_xml(sheet_xml: str, ref: str) -> str | None:
    m = re.search(rf'<c r="{ref}"[^>]*(?:/>|>.*?</c>)', sheet_xml)
    return m.group(0) if m else None


# --- item 1: plain None (no style) is still omitted -------------------------

def test_plain_none_cell_omitted_from_worksheet_xml() -> None:
    xml = _sheet_xml([["a", None, "b"]])
    assert _cell_xml(xml, "B1") is None
    assert _cell_xml(xml, "A1") is not None
    assert _cell_xml(xml, "C1") is not None


def test_plain_value_only_row_unaffected_by_fix() -> None:
    """A build_xlsx caller that never uses StyledCell (every pre-8-6A and
    every existing result/validation-workbook caller) gets byte-identical
    output to before this fix."""
    xml = build_xlsx([("s", [["action", "plotCode"], ["create_plot_with_cycle", None]])])
    parts = _unzip(xml)
    assert "xl/styles.xml" not in parts
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert _cell_xml(sheet, "B2") is None  # blank cell fully omitted, as before


# --- item 2: StyledCell(None, style) -> empty cell WITH the style ----------

def test_styled_none_cell_is_written_empty_with_style_attribute() -> None:
    xml = _sheet_xml([[StyledCell(None, _YELLOW)]], {_YELLOW: 1})
    cell = _cell_xml(xml, "A1")
    assert cell == '<c r="A1" s="1"/>'
    assert "<v>" not in cell
    assert "<is>" not in cell


# --- item 3: StyledCell(None, None) is still omitted ------------------------

def test_styled_cell_none_value_none_style_is_omitted() -> None:
    xml = _sheet_xml([[StyledCell(None, None), "kept"]])
    assert _cell_xml(xml, "A1") is None
    assert _cell_xml(xml, "B1") is not None


def test_blank_row_with_no_styled_cells_stays_a_bare_empty_row() -> None:
    xml = _sheet_xml([[None, StyledCell(None, None)]])
    assert '<row r="1"></row>' in xml


# --- item 4: read_first_sheet still treats the empty styled cell as blank --

def test_read_first_sheet_returns_no_field_for_empty_styled_cell() -> None:
    content = build_xlsx([
        ("s", [
            ["action", "lotNo"],
            [StyledCell("start_next_cycle", _YELLOW), StyledCell(None, _YELLOW)],
        ])
    ])
    headers, rows = read_first_sheet(content)
    assert headers == ["action", "lotNo"]
    _row_no, row = rows[0]
    assert row == {"action": "start_next_cycle"}
    assert "lotNo" not in row


# --- non-None values are completely unaffected ------------------------------

def test_styled_cell_with_real_string_value_unchanged() -> None:
    xml = _sheet_xml([[StyledCell("hello", _YELLOW)]], {_YELLOW: 1})
    assert _cell_xml(xml, "A1") == '<c r="A1" s="1" t="inlineStr"><is><t>hello</t></is></c>'


def test_styled_cell_empty_string_value_is_a_blank_inline_string_not_omitted() -> None:
    """StyledCell("", yellow) — an explicit empty string, distinct from None —
    keeps its pre-existing inline-string-cell shape; read_first_sheet already
    treats an empty inline string as blank (text == "" is never added to the
    row dict), so no reader change was needed for this shape."""
    xml = _sheet_xml([[StyledCell("", _YELLOW)]], {_YELLOW: 1})
    cell = _cell_xml(xml, "A1")
    assert cell == '<c r="A1" s="1" t="inlineStr"><is><t></t></is></c>'
    content = build_xlsx([("s", [["h"], [StyledCell("", _YELLOW)]])])
    _headers, rows = read_first_sheet(content)
    assert rows == []  # fully-blank data row


def test_numeric_styled_cell_unchanged() -> None:
    xml = _sheet_xml([[StyledCell(42, _YELLOW)]], {_YELLOW: 1})
    assert _cell_xml(xml, "A1") == '<c r="A1" s="1"><v>42</v></c>'


# --- style-index lookup is correct end-to-end for a styled blank -----------

def test_empty_styled_cell_style_index_resolves_to_the_right_fill_in_styles_xml() -> None:
    content = build_xlsx([("s", [[StyledCell(None, _YELLOW)]])])
    parts = _unzip(content)
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet = parts["xl/worksheets/sheet1.xml"]
    m = re.search(r'<c r="A1" s="(\d+)"/>', sheet)
    assert m is not None
    assert fill_by_style[int(m.group(1))] == _YELLOW.bg
