"""Plot-import result workbook (round 8-2.4) — hand-rolled styled XLSX.

Renders a two-sheet Excel result file for a Plot Import preview or commit:
  * "ผลการนำเข้า" — the 18 input columns (echoed RAW, exactly as typed) plus
    5 server-only result columns, one row per data row, with per-status fills,
    a frozen header, an auto-filter and readable column widths.
  * "สรุป"        — filename / timestamp / phase / overall status / counts.

No new dependency: this extends the project's hand-rolled OOXML approach
(services/excel_workbook.py is inline-string only, so styles/freeze/fills are
built here directly — kept focused, NOT a generic Excel framework). All
user-provided text is written as an inline string AND neutralized against
spreadsheet formula injection. Never writes any UUID / token / qrKey /
inspection code / password / stack trace into the workbook.
"""
from __future__ import annotations

import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.services.plot_import import (
    ERROR_CODE_DUPLICATE_ROLLOVER,
    IMPORT_COLUMNS,
    TEMPLATE_COLUMN_DESCRIPTIONS,
)

# --- Row + overall + phase status vocabulary ------------------------------
STATUS_READY = "READY"
STATUS_ERROR = "ERROR"
STATUS_DUPLICATE = "DUPLICATE"
STATUS_COMPLETED = "COMPLETED"
STATUS_ROLLED_BACK = "ROLLED_BACK"

OVERALL_READY_TO_COMMIT = "READY_TO_COMMIT"
OVERALL_BLOCKED = "BLOCKED"
OVERALL_COMPLETED = "COMPLETED"
OVERALL_ROLLED_BACK = "ROLLED_BACK"

PHASE_PREVIEW = "PREVIEW"
PHASE_COMMIT = "COMMIT"

SHEET_RESULT = "ผลการนำเข้า"
SHEET_SUMMARY = "สรุป"

# Server-only columns appended after the input columns. Round 8-5B adds the
# lot preview (lotMode/proposedLotNo) + the real committed lot (resultLotNo/
# resultLotNoSource/resultLotRunningNo).
RESULT_COLUMNS: list[str] = [
    "resultRowNumber",
    "resultStatus",
    "resultMessage",
    "resultCycleNo",
    "lotMode",
    "proposedLotNo",
    "resultLotNo",
    "resultLotNoSource",
    "resultLotRunningNo",
    "resultInspectionPassword",
    "resultProcessedAt",
]

# The import column whose value must NEVER appear in an output workbook.
COLUMN_NEW_INSPECTION_PASSWORD = "newInspectionPassword"

# Safe, fixed statuses for the resultInspectionPassword column (round 8-9B.1).
# None of them reveals the password, its length, or its last digits.
CREDENTIAL_RESULT_KEEP = "คงรหัสเดิม"
CREDENTIAL_RESULT_SET = "ตั้งรหัสแล้ว"
CREDENTIAL_RESULT_REPLACED = "เปลี่ยนรหัสแล้ว"
CREDENTIAL_RESULT_FAILED = "ไม่สำเร็จ"
# On a PREVIEW (validation) workbook nothing has happened yet — say what WILL
# happen, in the same secret-free vocabulary.
CREDENTIAL_RESULT_WILL_SET = "จะตั้งรหัสใหม่"
CREDENTIAL_RESULT_WILL_REPLACE = "จะเปลี่ยนรหัส"

RESULT_COLUMN_DESCRIPTIONS: dict[str, str] = {
    "resultRowNumber": "เลขแถวจริงในไฟล์ Excel ต้นฉบับ",
    "resultStatus": "ผลการตรวจสอบหรือนำเข้า ระบบเป็นผู้ระบุ",
    "resultMessage": "รายละเอียดข้อผิดพลาดหรือผลการนำเข้า",
    "resultCycleNo": "เลขรอบปลูกที่สร้างหรือแก้สำเร็จ",
    "lotMode": "วิธีจัดการ Lot ของแถวนี้: auto=สร้างอัตโนมัติ, manual=กรอกเอง, preserve=คงค่าเดิม",
    "proposedLotNo": "Lot ที่คาดว่าจะได้ (Auto แสดงเป็น {cycleLabel}-{supplierCode}-{pCode}-###; เลขรันจริงออกตอนนำเข้า)",
    "resultLotNo": "Lot จริงที่ระบบบันทึก (หลังนำเข้าสำเร็จ)",
    "resultLotNoSource": "ที่มาของ Lot จริง: auto/manual/legacy",
    "resultLotRunningNo": "เลขรันของ Auto Lot จริง (ว่างถ้าไม่ใช่ auto)",
    "resultInspectionPassword": "ผลของรหัสยืนยันแปลงในแถวนี้ (ระบบไม่แสดงตัวรหัส)",
    "resultProcessedAt": "วันเวลาที่ระบบประมวลผล",
}

