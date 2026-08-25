"""Round 8-6A — the filter-aware CONTEXTUAL plot-import template, built from
real Plot rows. Round 8-27E cut it to TWO sheets ("นำเข้ารอบใหม่" /
"ตัวอย่าง") and made the blank template (test_plot_import_template.py) build
the same two, so the app now ships ONE template shape. The dropped sheets:
"ข้อมูลปัจจุบัน" restated values Sheet 1's own gray reference columns already
carry, and "รายการที่ไม่รวม" is now an X-Excluded-Plot-Count response header
the Plots page shows on screen. No DB needed — the builders take plain
Plot-shaped objects.
"""
from __future__ import annotations

import datetime
import re
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from app.api.v1.plots import (
    _EDITABLE_COLUMNS,
    _PLOT_TEMPLATE_HEADERS,
    _REFERENCE_COLUMNS,
    _SHEET_EXAMPLES,
    _SHEET_NEW_CYCLE,
    _STYLE_EDITABLE,
    _STYLE_EXAMPLE,
    _STYLE_REFERENCE,
    _contextual_plot_template_workbook,
    _new_cycle_row_values,
    _new_cycle_sheet,
)
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import StyledCell
from app.services.plot_import import (
    ACTION_START_NEXT,
    IMPORT_COLUMNS,
    SUPPORTED_ACTIONS,
    TEMPLATE_DESCRIPTION_ACTION,
)


