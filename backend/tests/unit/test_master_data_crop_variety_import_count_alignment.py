"""Round 8-15A.1 Part D — commit result count ALIGNMENT regression tests.

Root cause (8-15A): commit-report's workbook summary and its ActivityLogger
metadata each re-derived counts by tallying `row_views[i]["action"]` per
ROW. A single NEW crop shared by several NEW-variety rows was therefore
counted once per row instead of once per distinct crop — e.g. 1 new crop +
2 new varieties under it reported createdCrops=2, not 1. The JSON /commit
result and the JSON /preview summary were ALREADY correct (both dedupe via
a Python `set` internally) — this file proves all four surfaces (JSON
commit result, commit-report workbook summary, ActivityLogger metadata, and
preview summary) now agree, using the SAME service functions the endpoints
call. DB-free (AsyncMocks); never a live commit.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services import master_data_crop_variety_import as cv_import
from app.services.excel_workbook import build_xlsx
from app.services.master_data_crop_variety_import_report import (
    PHASE_COMMIT,
    build_crop_variety_import_result_workbook,
)

_REPO = "app.services.master_data_crop_variety_import.repo"


def _md(type_, value, parent=None, active=True):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active)


def _xlsx(rows: list[dict]) -> bytes:
    cols = cv_import.IMPORT_COLUMNS
    data = [cols, [cv_import.TEMPLATE_DESCRIPTION_MARKER, "คำอธิบาย", "คำอธิบาย"]]
    for r in rows:
        data.append([r.get(c, "") for c in cols])
    return build_xlsx([(cv_import.SHEET_NAME, data)])


def _row(crop=None, variety=None, status=None) -> dict:
    d: dict = {}
    if crop is not None:
        d["crop"] = crop
    if variety is not None:
        d["variety"] = variety
    if status is not None:
        d["varietyStatus"] = status
    return d


def _patch_lookup(existing_crops=None, existing_varieties=None):
    existing_crops = existing_crops or []
    existing_varieties = existing_varieties or []

    async def fake(db, type, values):
        pool = existing_crops if type == "crop" else existing_varieties
        return [m for m in pool if m.value in values]

    return patch(f"{_REPO}.list_by_type_values", AsyncMock(side_effect=fake))


def _patch_writes():
    async def fake_create(db, payload):
        return SimpleNamespace(type=payload.type, value=payload.value, parent=payload.parent, active=True)

    async def fake_update(db, item, payload):
        if payload.active is not None:
            item.active = payload.active
        return item

    return AsyncMock(side_effect=fake_create), AsyncMock(side_effect=fake_update)


async def _preview_state_for(content: bytes, **lookup_kwargs):
    with _patch_lookup(**lookup_kwargs):
        preview = await cv_import.build_preview(AsyncMock(), content)
    return preview.preview_state


# --- 1. new crop + 2 new varieties → 1 crop / 2 varieties (everywhere) ----

async def test_json_commit_result_counts_one_crop_not_two():
    content = _xlsx([
        _row(crop="มังคุด", variety="มังคุดทอง"),
        _row(crop="มังคุด", variety="มังคุดสีทอง"),
    ])
    state = await _preview_state_for(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.created_crops == 1
    assert result.created_varieties == 2
    # The crop was only ever create()'d ONCE, not once per variety row.
    crop_creates = [c for c in create_mock.call_args_list if c.args[1].type == "crop"]
    assert len(crop_creates) == 1


async def test_preview_summary_agrees_with_commit_result():
    content = _xlsx([
        _row(crop="มังคุด", variety="มังคุดทอง"),
        _row(crop="มังคุด", variety="มังคุดสีทอง"),
    ])
    with _patch_lookup():
        preview = await cv_import.build_preview(AsyncMock(), content)
    assert preview.summary.crops_to_create == 1
    assert preview.summary.varieties_to_create == 2


async def test_commit_report_workbook_summary_matches_commit_result():
    content = _xlsx([
        _row(crop="มังคุด", variety="มังคุดทอง"),
        _row(crop="มังคุด", variety="มังคุดสีทอง"),
    ])
    state = await _preview_state_for(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result, row_views = await cv_import.commit_row_views(AsyncMock(), content, preview_state=state)

    assert result.created_crops == 1  # sanity: same bug surface as test 1 above

    workbook = build_crop_variety_import_result_workbook(
        row_views, phase=PHASE_COMMIT, completed=True,
        crops_to_create=result.created_crops, varieties_to_create=result.created_varieties,
        varieties_to_activate=result.activated_varieties, varieties_to_deactivate=result.deactivated_varieties,
    )
    # sheet2 ("สรุป") is label/value pairs in columns A/B, no header row — read
    # it directly via the XML rather than read_first_sheet (which assumes a
    # header row on row 1 and would misread the first label/value pair as
    # column headers).
    import xml.etree.ElementTree as ET
    from zipfile import ZipFile
    from io import BytesIO
    with ZipFile(BytesIO(workbook)) as zf:
        sheet2_xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(sheet2_xml)
    pairs = {}
    for row_el in root.findall(f"{ns}sheetData/{ns}row"):
        cells = row_el.findall(f"{ns}c")
        if len(cells) != 2:
            continue
        label_el = cells[0].find(f"{ns}is/{ns}t")
        v_el = cells[1].find(f"{ns}v")
        value_el = v_el if v_el is not None else cells[1].find(f"{ns}is/{ns}t")
        if label_el is not None and value_el is not None:
            pairs[label_el.text] = value_el.text
    assert pairs["สร้างชนิดพืชใหม่ (จำนวนชนิดพืช ไม่ซ้ำ)"] == "1"
    assert pairs["สร้างพันธุ์ใหม่"] == "2"


async def test_activity_log_metadata_matches_commit_result():
    import io
    from fastapi import UploadFile
    from app.api.v1.masterdata import commit_crop_variety_import_report

    content = _xlsx([
        _row(crop="มังคุด", variety="มังคุดทอง"),
        _row(crop="มังคุด", variety="มังคุดสีทอง"),
    ])
    state = await _preview_state_for(content)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        logger_instance = logger_cls.return_value
        logger_instance.log = AsyncMock()
        upload = UploadFile(file=io.BytesIO(content), filename="import.xlsx")
        await commit_crop_variety_import_report(
            request=SimpleNamespace(), user=SimpleNamespace(id=uuid4()),
            file=upload, preview_state=state.model_dump_json(by_alias=True), db=AsyncMock(),
        )
    metadata = logger_instance.log.await_args.kwargs["metadata"]
    assert metadata["createdCrops"] == 1
    assert metadata["createdVarieties"] == 2


# --- 2. existing crop + 2 new varieties → 0 crop / 2 varieties -----------

async def test_existing_crop_plus_two_new_varieties_creates_zero_crops():
    crop = _md("crop", "พริก", active=True)
    content = _xlsx([
        _row(crop="พริก", variety="พริกขี้หนู"),
        _row(crop="พริก", variety="พริกจินดา"),
    ])
    state = await _preview_state_for(content, existing_crops=[crop])
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop]), patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.created_crops == 0
    assert result.created_varieties == 2
    assert all(c.args[1].type != "crop" for c in create_mock.call_args_list)


# --- 3. activate/deactivate/skipped counts unaffected by the fix ---------

async def test_activate_deactivate_skipped_counts_no_regression():
    crop = _md("crop", "พริก", active=True)
    v_to_activate = _md("variety", "พริกขี้หนู", parent="พริก", active=False)
    v_to_deactivate = _md("variety", "พริกจินดา", parent="พริก", active=True)
    v_unchanged = _md("variety", "พริกหยวก", parent="พริก", active=True)
    content = _xlsx([
        _row(crop="พริก", variety="พริกขี้หนู", status="เปิดใช้งาน"),
        _row(crop="พริก", variety="พริกจินดา", status="ปิดใช้งาน"),
        _row(crop="พริก", variety="พริกหยวก", status="เปิดใช้งาน"),
    ])
    existing_varieties = [v_to_activate, v_to_deactivate, v_unchanged]
    state = await _preview_state_for(content, existing_crops=[crop], existing_varieties=existing_varieties)
    create_mock, update_mock = _patch_writes()
    with _patch_lookup(existing_crops=[crop], existing_varieties=existing_varieties), \
         patch(f"{_REPO}.create", create_mock), patch(f"{_REPO}.update", update_mock):
        result = await cv_import.commit(AsyncMock(), content, preview_state=state)
    assert result.created_crops == 0
    assert result.created_varieties == 0
    assert result.activated_varieties == 1
    assert result.deactivated_varieties == 1
    assert result.skipped_rows == 1
    assert result.total_rows == 3
