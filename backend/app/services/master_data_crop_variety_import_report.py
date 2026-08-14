"""Master Data crop/variety import result workbook (round 8-15A) — hand-
rolled styled XLSX, following the SAME pattern as services/
plot_import_report.py (two sheets: data + summary, per-status cell fills, a
frozen header, an auto-filter) — a fresh, self-contained module rather than
importing that file's constants, since the two importers' column sets are
unrelated (Plot Import's 22+ columns vs this feature's 3).

Adds `importStatus` and `errorMessage` (plus `rowNumber`, required so every
error is traceable to an exact Excel row) as the result columns appended
after the 3 input columns (crop/variety/varietyStatus).

Status vocabulary:
  Preview workbook   — READY / SKIPPED / ERROR
  Completed workbook — COMPLETED / SKIPPED (a successful commit never leaves
                       an ERROR row; every previously-READY row becomes
                       COMPLETED)

Round 8-15A.1 — the summary sheet's crop/variety created/activated/
deactivated counts are passed in EXPLICITLY by the caller (sourced from the
authoritative CropVarietyImportSummary/CropVarietyImportCommitResult), never
re-derived by tallying `row_views[i]["action"]` per row: a crop shared by
several NEW-variety rows would otherwise be counted once per row instead of
once per distinct crop. Per-ROW status counts (READY/SKIPPED/ERROR/
COMPLETED) have no such issue — those legitimately are 1:1 with rows — and
are still tallied from row_views here.

No new dependency — extends the project's hand-rolled OOXML approach. All
user-provided text (crop/variety names) is written as an inline string AND
neutralized against spreadsheet formula injection. Never writes any UUID,
internal `type` string, stack trace, or other internal id into the workbook.
"""
from __future__ import annotations

import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.master_data_crop_variety_import import (
    IMPORT_COLUMNS,
    ROW_STATUS_READY,
    ROW_STATUS_SKIPPED,
)

STATUS_READY = "READY"
STATUS_SKIPPED = "SKIPPED"
STATUS_ERROR = "ERROR"
STATUS_COMPLETED = "COMPLETED"

PHASE_PREVIEW = "PREVIEW"
PHASE_COMMIT = "COMMIT"

SHEET_RESULT = "ผลการนำเข้า"
SHEET_SUMMARY = "สรุป"

RESULT_COLUMNS: list[str] = ["rowNumber", "importStatus", "errorMessage"]
ALL_COLUMNS: list[str] = [*IMPORT_COLUMNS, *RESULT_COLUMNS]


def result_filename(phase: str, processed_at: datetime.datetime) -> str:
    """Server-generated download name (never derived from the uploaded file's
    own name)."""
    stem = "validation" if phase == PHASE_PREVIEW else "result"
    return f"crop-variety-import-{stem}-{processed_at:%Y%m%d-%H%M%S}.xlsx"


def map_row_status(view: dict[str, Any], *, completed: bool) -> str:
    """Row status from STRUCTURED state — never by parsing the Thai
    error_message. A READY row becomes COMPLETED once the commit that
    produced this view actually succeeded; SKIPPED/ERROR are unaffected by
    `completed` (a commit workbook never contains an ERROR row at all, since
    commit refuses to execute while any row still has one)."""
    status = view.get("row_status")
    if status == ROW_STATUS_READY:
        return STATUS_COMPLETED if completed else STATUS_READY
    if status == ROW_STATUS_SKIPPED:
        return STATUS_SKIPPED
    return STATUS_ERROR


# --- formula-injection neutralization (same mitigation as plot_import_report) --
_FORMULA_LEAD = ("=", "+", "-", "@")


def _formula_safe(text: str) -> str:
    if text and text[0] in _FORMULA_LEAD:
        return "'" + text
    return text


def _col_ref(index0: int) -> str:
    name = ""
    n = index0 + 1
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(col0: int, row: int, value: Any, style: int) -> str:
    ref = f"{_col_ref(col0)}{row}"
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(_formula_safe(str(value)))
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


_S_DEFAULT = 0
_S_HEADER = 1
_S_STATUS = {
    STATUS_READY: 2,
    STATUS_SKIPPED: 3,
    STATUS_ERROR: 4,
    STATUS_COMPLETED: 5,
}

_COL_WIDTHS = [22, 22, 16, 12, 14, 60]

_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="7">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE8E8E8"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD7F0D7"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF7D0D0"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF9BD79B"/></patternFill></fill>'
    "</fills>"
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="6" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)

# Labels for the 4 caller-supplied action counts (round 8-15A.1 — the counts
# themselves come from CropVarietyImportSummary/CropVarietyImportCommitResult,
# never tallied here; see the module docstring for why).
_LABEL_CROPS_TO_CREATE = "สร้างชนิดพืชใหม่ (จำนวนชนิดพืช ไม่ซ้ำ)"
_LABEL_VARIETIES_TO_CREATE = "สร้างพันธุ์ใหม่"
_LABEL_VARIETIES_TO_ACTIVATE = "เปิดใช้งานพันธุ์"
_LABEL_VARIETIES_TO_DEACTIVATE = "ปิดใช้งานพันธุ์"