def _phone(phone: str, access_type: str, *, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(phone_normalized=phone, access_type=access_type, is_active=is_active)


def _supplier(code: str = "SUP001", name: str = "Supplier One") -> SimpleNamespace:
    return SimpleNamespace(code=code, name=name)


def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=3, status="active",
        crop="พริก", variety="พริกขี้หนู", cycle_label="jun2026",
        po_number="PO25001", p_code="Melon-A",
        supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        lot_no="PO25001-P001-01", lot_no_source="auto", lot_running_no=1,
        planting_date=datetime.date(2026, 6, 1), plant_count=1000,
        expected_yield_full=Decimal("800"), expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(**over) -> SimpleNamespace:
    base = dict(
        # round 8-9B.1 — the credential-status map is keyed by plot id
        id=uuid4(),
        plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        village="ต.หนึ่ง", district="อ.หนึ่ง", province="เชียงใหม่",
        latitude=Decimal("18.7883"), longitude=Decimal("98.9853"), rai=Decimal("5"),
        supplier=_supplier(),
        active_cycle=_cycle(),
        access_phones=[_phone("0845552162", "primary"), _phone("0855551234", "additional")],
        cycles=[],  # closed history, if any — must never be read by the builders
    )
    base.update(over)
    return SimpleNamespace(**base)


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _rows_by_number(plots: list) -> tuple[list[str], dict[int, dict[str, str]]]:
    headers, rows = read_first_sheet(_contextual_plot_template_workbook(plots))
    return headers, {n: v for n, v in rows}


# --- item 12/13: 3 sheets, correct order, sheet1 headers --------------------

def test_workbook_has_exactly_two_sheets_in_order() -> None:
    """Round 8-27E — the import sheet and the examples sheet, nothing else.
    Sheet 1 must stay first: excel_reader.read_first_sheet only ever reads
    sheet1.xml."""
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    workbook = parts["xl/workbook.xml"]
    assert workbook.count("<sheet ") == 2
    # Sheet order in workbook.xml reflects the order build_xlsx was given.
    names_in_order = re.findall(r'<sheet name="([^"]+)"', workbook)
    assert names_in_order == [_SHEET_NEW_CYCLE, _SHEET_EXAMPLES]


def test_first_sheet_is_new_cycle_sheet_headers_match_import_columns() -> None:
    headers, _ = _rows_by_number([_plot()])
    assert headers == IMPORT_COLUMNS
    assert headers[0] == "action"


# --- item 14: row 2 is the description marker the importer skips -----------

def test_row_two_is_the_template_description_marker() -> None:
    _headers, by_no = _rows_by_number([_plot()])
    assert by_no[2]["action"] == TEMPLATE_DESCRIPTION_ACTION


# --- item 15/16: Sheet 1 has ONLY filtered real plots, all start_next_cycle -

def test_sheet_one_has_only_real_plot_rows_no_examples() -> None:
    _headers, by_no = _rows_by_number([_plot(plot_code="P001"), _plot(plot_code="P002")])
    data_rows = {n: v for n, v in by_no.items() if n > 2}
    assert len(data_rows) == 2
    plot_codes = {v["plotCode"] for v in data_rows.values()}
    assert plot_codes == {"P001", "P002"}
    for v in data_rows.values():
        assert v["action"] == ACTION_START_NEXT


def test_every_real_row_action_is_start_next_cycle() -> None:
    plots = [_plot(plot_code=f"P{i:03d}") for i in range(5)]
    _headers, by_no = _rows_by_number(plots)
    data_rows = [v for n, v in by_no.items() if n > 2]
    assert len(data_rows) == 5
    assert all(v["action"] == ACTION_START_NEXT for v in data_rows)


# --- item 17/19: cycleLabel/PO/P.Code/crop/variety/plan copied from active --

def test_new_cycle_row_copies_active_cycle_plan_fields() -> None:
    plot = _plot(active_cycle=_cycle(
        crop="ทุเรียน", variety="หมอนทอง", cycle_label="aug2026",
        po_number="PO99999", p_code="Durian-A",
        plant_count=250, expected_yield_full=Decimal("1234.5"), expected_yield_unit="ตัน",
    ))
    values = _new_cycle_row_values(plot)
    assert values["crop"] == "ทุเรียน"
    assert values["variety"] == "หมอนทอง"
    assert values["cycleLabel"] == "aug2026"
    assert values["poNumber"] == "PO99999"
    assert values["pCode"] == "Durian-A"
    assert values["plantCount"] == "250"
    assert values["expectedYieldFull"] == "1234.5"
    assert values["expectedYieldUnit"] == "ตัน"


# --- item 18: lotNo / plantingDate are ALWAYS blank in Sheet 1 --------------

def test_new_cycle_row_lot_no_and_planting_date_always_blank() -> None:
    plot = _plot(active_cycle=_cycle(lot_no="EXISTING-LOT-01", planting_date=datetime.date(2026, 1, 1)))
    values = _new_cycle_row_values(plot)
    assert values["lotNo"] is None
    assert values["plantingDate"] is None


def test_workbook_sheet_one_lot_no_and_planting_date_columns_are_empty() -> None:
    _headers, by_no = _rows_by_number([_plot()])
    data_row = by_no[3]
    assert "lotNo" not in data_row  # blank cells are omitted by the reader
    assert "plantingDate" not in data_row


# --- item 21: no active cycle -> blank, never infer from closed history ----

def test_plot_without_active_cycle_has_blank_cycle_fields_and_still_start_next_cycle() -> None:
    closed = _cycle(status="harvested", crop="ข้าวโพด", cycle_label="closed2025", lot_no="OLD-LOT-99")
    plot = _plot(active_cycle=None, cycles=[closed])
    values = _new_cycle_row_values(plot)
    assert values["action"] == ACTION_START_NEXT
    for field in ("crop", "variety", "cycleLabel", "poNumber", "pCode", "plantCount",
                  "expectedYieldFull", "expectedYieldUnit"):
        assert values[field] is None, field
    # Never inferred from the closed cycle in `cycles`.
    assert values["crop"] != closed.crop
    assert values["cycleLabel"] != closed.cycle_label


# Round 8-27E removed the "ข้อมูลปัจจุบัน" sheet and the four tests that
# covered it. What it showed is not lost: Sheet 1's own gray reference
# columns carry each plot's current values, and the rule those tests really
# protected — read from active_cycle, never the plot's mirror columns — is
# still asserted for Sheet 1 by test_new_cycle_row_copies_active_cycle_plan_
# fields and test_plot_without_active_cycle_has_blank_cycle_fields_and_still_
# start_next_cycle above.


# --- item 22: additionalPhones comma-separated, deterministic --------------

def test_additional_phones_comma_separated_deterministic() -> None:
    plot = _plot(access_phones=[
        _phone("0811111111", "primary"),
        _phone("0822222222", "additional"),
        _phone("0833333333", "additional"),
    ])
    values = _new_cycle_row_values(plot)
    assert values["primaryPhone"] == "0811111111"
    assert values["additionalPhones"] == "0822222222,0833333333"
    # Same input -> same output, every call (no set/dict-order dependence).
    assert _new_cycle_row_values(plot)["additionalPhones"] == values["additionalPhones"]


def test_inactive_phones_excluded_from_new_cycle_row() -> None:
    plot = _plot(access_phones=[
        _phone("0811111111", "primary"),
        _phone("0899999999", "additional", is_active=False),
    ])
    values = _new_cycle_row_values(plot)
    assert values["additionalPhones"] is None


# --- item 23: Sheet 4 has the 3 example actions (round 8-6G: shifted from
# sheet 3 to sheet 4 by the new "รายการที่ไม่รวม" sheet 3) -------------------

def test_examples_sheet_has_the_three_common_actions() -> None:
    _headers, by_no = _rows_by_number([_plot()])
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    for action in ("create_plot_with_cycle", "update_current_cycle", "start_next_cycle"):
        assert action in sheet2
    assert "ข้อมูลตัวอย่างเท่านั้น" in sheet2


def test_examples_sheet_is_not_sheet_one() -> None:
    """The importer only ever reads sheet1 — confirm the example actions are
    NOT present on Sheet 1's real-data rows."""
    _headers, by_no = _rows_by_number([_plot(plot_code="P001")])
    data_rows = [v for n, v in by_no.items() if n > 2]
    assert all(v["action"] == ACTION_START_NEXT for v in data_rows)
    assert all(v["action"] != "create_plot_with_cycle" for v in data_rows)


# --- item 24: example rows carry the RED style, verified from styles.xml ---

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


def _cell_style_index(sheet_xml: str, ref: str) -> int | None:
    m = re.search(rf'<c r="{ref}"(?:\s+s="(\d+)")?[^>]*>', sheet_xml)
    assert m is not None, f"cell {ref} not found"
    return int(m.group(1)) if m.group(1) else None


def _col_letter(n: int) -> str:
    name = ""
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return name


def test_example_row_cells_reference_the_red_fill_in_styles_xml() -> None:
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    # Row 4 = first example row (row1 header, row2 description, row3 notice).
    style_idx = _cell_style_index(sheet2, "A4")
    assert style_idx is not None
    assert fill_by_style[style_idx] == _STYLE_EXAMPLE.bg


# --- item 25/26: editable cells yellow, reference/description cells gray ---

def test_new_cycle_sheet_editable_columns_use_yellow_style_object_level() -> None:
    rows = _new_cycle_sheet([_plot()])
    data_row = rows[2]
    for col, cell in zip(_PLOT_TEMPLATE_HEADERS, data_row, strict=True):
        assert isinstance(cell, StyledCell)
        if col in _EDITABLE_COLUMNS:
            assert cell.style == _STYLE_EDITABLE, col
        else:
            assert cell.style == _STYLE_REFERENCE, col


def test_sheet_one_data_row_cells_reference_yellow_and_gray_fills_in_styles_xml() -> None:
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet1 = parts["xl/worksheets/sheet1.xml"]
    # Row 3, column order == IMPORT_COLUMNS: "crop" is column 13 (M), an
    # editable column; "plotCode" is column 3 (C), a reference column.
    crop_col_index = IMPORT_COLUMNS.index("crop") + 1
    plot_code_col_index = IMPORT_COLUMNS.index("plotCode") + 1

    crop_ref = f"{_col_letter(crop_col_index)}3"
    plot_code_ref = f"{_col_letter(plot_code_col_index)}3"
    crop_style = _cell_style_index(sheet1, crop_ref)
    plot_code_style = _cell_style_index(sheet1, plot_code_ref)
    assert fill_by_style[crop_style] == _STYLE_EDITABLE.bg
    assert fill_by_style[plot_code_style] == _STYLE_REFERENCE.bg


# --- round 8-6A.1: blank editable/example cells still carry their style ----

def test_sheet_one_blank_lot_no_cell_has_yellow_fill_in_styles_xml() -> None:
    """lotNo is ALWAYS blank in Sheet 1 (Part C) — it must still render
    yellow, not lose its fill just because there's nothing typed in it."""
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet1 = parts["xl/worksheets/sheet1.xml"]
    lot_no_col = IMPORT_COLUMNS.index("lotNo") + 1
    ref = f"{_col_letter(lot_no_col)}3"
    style_idx = _cell_style_index(sheet1, ref)
    assert style_idx is not None
    assert fill_by_style[style_idx] == _STYLE_EDITABLE.bg


def test_sheet_one_blank_planting_date_cell_has_yellow_fill_in_styles_xml() -> None:
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet1 = parts["xl/worksheets/sheet1.xml"]
    col = IMPORT_COLUMNS.index("plantingDate") + 1
    ref = f"{_col_letter(col)}3"
    style_idx = _cell_style_index(sheet1, ref)
    assert style_idx is not None
    assert fill_by_style[style_idx] == _STYLE_EDITABLE.bg


def test_plot_without_active_cycle_all_editable_columns_still_yellow() -> None:
    """Every editable column is blank when there's no active cycle (item 21) —
    all 10 must still carry the yellow style (not just lotNo/plantingDate)."""
    parts = _unzip(_contextual_plot_template_workbook([_plot(active_cycle=None)]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet1 = parts["xl/worksheets/sheet1.xml"]
    for col in _EDITABLE_COLUMNS:
        col_index = IMPORT_COLUMNS.index(col) + 1
        ref = f"{_col_letter(col_index)}3"
        style_idx = _cell_style_index(sheet1, ref)
        assert style_idx is not None, f"{col} cell missing entirely"
        assert fill_by_style[style_idx] == _STYLE_EDITABLE.bg, col


def test_examples_sheet_every_import_column_has_red_fill_including_blanks() -> None:
    """Rows 5-8 (update_current_cycle / start_next_cycle /
    reactivate_plot_with_cycle / final_plot examples) leave several
    physical-plot columns blank (test_non_create_examples_leave_physical_
    plot_fields_empty in test_plot_import_template.py) — every one of those
    blanks must still be red, not just the filled-in cells. Round 8-27D added
    the reactivate example, so the range grew by one."""
    parts = _unzip(_contextual_plot_template_workbook([_plot()]))
    fill_by_style = _cellxfs_fill_colors(parts["xl/styles.xml"])
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    for example_row in (4, 5, 6, 7, 8):
        for col_index in range(1, len(IMPORT_COLUMNS) + 1):
            ref = f"{_col_letter(col_index)}{example_row}"
            style_idx = _cell_style_index(sheet2, ref)
            assert style_idx is not None, f"row {example_row} col {col_index} missing entirely"
            assert fill_by_style[style_idx] == _STYLE_EXAMPLE.bg, (example_row, col_index)


async def test_contextual_workbook_with_blank_editable_cells_still_parses_via_importer() -> None:
    """Round 8-6A.1 changed the raw cell XML shape for blank styled cells
    (now an empty self-closing <c s="N"/> instead of being omitted) — confirm
    the real importer (plot_import.build_preview) still parses the downloaded
    file without error and still sees those cells as blank."""
    from unittest.mock import AsyncMock, patch

    from app.services.plot_import import ImportContext, build_preview

    plot = _plot(active_cycle=_cycle(po_number=None, p_code=None, lot_no=None))
    content = _contextual_plot_template_workbook([plot])
    ctx = ImportContext(allowed_supplier_id=None, can_create=True, can_update=True)

    from uuid import uuid4

    fake_supplier = SimpleNamespace(id=uuid4(), code="SUP001", is_active=True)
    fake_plot = SimpleNamespace(id=uuid4(), is_active=True)
    _M = "app.services.plot_import"
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=fake_supplier)), \
         patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=fake_plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)):
        preview = await build_preview(object(), content, ctx=ctx)

    assert preview.total_rows == 1
    row = preview.rows[0]
    assert row.payload.lot_no is None
    assert row.payload.planting_date is None