ALL_COLUMNS: list[str] = [*IMPORT_COLUMNS, *RESULT_COLUMNS]


def _credential_result(view: dict, *, completed: bool) -> str:
    """The safe per-row password status for the result workbook (round
    8-9B.1).

    Reads only the STRUCTURED credential_change the importer computed — never
    the raw cell, never a hash/digest/version. An errored row reports
    "ไม่สำเร็จ" regardless of what it intended, because an all-or-nothing
    commit means nothing was written for it."""
    change = view.get("credential_change")
    if change is None:
        return CREDENTIAL_RESULT_KEEP
    if view.get("status") != "valid":
        return CREDENTIAL_RESULT_FAILED
    if not completed:
        return (
            CREDENTIAL_RESULT_WILL_SET if change == "set"
            else CREDENTIAL_RESULT_WILL_REPLACE
        )
    return CREDENTIAL_RESULT_SET if change == "set" else CREDENTIAL_RESULT_REPLACED


def map_row_status(view: dict, *, completed: bool) -> str:
    """Row status from STRUCTURED state — never by parsing the Thai message.
    A valid row is COMPLETED after a successful commit, otherwise READY; an
    error row is DUPLICATE only when tagged with the duplicate-rollover code.

    An explicit `report_status` on the view wins (e.g. ROLLED_BACK) — this
    round no endpoint sets it, but it keeps the builder able to render every
    status including ROLLED_BACK."""
    forced = view.get("report_status")
    if forced:
        return forced
    if view.get("status") == "valid":
        return STATUS_COMPLETED if completed else STATUS_READY
    if view.get("error_code") == ERROR_CODE_DUPLICATE_ROLLOVER:
        return STATUS_DUPLICATE
    return STATUS_ERROR


def result_filename(phase: str, processed_at: datetime.datetime) -> str:
    """Server-generated download name (never derived from the user's upload
    name, so it is always safe): validation vs result + a timestamp."""
    stem = "validation" if phase == PHASE_PREVIEW else "result"
    return f"plot-import-{stem}-{processed_at:%Y%m%d-%H%M%S}.xlsx"


# --- formula-injection neutralization -------------------------------------
_FORMULA_LEAD = ("=", "+", "-", "@")


def _formula_safe(text: str) -> str:
    """Neutralize spreadsheet formula injection: a user value starting with
    = + - @ is prefixed with an apostrophe so Excel/Sheets show it as literal
    text and never execute it. (Cells are already inline strings, but this is
    the recognized belt-and-suspenders mitigation.)"""
    if text and text[0] in _FORMULA_LEAD:
        return "'" + text
    return text


# --- low-level cell/OOXML helpers -----------------------------------------

def _col_ref(index0: int) -> str:
    """0-based column index → Excel letter (0→A)."""
    name = ""
    n = index0 + 1
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(col0: int, row: int, value, style: int) -> str:
    ref = f"{_col_ref(col0)}{row}"
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(_formula_safe(str(value)))
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr">'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


# Style indexes into styles.xml cellXfs (see _STYLES_XML).
_S_DEFAULT = 0
_S_HEADER = 1
_S_DESC = 2
_S_STATUS = {
    STATUS_READY: 3,
    STATUS_ERROR: 4,
    STATUS_DUPLICATE: 5,
    STATUS_COMPLETED: 6,
    STATUS_ROLLED_BACK: 7,
}

# Per-column width for the result sheet — one entry per ALL_COLUMNS entry
# (33 import + 11 result = 44 columns). Round 8-21A added oracleSupplierCode/
# oracleInvoice/refAccount after supplierLotNo (see plot_import.IMPORT_
# COLUMNS) — inserted at the matching position below, and also corrected a
# pre-existing one-short count in the result-column section (resultProcessedAt
# had no width entry of its own before this round).
_COL_WIDTHS = [
    # import columns, in IMPORT_COLUMNS order (action..supplierLotNo, 19 cols):
    22, 14, 16, 24, 16, 28, 14, 14, 14, 12, 12, 8, 14, 14, 16, 14, 14, 14, 14,
    18, 18, 18,  # oracleSupplierCode, oracleInvoice, refAccount (round 8-21A)
    # plantingDate..newInspectionPassword (11 cols):
    12, 16, 14, 14, 12, 12, 20, 16, 20, 20, 20,
    # result columns:
    12, 14, 50, 12, 12, 24, 20, 14, 14, 20, 20,
]

