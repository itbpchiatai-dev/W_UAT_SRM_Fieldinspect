"""Minimal XLSX reader for the plot import (round 7.5).

Avoids adding a spreadsheet-parser dependency (openpyxl/pandas) — same
rationale as excel_workbook.py's hand-rolled writer. Reads the FIRST worksheet
only: the header row (row 1) names the columns; each subsequent non-blank row
becomes a dict keyed by header. Handles shared strings (what Excel emits when
you fill in and re-save the template), inline strings (our own generated
template), plain numeric cells, and formula string results. Every value comes
back as a trimmed string — the import service does its own typed parsing
(int/decimal/date, incl. Excel serial dates), so this stays format-agnostic.
"""
from __future__ import annotations

from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class ExcelParseError(ValueError):
    """The uploaded file isn't a readable single-sheet .xlsx workbook."""


def _col_index(cell_ref: str) -> int:
    """'B7' -> 1 (0-based column index); ignores the row digits."""
    idx = 0
    for ch in cell_ref:
        if ch.isalpha():
            idx = idx * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return idx - 1


def _shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    # <si> is either a single <t> or several <r><t> runs — join all text nodes.
    return ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in root.findall(f"{_NS}si")]


def _first_sheet_bytes(zf: ZipFile) -> bytes:
    names = zf.namelist()
    if "xl/worksheets/sheet1.xml" in names:
        return zf.read("xl/worksheets/sheet1.xml")
    sheets = sorted(
        n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")
    )
    if not sheets:
        raise ExcelParseError("workbook has no worksheet")
    return zf.read(sheets[0])


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    t = cell.get("t")
    if t == "inlineStr":
        is_el = cell.find(f"{_NS}is")
        if is_el is None:
            return ""
        return "".join(x.text or "" for x in is_el.iter(f"{_NS}t")).strip()
    v = cell.find(f"{_NS}v")
    if v is None or v.text is None:
        return ""
    if t == "s":  # shared-string index
        try:
            return shared[int(v.text)].strip()
        except (ValueError, IndexError):
            return ""
    # numbers, booleans, formula-string results — return the raw literal.
    return v.text.strip()


def read_first_sheet(content: bytes) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """Return (headers, rows).

    headers = trimmed row-1 cell texts in column order.
    rows     = (excel_row_number, {header: trimmed value}) for each non-blank
               data row; blank cells and cells with no matching header are
               omitted. Raises ExcelParseError for anything that isn't a
               readable .xlsx.
    """
    try:
        zf = ZipFile(BytesIO(content))
    except BadZipFile as exc:
        raise ExcelParseError("not a valid .xlsx file") from exc
    with zf:
        shared = _shared_strings(zf)
        sheet_bytes = _first_sheet_bytes(zf)
    try:
        root = ET.fromstring(sheet_bytes)
    except ET.ParseError as exc:
        raise ExcelParseError("worksheet XML is corrupt") from exc

    data = root.find(f"{_NS}sheetData")
    if data is None:
        return [], []

    header_by_col: dict[int, str] = {}
    header_seen = False
    rows: list[tuple[int, dict[str, str]]] = []
    for row in data.findall(f"{_NS}row"):
        try:
            row_no = int(row.get("r") or 0)
        except ValueError:
            row_no = 0
        cells: dict[int, str] = {}
        for i, c in enumerate(row.findall(f"{_NS}c")):
            ref = c.get("r") or ""
            col = _col_index(ref) if ref else i
            text = _cell_text(c, shared)
            if text != "":
                cells[col] = text
        if not header_seen:
            header_by_col = dict(cells)
            header_seen = True
            continue
        if not cells:
            continue  # fully-blank row
        row_dict = {
            header_by_col[col]: val
            for col, val in cells.items()
            if col in header_by_col
        }
        if row_dict:
            rows.append((row_no, row_dict))

    headers = [header_by_col[col] for col in sorted(header_by_col)]
    return headers, rows
