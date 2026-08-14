"""Master Data crop/variety import result workbook (round 8-15A, brief items
40-44). Pure builder tests — no DB, no HTTP; `row_views` are hand-built
dicts in the exact shape master_data_crop_variety_import.row_view produces.
"""
from __future__ import annotations

import re

from app.services.excel_reader import read_first_sheet
from app.services.master_data_crop_variety_import import (
    ACTION_CREATE_CROP_AND_VARIETY,
    ACTION_NONE,
    ROW_STATUS_ERROR,
    ROW_STATUS_READY,
    ROW_STATUS_SKIPPED,
)
from app.services.master_data_crop_variety_import_report import (
    ALL_COLUMNS,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_READY,
    STATUS_SKIPPED,
    build_crop_variety_import_result_workbook,
    map_row_status,
)


def _view(row_number, crop, variety, status_text, *, row_status, action=ACTION_NONE, error_message=""):
    return {
        "row_number": row_number,
        "raw": {"crop": crop, "variety": variety, "varietyStatus": status_text},
        "row_status": row_status,
        "action": action,
        "error_message": error_message,
    }


def test_item40_preview_statuses_are_ready_skipped_error():
    views = [
        _view(3, "มังคุด", "มังคุดทอง", "เปิดใช้งาน", row_status=ROW_STATUS_READY, action=ACTION_CREATE_CROP_AND_VARIETY),
        _view(4, "พริก", None, None, row_status=ROW_STATUS_SKIPPED),
        _view(5, "เมล่อน", "มะม่วง", "ไม่ทราบ", row_status=ROW_STATUS_ERROR, error_message="varietyStatus ไม่ถูกต้อง"),
    ]
    assert [map_row_status(v, completed=False) for v in views] == [STATUS_READY, STATUS_SKIPPED, STATUS_ERROR]

    content = build_crop_variety_import_result_workbook(views, phase="PREVIEW", completed=False)
    headers, rows = read_first_sheet(content)
    assert headers == ALL_COLUMNS
    statuses = {r["rowNumber"]: r["importStatus"] for _n, r in rows}
    assert statuses == {"3": "READY", "4": "SKIPPED", "5": "ERROR"}


def test_item41_completed_statuses_are_completed_skipped():
    views = [
        _view(3, "มังคุด", "มังคุดทอง", "เปิดใช้งาน", row_status=ROW_STATUS_READY, action=ACTION_CREATE_CROP_AND_VARIETY),
        _view(4, "พริก", None, None, row_status=ROW_STATUS_SKIPPED),
    ]
    assert [map_row_status(v, completed=True) for v in views] == [STATUS_COMPLETED, STATUS_SKIPPED]

    content = build_crop_variety_import_result_workbook(views, phase="COMMIT", completed=True)
    headers, rows = read_first_sheet(content)
    statuses = {r["rowNumber"]: r["importStatus"] for _n, r in rows}
    assert statuses == {"3": "COMPLETED", "4": "SKIPPED"}
    assert "ERROR" not in statuses.values()  # a completed commit never leaves an ERROR row


def test_item42_row_number_and_error_message_round_trip_exactly():
    views = [
        _view(
            7, "เมล่อน", "มะม่วง", "xxx", row_status=ROW_STATUS_ERROR,
            error_message="varietyStatus ต้องเป็น 'เปิดใช้งาน' หรือ 'ปิดใช้งาน' เท่านั้น (พบ: 'xxx')",
        ),
    ]
    content = build_crop_variety_import_result_workbook(views, phase="PREVIEW", completed=False)
    _headers, rows = read_first_sheet(content)
    row_no, row = rows[0]
    assert row["rowNumber"] == "7"
    assert row["errorMessage"] == "varietyStatus ต้องเป็น 'เปิดใช้งาน' หรือ 'ปิดใช้งาน' เท่านั้น (พบ: 'xxx')"
    assert row["crop"] == "เมล่อน"
    assert row["variety"] == "มะม่วง"


def test_item43_no_stack_trace_or_internal_ids():
    views = [
        _view(3, "มังคุด", "มังคุดทอง", "เปิดใช้งาน", row_status=ROW_STATUS_READY, action=ACTION_CREATE_CROP_AND_VARIETY),
    ]
    content = build_crop_variety_import_result_workbook(views, phase="PREVIEW", completed=False)
    text = content.decode("latin-1")  # zip bytes — decode loosely just to substring-scan
    for forbidden in ("Traceback", "File \"", "raise ", "Exception", ".py\", line"):
        assert forbidden not in text
    uuid_pattern = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    # Scan only the actual worksheet/summary XML (not the deflate-compressed
    # zip bytes, which can coincidentally contain hex-looking runs) for a
    # UUID pattern — parse the sheet text properly instead of the raw zip.
    from io import BytesIO
    from zipfile import ZipFile
    with ZipFile(BytesIO(content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        summary_xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert not uuid_pattern.search(sheet_xml)
    assert not uuid_pattern.search(summary_xml)


def test_item44_thai_unicode_survives_the_round_trip():
    crop = "แตงกวาญี่ปุ่นพันธุ์พิเศษ ๑๒๓"
    variety = "พันธุ์ทดสอบ กขคง ñ é 中文"
    views = [
        _view(3, crop, variety, "เปิดใช้งาน", row_status=ROW_STATUS_READY, action=ACTION_CREATE_CROP_AND_VARIETY),
    ]
    content = build_crop_variety_import_result_workbook(views, phase="PREVIEW", completed=False)
    _headers, rows = read_first_sheet(content)
    _row_no, row = rows[0]
    assert row["crop"] == crop
    assert row["variety"] == variety