def _summarize(
    row_views: list[dict[str, Any]], *, completed: bool,
    crops_to_create: int, varieties_to_create: int,
    varieties_to_activate: int, varieties_to_deactivate: int,
) -> dict[str, Any]:
    statuses = [map_row_status(v, completed=completed) for v in row_views]
    counts = {s: statuses.count(s) for s in (STATUS_READY, STATUS_SKIPPED, STATUS_ERROR, STATUS_COMPLETED)}
    return {
        "statuses": statuses,
        "counts": counts,
        "total": len(row_views),
        "crops_to_create": crops_to_create,
        "varieties_to_create": varieties_to_create,
        "varieties_to_activate": varieties_to_activate,
        "varieties_to_deactivate": varieties_to_deactivate,
    }


def _result_sheet_xml(row_views: list[dict[str, Any]], statuses: list[str]) -> str:
    rows_xml: list[str] = []
    header_cells = "".join(_cell(i, 1, col, _S_HEADER) for i, col in enumerate(ALL_COLUMNS))
    rows_xml.append(f'<row r="1">{header_cells}</row>')

    for offset, (view, status) in enumerate(zip(row_views, statuses)):
        excel_row = 2 + offset
        raw = view.get("raw") or {}
        style = _S_STATUS[status]
        cells = [_cell(i, excel_row, raw.get(col), style) for i, col in enumerate(IMPORT_COLUMNS)]
        result_values = [view.get("row_number"), status, view.get("error_message") or ""]
        for j, val in enumerate(result_values):
            cells.append(_cell(len(IMPORT_COLUMNS) + j, excel_row, val, style))
        rows_xml.append(f'<row r="{excel_row}">{"".join(cells)}</row>')

    last_row = 1 + len(row_views)
    last_col = _col_ref(len(ALL_COLUMNS) - 1)
    cols_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(_COL_WIDTHS)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<cols>{cols_xml}</cols>"
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        f'<autoFilter ref="A1:{last_col}{last_row}"/>'
        "</worksheet>"
    )


def _summary_sheet_xml(
    summary: dict[str, Any], *, phase: str, original_filename: str | None, processed_at: datetime.datetime,
) -> str:
    c = summary["counts"]
    pairs: list[tuple[str, Any]] = [
        ("ชื่อไฟล์ต้นฉบับ", original_filename or "-"),
        ("วันเวลาที่ประมวลผล", f"{processed_at:%Y-%m-%d %H:%M:%S}"),
        ("ขั้นตอน (Phase)", phase),
        ("จำนวนแถวทั้งหมด", summary["total"]),
        ("READY", c[STATUS_READY]),
        ("SKIPPED", c[STATUS_SKIPPED]),
        ("ERROR", c[STATUS_ERROR]),
        ("COMPLETED", c[STATUS_COMPLETED]),
        (_LABEL_CROPS_TO_CREATE, summary["crops_to_create"]),
        (_LABEL_VARIETIES_TO_CREATE, summary["varieties_to_create"]),
        (_LABEL_VARIETIES_TO_ACTIVATE, summary["varieties_to_activate"]),
        (_LABEL_VARIETIES_TO_DEACTIVATE, summary["varieties_to_deactivate"]),
    ]
    rows_xml = []
    for r, (label, value) in enumerate(pairs, start=1):
        cells = _cell(0, r, label, _S_HEADER) + _cell(1, r, value, _S_DEFAULT)
        rows_xml.append(f'<row r="{r}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="40" customWidth="1"/>'
        '<col min="2" max="2" width="30" customWidth="1"/></cols>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        "</worksheet>"
    )


def _package(sheet1: str, sheet2: str) -> bytes:
    names = [SHEET_RESULT, SHEET_SUMMARY]
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, name in enumerate(names, start=1)
            )
            + "</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/styles.xml", _STYLES_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buffer.getvalue()


def build_crop_variety_import_result_workbook(
    row_views: list[dict[str, Any]],
    *,
    phase: str,
    completed: bool = False,
    original_filename: str | None = None,
    processed_at: datetime.datetime | None = None,
    crops_to_create: int = 0,
    varieties_to_create: int = 0,
    varieties_to_activate: int = 0,
    varieties_to_deactivate: int = 0,
) -> bytes:
    """Pure builder → XLSX bytes (no temp file, no DB). `row_views` are the
    neutral dicts from master_data_crop_variety_import.row_view. `phase` is
    PREVIEW or COMMIT; `completed` is True only for a successfully-committed
    file. Deterministic apart from `processed_at`.

    Round 8-15A.1 — the 4 `*_to_*`/`*_to_create` counts are the caller's
    AUTHORITATIVE numbers (CropVarietyImportSummary for a preview-report
    call, CropVarietyImportCommitResult for a commit-report call) — this
    function never re-derives them from `row_views` (see the module
    docstring for why that double-counts a crop shared by several new-
    variety rows). All default to 0 so an old call site that doesn't pass
    them still produces a valid workbook (with an all-zero summary block,
    same as today for the caller-side gap, never a crash)."""
    processed_at = processed_at or datetime.datetime.now(datetime.timezone.utc)
    summary = _summarize(
        row_views, completed=completed,
        crops_to_create=crops_to_create, varieties_to_create=varieties_to_create,
        varieties_to_activate=varieties_to_activate, varieties_to_deactivate=varieties_to_deactivate,
    )
    sheet1 = _result_sheet_xml(row_views, summary["statuses"])
    sheet2 = _summary_sheet_xml(
        summary, phase=phase, original_filename=original_filename, processed_at=processed_at,
    )
    return _package(sheet1, sheet2)