# fills: 0 none, 1 gray125 (both reserved), 2 header, 3 desc, then one solid
# per status color: 4 READY, 5 ERROR, 6 DUPLICATE, 7 COMPLETED, 8 ROLLED_BACK.
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="3">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '<font><i/><sz val="10"/><color rgb="FF666666"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="9">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE8E8E8"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD7F0D7"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF7D0D0"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFCE2C0"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF9BD79B"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF3B0"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="8">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="6" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="7" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="8" borderId="0" xfId="0" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)


# --- summary computation ---------------------------------------------------

def _summarize(row_views: list[dict], *, phase: str, completed: bool) -> dict:
    from app.services import plot_import as _pi

    statuses = [map_row_status(v, completed=completed) for v in row_views]
    counts = {s: statuses.count(s) for s in (
        STATUS_READY, STATUS_ERROR, STATUS_DUPLICATE, STATUS_COMPLETED, STATUS_ROLLED_BACK,
    )}
    if completed:
        overall = OVERALL_COMPLETED
    elif counts[STATUS_ERROR] or counts[STATUS_DUPLICATE]:
        overall = OVERALL_BLOCKED
    else:
        overall = OVERALL_READY_TO_COMMIT

    action_counts = {a: 0 for a in (
        _pi.ACTION_CREATE, _pi.ACTION_START, _pi.ACTION_UPDATE, _pi.ACTION_ROLLOVER,
        _pi.ACTION_REACTIVATE_WITH_CYCLE, _pi.ACTION_FINAL,
    )}
    if completed:
        for v in row_views:
            a = v.get("action")
            # A start_next_cycle row (round 8-2.7.1) is bucketed by its
            # resolved_action (the ACTUAL outcome _execute_row recorded) —
            # never by its own literal action string, which isn't one of the
            # keys above. reactivate_plot_with_cycle (round 8-6H) and
            # final_plot (round 8-7A) are both unambiguous and bucketed by
            # their own literal action string.
            if a == _pi.ACTION_START_NEXT:
                a = v.get("resolved_action")
            if a in action_counts:
                action_counts[a] += 1
    return {
        "statuses": statuses,
        "counts": counts,
        "overall": overall,
        "created_plots": action_counts[_pi.ACTION_CREATE],
        "started_cycles": action_counts[_pi.ACTION_START],
        "updated_cycles": action_counts[_pi.ACTION_UPDATE],
        "rolled_over_cycles": action_counts[_pi.ACTION_ROLLOVER],
        "reactivated_plots": action_counts[_pi.ACTION_REACTIVATE_WITH_CYCLE],
        "finalized_plots": action_counts[_pi.ACTION_FINAL],
        "total": len(row_views),
    }


# --- sheet XML -------------------------------------------------------------