def test_description_row_uses_gray_style() -> None:
    from app.api.v1.plots import _STYLE_DESCRIPTION, _description_row
    row = _description_row()
    assert all(isinstance(c, StyledCell) and c.style == _STYLE_DESCRIPTION for c in row)


def test_header_description_and_editable_reference_columns_partition_import_columns() -> None:
    assert _REFERENCE_COLUMNS | _EDITABLE_COLUMNS == set(IMPORT_COLUMNS)
    assert not (_REFERENCE_COLUMNS & _EDITABLE_COLUMNS)


# --- item 27: read_first_sheet reads only Sheet 1, filtered rows parse -----

def test_read_first_sheet_reads_only_sheet_one_ignoring_the_other_sheets() -> None:
    plots = [_plot(plot_code="P001"), _plot(plot_code="P777")]
    headers, rows = read_first_sheet(_contextual_plot_template_workbook(plots))
    # Row 2 (n == 2) is the description marker row, not data — same
    # row-2-is-not-data convention as test_plot_import_template.py and this
    # file's own _rows_by_number helper (plot_import._validate_all is what
    # actually skips it for the importer; read_first_sheet itself returns
    # every row verbatim, by design — see excel_reader.py's docstring).
    plot_codes = {v.get("plotCode") for n, v in rows if n > 2 and "plotCode" in v}
    assert plot_codes == {"P001", "P777"}
    # Example plot codes (Sheet 4) never appear when reading "the first sheet".
    assert "P101" not in plot_codes and "P002" not in plot_codes and "P003" not in plot_codes


def test_all_supported_actions_constant_still_includes_start_next_cycle() -> None:
    assert ACTION_START_NEXT in SUPPORTED_ACTIONS


# --- generic template regression (unchanged) --------------------------------

def test_generic_template_builder_is_untouched_by_this_module() -> None:
    """Sanity: importing this module's new names doesn't change
    _plot_template_workbook's own shape — see test_plot_import_template.py
    for its full regression suite."""
    from app.api.v1.plots import _plot_template_workbook
    parts = _unzip(_plot_template_workbook([_supplier()]))
    # Round 8-27E — the blank template now builds the SAME two sheets as the
    # contextual one; that is the point of the round, not a leak from it.
    assert parts["xl/workbook.xml"].count("<sheet ") == 2
