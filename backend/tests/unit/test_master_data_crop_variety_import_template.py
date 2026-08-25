"""Master Data crop/variety Excel import — template contract (round 8-15A,
brief items 1-8). DB-free: master_data_repository.list_items is patched with
an AsyncMock; the actual XLSX bytes returned by build_template are parsed
back with the real hand-rolled reader/zip, exercising the real writer.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from io import BytesIO

from app.services import master_data_crop_variety_import as cv_import
from app.services.excel_reader import read_first_sheet

_M = "app.services.master_data_crop_variety_import.repo"


def _md(type_, value, parent=None, active=True, order=0):
    return SimpleNamespace(type=type_, value=value, parent=parent, active=active, order_index=order)


def _patch_list_items(crops, varieties, p_codes=None):
    async def fake(db, type=None, parent=None, active_only=False):
        if type == "crop":
            assert active_only is True  # item 6: only ACTIVE crops are ever fetched
            return crops
        if type == "variety":
            assert active_only is False  # item 4: both active AND inactive varieties
            return varieties
        if type == cv_import.P_CODE_TYPE:
            # Round 8-26B: only ACTIVE P.Codes are pre-filled, so a
            # deactivated one is never resurrected by an untouched round-trip.
            assert active_only is True
            return p_codes or []
        return []
    return patch(f"{_M}.list_items", AsyncMock(side_effect=fake))


async def test_header_has_exactly_four_columns_in_order():
    """Item 1: row 1 header. Round 8-26B inserted pCode between variety and
    varietyStatus — the layout the user asked for."""
    with _patch_list_items([], []):
        content = await cv_import.build_template(db=AsyncMock())
    headers, _rows = read_first_sheet(content)
    assert headers == ["crop", "variety", "pCode", "varietyStatus"]


async def test_row_two_is_a_skipped_description_row():
    """Item 2: row 2 is Thai description and the parser skips it."""
    crop = _md("crop", "พริก")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    # read_first_sheet returns ALL non-blank rows after the header, including
    # row 2 — the IMPORTER is what skips it (see _is_template_description_row),
    # not the raw reader. Confirm row 2's crop cell carries the marker...
    row2_no, row2 = rows[0]
    assert row2["crop"].startswith(cv_import.TEMPLATE_DESCRIPTION_MARKER)
    # ...and confirm the importer's own filter actually removes it.
    assert cv_import._is_template_description_row(row2) is True
    # The real data row (crop) must be the NEXT one, not row 2.
    assert rows[1][1]["crop"] == "พริก"


async def test_only_active_crops_are_listed():
    """Item 3 + item 6: active crops appear; an inactive crop never does."""
    active_crop = _md("crop", "พริก", active=True)
    with _patch_list_items([active_crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    crop_values = [r["crop"] for _n, r in rows[1:]]  # skip description row
    assert crop_values == ["พริก"]


async def test_variety_status_reflects_active_and_inactive():
    """Item 4 + item 7 + item 8: active → 'เปิดใช้งาน', inactive → 'ปิดใช้งาน'."""
    crop = _md("crop", "พริก")
    v_active = _md("variety", "พริกขี้หนู", parent="พริก", active=True)
    v_inactive = _md("variety", "พริกจินดา", parent="พริก", active=False)
    with _patch_list_items([crop], [v_active, v_inactive]):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    data = {r["variety"]: r["varietyStatus"] for _n, r in rows[1:]}
    assert data["พริกขี้หนู"] == "เปิดใช้งาน"
    assert data["พริกจินดา"] == "ปิดใช้งาน"


async def test_crop_with_no_variety_gets_one_blank_row():
    """Item 5: a crop with zero varieties still gets exactly one row, blank
    variety/varietyStatus."""
    crop = _md("crop", "ฟักทอง")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    _headers, rows = read_first_sheet(content)
    data_rows = [r for _n, r in rows[1:]]
    assert len(data_rows) == 1
    assert data_rows[0].get("crop") == "ฟักทอง"
    assert data_rows[0].get("variety") is None or data_rows[0].get("variety") == ""
    assert data_rows[0].get("varietyStatus") is None or data_rows[0].get("varietyStatus") == ""


async def test_variety_status_dropdown_present_with_exact_two_options():
    """Item 9: varietyStatus column has an Excel dropdown of exactly the two
    status values."""
    crop = _md("crop", "พริก")
    v = _md("variety", "พริกขี้หนู", parent="พริก")
    with _patch_list_items([crop], [v]):
        content = await cv_import.build_template(db=AsyncMock())
    with ZipFile(BytesIO(content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<dataValidation" in sheet_xml
    assert "เปิดใช้งาน,ปิดใช้งาน" in sheet_xml
    # showErrorMessage="1" — the closed status set BLOCKS an out-of-list entry.
    root = ET.fromstring(sheet_xml)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    validations = root.findall(f"{ns}dataValidations/{ns}dataValidation")
    status_rule = next(v for v in validations if v.get("sqref", "").startswith("D"))
    assert status_rule.get("showErrorMessage") == "1"


async def test_crop_dropdown_is_a_suggestion_not_a_closed_list():
    """Item 10: crop has a dropdown suggestion from active crops, but typing
    a NEW crop must still be allowed (showErrorMessage disabled). Round
    8-15A.1: the suggestion source is a defined-name reference, not an
    inline literal list — see the Part C test block below for the full
    hidden-sheet/defined-name contract."""
    crop = _md("crop", "พริก")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    with ZipFile(BytesIO(content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    root = ET.fromstring(sheet_xml)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    validations = root.findall(f"{ns}dataValidations/{ns}dataValidation")
    crop_rule = next(v for v in validations if v.get("sqref", "").startswith("A"))
    assert crop_rule.get("showErrorMessage") == "0"
    formula = crop_rule.find(f"{ns}formula1")
    assert formula.text == cv_import.CROP_OPTIONS_DEFINED_NAME


async def test_no_crop_dropdown_when_there_are_zero_active_crops():
    """Edge case backing item 10 — an empty Excel list formula is invalid, so
    the crop dropdown is simply omitted when there are no active crops yet."""
    with _patch_list_items([], []):
        content = await cv_import.build_template(db=AsyncMock())
    with ZipFile(BytesIO(content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    root = ET.fromstring(sheet_xml)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    validations = root.findall(f"{ns}dataValidations/{ns}dataValidation")
    assert all(not v.get("sqref", "").startswith("A") for v in validations)


async def test_no_uuid_or_internal_fields_anywhere_in_the_workbook():
    """Item 8: no UUID / database id / internal `type` string anywhere."""
    import re
    crop = _md("crop", "พริก")
    v = _md("variety", "พริกขี้หนู", parent="พริก")
    with _patch_list_items([crop], [v]):
        content = await cv_import.build_template(db=AsyncMock())
    with ZipFile(BytesIO(content)) as zf:
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    uuid_pattern = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    assert not uuid_pattern.search(sheet_xml)
    assert "cropStatus" not in sheet_xml  # item 11: no cropStatus column at all


# --- Round 8-15A.1 Part C: scalable crop dropdown (hidden reference sheet +
# defined name, replacing the inline-literal-list formula capped ~255 chars) --

def _workbook_xml(content: bytes) -> str:
    with ZipFile(BytesIO(content)) as zf:
        return zf.read("xl/workbook.xml").decode("utf-8")


def _sheet1_xml(content: bytes) -> str:
    with ZipFile(BytesIO(content)) as zf:
        return zf.read("xl/worksheets/sheet1.xml").decode("utf-8")


async def test_long_crop_list_over_255_chars_survives_intact():
    """A crop-name list joined with commas that would overflow Excel's
    ~255-char inline list-formula cap must still all be present and
    selectable — via the reference sheet, not an inline literal."""
    many_crops = [_md("crop", f"ชนิดพืชทดสอบชื่อยาวเพื่อทดสอบ dropdown ที่หมายเลข {i:03d}") for i in range(20)]
    joined_len = len(",".join(c.value for c in many_crops))
    assert joined_len > 255  # confirms this fixture actually exercises the overflow case

    with _patch_list_items(many_crops, []):
        content = await cv_import.build_template(db=AsyncMock())

    with ZipFile(BytesIO(content)) as zf:
        ref_xml = zf.read("xl/worksheets/sheet2.xml").decode("utf-8")
    for crop in many_crops:
        assert crop.value in ref_xml


async def test_crop_formula_is_not_an_inline_literal():
    crop = _md("crop", "พริก")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    sheet_xml = _sheet1_xml(content)
    root = ET.fromstring(sheet_xml)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    crop_rule = next(
        v for v in root.findall(f"{ns}dataValidations/{ns}dataValidation")
        if v.get("sqref", "").startswith("A")
    )
    formula_text = crop_rule.find(f"{ns}formula1").text
    assert not formula_text.startswith('"')  # never a quoted inline list
    assert "," not in formula_text  # never a comma-joined value list
    assert formula_text == cv_import.CROP_OPTIONS_DEFINED_NAME


async def test_hidden_reference_sheet_has_hidden_state():
    crop = _md("crop", "พริก")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    sheet_els = root.findall(f"{ns}sheets/{ns}sheet")
    ref_sheet = next(s for s in sheet_els if s.get("name") == cv_import._REFERENCE_SHEET_NAME)
    assert ref_sheet.get("state") == "hidden"
    main_sheet = next(s for s in sheet_els if s.get("name") == cv_import.SHEET_NAME)
    assert main_sheet.get("state") is None  # the DATA sheet is never hidden


async def test_defined_name_references_the_correct_range():
    crops = [_md("crop", "พริก"), _md("crop", "เมล่อน"), _md("crop", "ฟักทอง")]
    with _patch_list_items(crops, []):
        content = await cv_import.build_template(db=AsyncMock())
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    defined_name = root.find(f"{ns}definedNames/{ns}definedName")
    assert defined_name.get("name") == cv_import.CROP_OPTIONS_DEFINED_NAME
    assert defined_name.text == f"'{cv_import._REFERENCE_SHEET_NAME}'!$A$1:$A$3"  # exactly 3 crops


async def test_crop_validation_references_the_defined_name_by_name():
    crop = _md("crop", "พริก")
    with _patch_list_items([crop], []):
        content = await cv_import.build_template(db=AsyncMock())
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    defined_name = root.find(f"{ns}definedNames/{ns}definedName").get("name")

    sheet_root = ET.fromstring(_sheet1_xml(content))
    crop_rule = next(
        v for v in sheet_root.findall(f"{ns}dataValidations/{ns}dataValidation")
        if v.get("sqref", "").startswith("A")
    )
    assert crop_rule.find(f"{ns}formula1").text == defined_name


async def test_status_dropdown_behavior_unchanged_by_this_round():
    """Item 6/regression: varietyStatus stays a closed inline list, exactly
    the pre-8-15A.1 behavior — Part C only touches the crop column."""
    crop = _md("crop", "พริก")
    v = _md("variety", "พริกขี้หนู", parent="พริก")
    with _patch_list_items([crop], [v]):
        content = await cv_import.build_template(db=AsyncMock())
    root = ET.fromstring(_sheet1_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    status_rule = next(
        v for v in root.findall(f"{ns}dataValidations/{ns}dataValidation")
        if v.get("sqref", "").startswith("D")
    )
    assert status_rule.get("showErrorMessage") == "1"
    assert status_rule.find(f"{ns}formula1").text == '"เปิดใช้งาน,ปิดใช้งาน"'


async def test_no_active_crops_means_no_reference_sheet_or_defined_name():
    """Item 7: zero active crops → no reference sheet, no defined name, no
    broken formula — the workbook still opens normally with just the main
    (visible) sheet."""
    with _patch_list_items([], []):
        content = await cv_import.build_template(db=AsyncMock())
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    sheet_names = {s.get("name") for s in root.findall(f"{ns}sheets/{ns}sheet")}
    assert sheet_names == {cv_import.SHEET_NAME}
    assert root.find(f"{ns}definedNames") is None
    # And the workbook is still a valid, openable single-sheet .zip/.xlsx —
    # read_first_sheet must parse it without error.
    headers, _rows = read_first_sheet(content)
    assert headers == ["crop", "variety", "pCode", "varietyStatus"]


async def test_generic_build_xlsx_caller_without_hidden_sheets_is_unaffected():
    """Part C item 9 (generic writer must stay additive): a build_xlsx call
    that never passes hidden_sheets/defined_names — e.g. every OTHER sheet in
    this same workbook, and every Plot Import call — gets no hidden-sheet
    attribute and no <definedNames> block at all."""
    from app.services.excel_workbook import build_xlsx

    content = build_xlsx([("plain sheet", [["a", "b"]])])
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    sheet_el = root.find(f"{ns}sheets/{ns}sheet")
    assert sheet_el.get("state") is None
    assert root.find(f"{ns}definedNames") is None


async def test_plot_import_template_still_has_no_hidden_sheets_or_defined_names():
    """Confirms the OTHER real caller of build_xlsx (Plot Import) is
    completely unaffected by this round's additive changes — a live smoke
    check, not just the generic-writer unit test above."""
    from app.api.v1 import plots as plots_module

    suppliers = [SimpleNamespace(id="sup-1", code="SUP001", name="Test", is_active=True)]
    content = plots_module._plot_template_workbook(suppliers)
    root = ET.fromstring(_workbook_xml(content))
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    assert all(s.get("state") is None for s in root.findall(f"{ns}sheets/{ns}sheet"))
    assert root.find(f"{ns}definedNames") is None


async def test_generated_workbook_parses_back_and_first_sheet_is_still_the_data_sheet():
    crops = [_md("crop", "พริก"), _md("crop", "เมล่อน")]
    with _patch_list_items(crops, []):
        content = await cv_import.build_template(db=AsyncMock())
    headers, rows = read_first_sheet(content)
    assert headers == ["crop", "variety", "pCode", "varietyStatus"]
    # read_first_sheet always reads sheet1.xml — must be the DATA sheet
    # (พืชและพันธุ์), never the hidden reference sheet, regardless of the
    # reference sheet's presence.
    data_values = [r.get("crop") for _n, r in rows[1:]]
    assert set(data_values) == {"พริก", "เมล่อน"}