def _result_sheet_xml(
    row_views: list[dict], statuses: list[str], *, processed_at: datetime.datetime,
    completed: bool = False,
) -> str:
    processed_str = f"{processed_at:%Y-%m-%d %H:%M:%S}"
    rows_xml: list[str] = []

    # Row 1 — headers (bold).
    header_cells = "".join(
        _cell(i, 1, col, _S_HEADER) for i, col in enumerate(ALL_COLUMNS)
    )
    rows_xml.append(f'<row r="1">{header_cells}</row>')

    # Row 2 — Thai descriptions (input + result). action cell keeps the import
    # description marker, so a re-uploaded result workbook still skips row 2.
    desc_values = (
        [TEMPLATE_COLUMN_DESCRIPTIONS[c] for c in IMPORT_COLUMNS]
        + [RESULT_COLUMN_DESCRIPTIONS[c] for c in RESULT_COLUMNS]
    )
    desc_cells = "".join(_cell(i, 2, v, _S_DESC) for i, v in enumerate(desc_values))
    rows_xml.append(f'<row r="2">{desc_cells}</row>')

    # Rows 3+ — one per data row. Input columns echo RAW input (plain); result
    # columns are server-only and carry the status fill.
    for offset, (view, status) in enumerate(zip(row_views, statuses)):
        excel_row = 3 + offset
        raw = view.get("raw") or {}
        status_style = _S_STATUS[status]
        cells = [
            # Round 8-9B.1 — newInspectionPassword is NEVER echoed back, even
            # if a `raw` dict somehow still carried it (plot_import.
            # report_row_view already strips it). Two independent guards,
            # because a plaintext password reaching a downloadable workbook is
            # the single worst failure mode of this feature.
            _cell(
                i, excel_row,
                None if col == COLUMN_NEW_INSPECTION_PASSWORD else raw.get(col),
                _S_DEFAULT,
            )
            for i, col in enumerate(IMPORT_COLUMNS)
        ]
        result_values = [
            view.get("row_number"),
            status,
            view.get("message") or "",
            view.get("result_cycle_no"),
            view.get("lot_mode"),
            view.get("proposed_lot_no"),
            view.get("result_lot_no"),
            view.get("result_lot_no_source"),
            view.get("result_lot_running_no"),
            _credential_result(view, completed=completed),
            processed_str,
        ]
        for j, val in enumerate(result_values):
            cells.append(_cell(len(IMPORT_COLUMNS) + j, excel_row, val, status_style))
        rows_xml.append(f'<row r="{excel_row}">{"".join(cells)}</row>')

    last_row = 2 + len(row_views)
    last_col = _col_ref(len(ALL_COLUMNS) - 1)
    cols_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(_COL_WIDTHS)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="2" topLeftCell="A3" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{cols_xml}</cols>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        f'<autoFilter ref="A2:{last_col}{last_row}"/>'
        '</worksheet>'
    )


def _summary_sheet_xml(
    summary: dict, *, phase: str, original_filename: str | None,
    processed_at: datetime.datetime,
) -> str:
    c = summary["counts"]
    pairs: list[tuple[str, object]] = [
        ("ชื่อไฟล์ต้นฉบับ", original_filename or "-"),
        ("วันเวลาที่ประมวลผล", f"{processed_at:%Y-%m-%d %H:%M:%S}"),
        ("ขั้นตอน (Phase)", phase),
        ("สถานะรวม", summary["overall"]),
        ("จำนวนแถวทั้งหมด", summary["total"]),
        ("READY", c[STATUS_READY]),
        ("ERROR", c[STATUS_ERROR]),
        ("DUPLICATE", c[STATUS_DUPLICATE]),
        ("COMPLETED", c[STATUS_COMPLETED]),
        ("ROLLED_BACK", c[STATUS_ROLLED_BACK]),
        ("สร้างแปลง (created plots)", summary["created_plots"]),
        ("เริ่มรอบปลูก (started cycles)", summary["started_cycles"]),
        ("แก้รอบปลูก (updated cycles)", summary["updated_cycles"]),
        ("จบรอบเดิม+เริ่มใหม่ (rolled-over cycles)", summary["rolled_over_cycles"]),
        ("เปิดแปลงและเริ่มรอบใหม่ (reactivated plots)", summary["reactivated_plots"]),
        ("ลงผลผลิตสุดท้าย+ปิดรอบ (finalized plots)", summary["finalized_plots"]),
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
        '</worksheet>'
    )


# --- package assembly ------------------------------------------------------

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
            '</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets>'
            + "".join(
                f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, name in enumerate(names, start=1)
            )
            + '</sheets></workbook>',
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
            '</Relationships>',
        )
        zf.writestr("xl/styles.xml", _STYLES_XML)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buffer.getvalue()


def build_plot_import_result_workbook(
    row_views: list[dict],
    *,
    phase: str,
    completed: bool = False,
    original_filename: str | None = None,
    processed_at: datetime.datetime | None = None,
) -> bytes:
    """Pure builder → XLSX bytes (no temp file, no DB). `row_views` are the
    neutral dicts from plot_import.report_row_view. `phase` is PREVIEW or
    COMMIT; `completed` is True only for a successfully-committed file (every
    valid row → COMPLETED). Deterministic apart from `processed_at`."""
    processed_at = processed_at or datetime.datetime.now(datetime.timezone.utc)
    summary = _summarize(row_views, phase=phase, completed=completed)
    sheet1 = _result_sheet_xml(
        row_views, summary["statuses"], processed_at=processed_at, completed=completed
    )
    sheet2 = _summary_sheet_xml(
        summary, phase=phase, original_filename=original_filename,
        processed_at=processed_at,
    )
    return _package(sheet1, sheet2)
