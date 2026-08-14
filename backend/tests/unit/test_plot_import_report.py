"""Plot-import result workbook (round 8-2.4) — hand-rolled styled XLSX.

Verifies structure/status/security/summary via the project's own reader and
by parsing the OOXML parts (openpyxl is intentionally NOT a dependency), so
the assertions stay at a non-brittle level.
"""
from __future__ import annotations

import datetime
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from app.services import plot_import_report as R
from app.services.excel_reader import read_first_sheet
from app.services.plot_import import IMPORT_COLUMNS, TEMPLATE_DESCRIPTION_ACTION

_TS = datetime.datetime(2026, 7, 14, 10, 30, 45, tzinfo=datetime.timezone.utc)


def _view(row_number, status, *, action="close_and_start_new_cycle",
          message="", error_code=None, result_cycle_no=None, raw=None,
          report_status=None) -> dict:
    v = {
        "row_number": row_number, "action": action, "status": status,
        "message": message, "error_code": error_code,
        "result_cycle_no": result_cycle_no, "raw": raw or {},
    }
    if report_status:
        v["report_status"] = report_status
    return v


def _parts(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


def _rows_by_num(content: bytes) -> dict[int, dict[str, str]]:
    _headers, rows = read_first_sheet(content)
    return {n: v for n, v in rows}


def _build(views, **kw) -> bytes:
    kw.setdefault("phase", R.PHASE_PREVIEW)
    kw.setdefault("processed_at", _TS)
    return R.build_plot_import_result_workbook(views, **kw)


# --- structure ------------------------------------------------------------

def test_every_xml_part_is_well_formed_and_two_named_sheets() -> None:
    content = _build([_view(3, "valid", action="create_plot_with_cycle",
                            raw={"action": "create_plot_with_cycle"})])
    parts = _parts(content)
    for name, xml in parts.items():
        if name.endswith(".xml"):
            ET.fromstring(xml)  # raises on malformed
    wb = parts["xl/workbook.xml"]
    assert wb.count("<sheet ") == 2
    assert R.SHEET_RESULT in wb and R.SHEET_SUMMARY in wb


def test_row1_has_41_headers_in_order() -> None:
    headers, _ = read_first_sheet(_build([_view(3, "valid", raw={"action": "x"})]))
    assert headers == R.ALL_COLUMNS
    # Round 8-21A — 33 import columns (oracleSupplierCode/oracleInvoice/
    # refAccount added after supplierLotNo) + 11 result columns = 44.
    assert len(headers) == 44
    assert headers[:len(IMPORT_COLUMNS)] == IMPORT_COLUMNS
    assert headers[len(IMPORT_COLUMNS):] == R.RESULT_COLUMNS


def test_row2_descriptions_and_action_marker_preserved() -> None:
    by_no = _rows_by_num(_build([_view(3, "valid", raw={"action": "x"})]))
    desc = by_no[2]
    # Result columns described; action cell keeps the skip marker for re-upload.
    for col in R.RESULT_COLUMNS:
        assert desc[col] == R.RESULT_COLUMN_DESCRIPTIONS[col]
    assert desc["action"] == TEMPLATE_DESCRIPTION_ACTION


def test_data_starts_at_row_3_and_result_row_number_is_source_row() -> None:
    # Source row 7 (e.g. gaps above) must be echoed in resultRowNumber even
    # though it is the FIRST data row of the output.
    by_no = _rows_by_num(_build([_view(7, "valid", raw={"action": "x"})]))
    assert min(n for n in by_no if n >= 3) == 3
    assert by_no[3]["resultRowNumber"] == "7"


def test_thai_text_round_trips() -> None:
    by_no = _rows_by_num(_build([_view(3, "error", message="พื้นที่ (ไร่) ต้องไม่ติดลบ",
                                       raw={"action": "x", "crop": "พริก"})]))
    assert by_no[3]["crop"] == "พริก"
    assert "ต้องไม่ติดลบ" in by_no[3]["resultMessage"]


def test_sheet_has_freeze_pane_autofilter_and_status_fill() -> None:
    sheet = _parts(_build([_view(3, "error", raw={"action": "x"})]))["xl/worksheets/sheet1.xml"]
    assert '<pane ySplit="2"' in sheet and 'state="frozen"' in sheet
    assert "<autoFilter" in sheet
    styles = _parts(_build([_view(3, "error", raw={"action": "x"})]))["xl/styles.xml"]
    assert styles.count("patternType=\"solid\"") >= 7  # header/desc + 5 status


# --- status mapping (structured, never message-parsed) --------------------

def test_valid_preview_row_is_ready() -> None:
    by_no = _rows_by_num(_build([_view(3, "valid", raw={"action": "x"})]))
    assert by_no[3]["resultStatus"] == R.STATUS_READY


def test_plain_error_row_is_error() -> None:
    by_no = _rows_by_num(_build([_view(3, "error", message="bad", raw={"action": "x"})]))
    assert by_no[3]["resultStatus"] == R.STATUS_ERROR


def test_duplicate_from_structured_code_not_message() -> None:
    # Message deliberately does NOT contain the word duplicate — status must
    # still be DUPLICATE, sourced from error_code.
    by_no = _rows_by_num(_build([_view(3, "error", message="ข้อมูลตรงกัน",
                                       error_code="duplicate_rollover",
                                       raw={"action": "x"})]))
    assert by_no[3]["resultStatus"] == R.STATUS_DUPLICATE


def test_completed_on_successful_commit() -> None:
    by_no = _rows_by_num(_build(
        [_view(3, "valid", result_cycle_no=4, raw={"action": "x"})],
        phase=R.PHASE_COMMIT, completed=True))
    assert by_no[3]["resultStatus"] == R.STATUS_COMPLETED
    assert by_no[3]["resultCycleNo"] == "4"


def test_rolled_back_status_is_renderable() -> None:
    by_no = _rows_by_num(_build([_view(3, "valid", report_status=R.STATUS_ROLLED_BACK,
                                       raw={"action": "x"})]))
    assert by_no[3]["resultStatus"] == R.STATUS_ROLLED_BACK


# --- summary sheet --------------------------------------------------------

def _summary_pairs(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        sheet2 = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    root = ET.fromstring(sheet2)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    out: dict[str, str] = {}
    for row in root.iter(f"{ns}row"):
        texts = [t.text or "" for t in row.iter(f"{ns}t")]
        vals = [v.text or "" for v in row.iter(f"{ns}v")]
        cells = texts + vals
        if len(cells) >= 2:
            out[cells[0]] = cells[1]
    return out


def test_summary_counts_and_overall_blocked() -> None:
    views = [
        _view(3, "valid", raw={"action": "x"}),
        _view(4, "error", message="bad", raw={"action": "x"}),
        _view(5, "error", error_code="duplicate_rollover", raw={"action": "x"}),
    ]
    pairs = _summary_pairs(_build(views))
    assert pairs["READY"] == "1"
    assert pairs["ERROR"] == "1"
    assert pairs["DUPLICATE"] == "1"
    assert pairs["จำนวนแถวทั้งหมด"] == "3"
    assert pairs["สถานะรวม"] == R.OVERALL_BLOCKED


def test_summary_overall_ready_to_commit_when_all_valid() -> None:
    pairs = _summary_pairs(_build([_view(3, "valid", raw={"action": "x"})]))
    assert pairs["สถานะรวม"] == R.OVERALL_READY_TO_COMMIT


def test_summary_commit_completed_counts_actions() -> None:
    views = [
        _view(3, "valid", action="create_plot_with_cycle", result_cycle_no=1, raw={"action": "x"}),
        _view(4, "valid", action="close_and_start_new_cycle", result_cycle_no=5, raw={"action": "x"}),
    ]
    pairs = _summary_pairs(_build(views, phase=R.PHASE_COMMIT, completed=True))
    assert pairs["สถานะรวม"] == R.OVERALL_COMPLETED
    assert pairs["COMPLETED"] == "2"
    assert pairs["สร้างแปลง (created plots)"] == "1"
    assert pairs["จบรอบเดิม+เริ่มใหม่ (rolled-over cycles)"] == "1"


# --- raw preservation -----------------------------------------------------

def test_invalid_raw_value_is_preserved_not_blanked() -> None:
    by_no = _rows_by_num(_build([_view(3, "error", message="วันที่ปลูก ต้องเป็น YYYY-MM-DD",
                                       raw={"action": "x", "plantingDate": "not-a-date"})]))
    assert by_no[3]["plantingDate"] == "not-a-date"


def test_blank_input_cell_stays_blank() -> None:
    by_no = _rows_by_num(_build([_view(3, "valid", raw={"action": "create_plot_with_cycle"})]))
    # village not provided → the reader omits the (empty) cell entirely.
    assert "village" not in by_no[3]


# --- formula-injection protection -----------------------------------------

def test_formula_like_plot_name_is_literal_text() -> None:
    content = _build([_view(3, "valid", raw={"action": "x", "plotName": "=HYPERLINK(\"http://x\")"})])
    sheet = _parts(content)["xl/worksheets/sheet1.xml"]
    assert "<f>" not in sheet  # no formula element anywhere
    by_no = _rows_by_num(content)
    assert by_no[3]["plotName"].startswith("'=")  # apostrophe-neutralized


def test_formula_like_lot_no_is_literal_text() -> None:
    by_no = _rows_by_num(_build([_view(3, "error", raw={"action": "x", "lotNo": "+SUM(1,1)"})]))
    assert by_no[3]["lotNo"].startswith("'+")


def test_workbook_has_no_uuid_token_or_secret_material() -> None:
    # report_row_view never carries UUIDs/tokens — assert none leak into bytes.
    import re

    content = _build([_view(3, "error", message="bad",
                            raw={"action": "x", "supplierCode": "SUP001"})])
    joined = "".join(_parts(content).values())
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", joined)  # no UUID
    for word in ("qrKey", "inspection_code", "Bearer ", "token"):
        assert word not in joined


# --- filename -------------------------------------------------------------

def test_result_filename_is_server_generated_and_safe() -> None:
    assert R.result_filename(R.PHASE_PREVIEW, _TS) == "plot-import-validation-20260714-103045.xlsx"
    assert R.result_filename(R.PHASE_COMMIT, _TS) == "plot-import-result-20260714-103045.xlsx"
    # Independent of any (weird) original upload name — that only appears,
    # escaped, inside the summary sheet, never in the filename.
    pairs = _summary_pairs(_build([_view(3, "valid", raw={"action": "x"})],
                                  original_filename='=cmd|" /c calc"!A1.xlsx'))
    assert pairs["ชื่อไฟล์ต้นฉบับ"].startswith("'=")  # neutralized in the cell
