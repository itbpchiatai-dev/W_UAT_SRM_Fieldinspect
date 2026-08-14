"""Plot-status report (Report #1 "สถานะแปลง").

Two layers, both DB-free:
- the Excel workbook shape (_plot_status_workbook) — single "plot-status"
  sheet, header order, inspected-state label, computed current-yield column;
- the endpoint's filter plumbing — that each query param reaches
  report_repository.plot_status_rows unchanged (repo mocked). Follows the
  call-the-function-directly pattern of test_record_create_endpoint.py
  rather than spinning up an ASGI client.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.api.v1.reports import (
    _PLOT_STATUS_HEADERS,
    _current_expected_yield,
    _plot_status_workbook,
    export_plot_status_report,
    plot_status_report,
)
from app.schemas.report import ReportPlotStatusRow

_MODULE = "app.api.v1.reports"


def _row(**overrides) -> ReportPlotStatusRow:
    base = dict(
        plot_id=uuid4(),
        supplier_code="SUP001",
        supplier_name="Supplier One",
        plot_code="P001",
        plot_name="แปลงหนึ่ง",
        province="เชียงใหม่",
        # Default row has an ACTIVE cycle (round 7.4) — its crop/plan below.
        active_cycle_id=uuid4(),
        active_cycle_no=1,
        active_cycle_status="active",
        current_crop="พริก",
        current_variety="พริกขี้หนู",
        current_stage="ออกดอก",
        current_yield_pct=Decimal("80"),
        current_field_prep_score=8,
        current_weather_score=2,
        current_care_score=7,
        current_variety_resistance_score=6,
        expected_yield_full=Decimal("1000"),
        expected_yield_unit="kg",
        plant_count=100,
        last_inspected_at=datetime.datetime(2026, 6, 15, 9, 30, tzinfo=datetime.timezone.utc),
        last_inspected_by_code="W01",
        is_inspected=True,
    )
    base.update(overrides)
    return ReportPlotStatusRow(**base)


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


# --- workbook shape -------------------------------------------------------

def test_headers_have_expected_columns_in_order() -> None:
    assert _PLOT_STATUS_HEADERS[0] == "Supplier"
    assert _PLOT_STATUS_HEADERS[-1] == "สถานะตรวจ"
    assert "Yield ปัจจุบัน" in _PLOT_STATUS_HEADERS
    assert "ตรวจล่าสุด" in _PLOT_STATUS_HEADERS
    # round 7.4 — explicit planting-cycle status column
    assert "สถานะรอบปลูก" in _PLOT_STATUS_HEADERS


def test_workbook_shows_cycle_status_active_vs_no_cycle() -> None:
    active = _plot_status_workbook([_row()])
    assert "กำลังปลูก" in _unzip(active)["xl/worksheets/sheet1.xml"]

    # A plot between cycles (round 7.4): backend sends null identity/plan and
    # active_cycle_id=None → the report says "รอเริ่มรอบปลูก", not a stale crop.
    no_cycle = _plot_status_workbook([
        _row(
            active_cycle_id=None, active_cycle_no=None, active_cycle_status=None,
            current_crop=None, current_variety=None,
            expected_yield_full=None, expected_yield_unit=None, plant_count=None,
        ),
    ])
    sheet = _unzip(no_cycle)["xl/worksheets/sheet1.xml"]
    assert "รอเริ่มรอบปลูก" in sheet


def test_workbook_has_single_plot_status_sheet() -> None:
    parts = _unzip(_plot_status_workbook([_row()]))
    workbook = parts["xl/workbook.xml"]
    assert workbook.count("<sheet ") == 1
    assert 'name="plot-status"' in workbook


def test_workbook_renders_row_values_and_inspected_label() -> None:
    parts = _unzip(_plot_status_workbook([_row()]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert "SUP001" in sheet
    assert "เชียงใหม่" in sheet
    assert "พริก" in sheet
    assert "ตรวจแล้ว" in sheet
    # last_inspected_at rendered date-only.
    assert "2026-06-15" in sheet


def test_not_inspected_row_label() -> None:
    parts = _unzip(_plot_status_workbook([
        _row(is_inspected=False, last_inspected_at=None, last_inspected_by_code=None),
    ]))
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert "ยังไม่ตรวจ" in sheet


def test_current_expected_yield_computation() -> None:
    assert _current_expected_yield(Decimal("1000"), Decimal("80")) == Decimal("800")
    assert _current_expected_yield(None, Decimal("80")) is None
    assert _current_expected_yield(Decimal("1000"), None) is None


def test_empty_rows_still_valid_single_sheet_with_headers() -> None:
    parts = _unzip(_plot_status_workbook([]))
    assert parts["xl/workbook.xml"].count("<sheet ") == 1
    assert "สถานะตรวจ" in parts["xl/worksheets/sheet1.xml"]


# --- endpoint filter plumbing (repo mocked) -------------------------------

@pytest.mark.anyio
async def test_endpoint_passes_all_filters_to_repository() -> None:
    captured: dict = {}

    async def fake_rows(db, **kwargs):
        captured.update(kwargs)
        return []

    sid = uuid4()
    with patch(f"{_MODULE}.repo.plot_status_rows", AsyncMock(side_effect=fake_rows)):
        result = await plot_status_report(
            db=object(),
            supplier_id=sid,
            province="เชียงใหม่",
            crop="พริก",
            inspected="inspected",
            date_from=datetime.date(2026, 6, 1),
            date_to=datetime.date(2026, 6, 30),
        )

    assert result == []
    assert captured["supplier_id"] == sid
    assert captured["province"] == "เชียงใหม่"
    assert captured["crop"] == "พริก"
    assert captured["inspected"] == "inspected"
    assert captured["date_from"] == datetime.date(2026, 6, 1)
    assert captured["date_to"] == datetime.date(2026, 6, 30)


@pytest.mark.anyio
async def test_export_endpoint_returns_xlsx_from_same_repo() -> None:
    with patch(f"{_MODULE}.repo.plot_status_rows", AsyncMock(return_value=[_row()])):
        resp = await export_plot_status_report(db=object())

    assert resp.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "plot-status-report.xlsx" in resp.headers["content-disposition"]
    # Body is a real xlsx (zip) with our single sheet.
    parts = _unzip(resp.body)
    assert 'name="plot-status"' in parts["xl/workbook.xml"]
    assert "SUP001" in parts["xl/worksheets/sheet1.xml"]
