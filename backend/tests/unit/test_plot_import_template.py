"""Plot + cycle IMPORT template (round 7.5; guidance row round 8-2.1) — shape
guarantees for the BLANK workbook produced by
app.api.v1.plots._plot_template_workbook.

The template feeds a real importer: its columns are the importer's own
IMPORT_COLUMNS (action-first). Round 8-27E made this build the SAME two
sheets as the filtered template (test_plot_import_template_contextual.py), so
the app ships one template shape rather than two that merely happened to
share columns:

  Sheet 1 "นำเข้ารอบใหม่" — row 1 technical headers, row 2 the Thai
      description row the importer skips, and nothing else.
  Sheet 2 "ตัวอย่าง"      — one worked example per action, on a sheet the
      importer never reads.

That split is also a fix. The examples used to sit on rows 3+ of Sheet 1, and
only row 2 is ever skipped (plot_import._is_template_description_row) — so a
user who typed their data in below them and uploaded had the examples
executed as real rows.

No DB needed — the builder takes a plain list of suppliers.
"""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

from app.api.v1.plots import (
    _PLOT_TEMPLATE_HEADERS,
    _SHEET_EXAMPLES,
    _SHEET_NEW_CYCLE,
    _examples_sheet,
    _plot_template_workbook,
)
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import _sheet_xml, build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    SUPPORTED_ACTIONS,
    TEMPLATE_COLUMN_DESCRIPTIONS,
    TEMPLATE_DESCRIPTION_ACTION,
)


def _fake_supplier(code: str = "SUP001") -> SimpleNamespace:
    return SimpleNamespace(code=code, name="Supplier One")


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def test_headers_match_importer_columns_action_first() -> None:
    assert _PLOT_TEMPLATE_HEADERS == IMPORT_COLUMNS
    assert _PLOT_TEMPLATE_HEADERS[0] == "action"
    for col in ("supplierCode", "plotCode", "crop", "expectedYieldUnit"):
        assert col in _PLOT_TEMPLATE_HEADERS


def test_workbook_has_the_same_two_sheets_as_the_filtered_template() -> None:
    """Round 8-27E — one template shape for the whole app. The names come
    from the same constants the contextual builder uses, so the two can only
    ever drift together."""
    parts = _unzip(_plot_template_workbook([_fake_supplier()]))
    workbook = parts["xl/workbook.xml"]
    assert workbook.count("<sheet ") == 2
    assert f'name="{_SHEET_NEW_CYCLE}"' in workbook
    assert f'name="{_SHEET_EXAMPLES}"' in workbook
    assert sum(1 for n in parts if n.startswith("xl/worksheets/sheet")) == 2


def test_sheet_one_carries_every_header_but_no_example_rows() -> None:
    """The fix half of round 8-27E: the sheet the importer reads must contain
    no runnable row at all, or a user filling in beneath the examples would
    import them."""
    parts = _unzip(_plot_template_workbook([_fake_supplier()]))
    sheet1 = parts["xl/worksheets/sheet1.xml"]
    for header in IMPORT_COLUMNS:
        assert header in sheet1
    for action in ("create_plot_with_cycle", "update_current_cycle",
                   "start_next_cycle", "reactivate_plot_with_cycle", "final_plot"):
        # The action NAMES still appear inside row 2's description cell; what
        # must not exist is an example row whose own `action` cell holds one.
        assert f"<t>{action}</t>" not in sheet1


def test_the_examples_live_on_sheet_two() -> None:
    parts = _unzip(_plot_template_workbook([_fake_supplier()]))
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    for action in ("create_plot_with_cycle", "update_current_cycle",
                   "start_next_cycle", "reactivate_plot_with_cycle", "final_plot"):
        assert action in sheet2


def test_example_rows_use_the_first_supplier_code() -> None:
    parts = _unzip(_plot_template_workbook([_fake_supplier("SUP042")]))
    assert "SUP042" in parts["xl/worksheets/sheet2.xml"]


def test_no_suppliers_still_produces_a_valid_workbook() -> None:
    parts = _unzip(_plot_template_workbook([]))
    assert parts["xl/workbook.xml"].count("<sheet ") == 2
    sheet2 = parts["xl/worksheets/sheet2.xml"]
    assert "create_plot_with_cycle" in sheet2
    # Falls back to a placeholder supplier code rather than an empty cell.
    assert "SUP001" in sheet2


def test_sheet_xml_helper_renders_thai_cells() -> None:
    xml = _sheet_xml([["ชนิดพืช", "พันธุ์/สายพันธุ์"]])
    assert "ชนิดพืช" in xml
    assert "พันธุ์/สายพันธุ์" in xml


# --- round 8-2.1: Thai guidance row; round 8-2.7: three worked examples ----

def _rows_by_number(suppliers: list) -> tuple[list[str], dict[int, dict[str, str]]]:
    """Sheet 1 ("นำเข้ารอบใหม่") read back through the real importer's reader:
    (row-1 headers, {excel_row_number: {header: value}}). Verifies the
    PHYSICAL positions the round pins down, not just substring presence.

    Round 8-27E — Sheet 1 of the blank template is the header + description
    row and NOTHING else. The worked examples moved to their own sheet
    (_example_rows below): leaving them on the sheet the importer reads meant
    a user who filled their data in beneath them and uploaded had the example
    rows executed too, since only row 2 is ever skipped."""
    headers, rows = read_first_sheet(_plot_template_workbook(suppliers))
    return headers, {n: v for n, v in rows}


def _example_rows(suppliers: list) -> tuple[list[str], dict[int, dict[str, str]]]:
    """The same shape for the "ตัวอย่าง" sheet — built from the very function
    the template embeds, re-packaged as a single-sheet workbook so the real
    reader can parse it (read_first_sheet only ever reads sheet1.xml).

    Row 1 header, row 2 description, row 3 the red "examples only" notice,
    rows 4+ the worked examples."""
    supplier_code = suppliers[0].code if suppliers else "SUP001"
    content = build_xlsx([(_SHEET_EXAMPLES, _examples_sheet(supplier_code))])
    headers, rows = read_first_sheet(content)
    return headers, {n: v for n, v in rows}


def test_row_one_is_import_columns_action_first() -> None:
    headers, _ = _rows_by_number([_fake_supplier()])
    assert headers == IMPORT_COLUMNS
    assert headers[0] == "action"


def test_description_mapping_keys_match_import_columns_exactly() -> None:
    assert set(TEMPLATE_COLUMN_DESCRIPTIONS) == set(IMPORT_COLUMNS)
    # Round 8-21A added oracleSupplierCode/oracleInvoice/refAccount -> 33.
    assert len(TEMPLATE_COLUMN_DESCRIPTIONS) == 33


def test_row_two_describes_every_column_and_action_cell_is_marker() -> None:
    _headers, by_no = _rows_by_number([_fake_supplier()])
    desc = by_no[2]
    # A description in every one of the 33 columns (round 8-21A added
    # oracleSupplierCode/oracleInvoice/refAccount).
    assert set(desc) == set(IMPORT_COLUMNS)
    assert len(desc) == 33
    # A2 is the exact skip marker (this is what the importer keys off).
    assert desc["action"] == TEMPLATE_DESCRIPTION_ACTION
    # Every other cell is exactly its mapped description.
    for col in IMPORT_COLUMNS:
        assert desc[col] == TEMPLATE_COLUMN_DESCRIPTIONS[col]


def test_row_two_action_description_explains_the_three_common_workflows() -> None:
    """Round 8-2.7.1: the A2 marker cell doubles as the visible guidance for
    the action column, so it must name the three current common workflows
    (create/update/start_next_cycle) and still mention the two legacy actions
    start_next_cycle unifies — without losing its job as the exact skip
    marker prefix (pinned by the previous test)."""
    _headers, by_no = _rows_by_number([_fake_supplier()])
    desc = by_no[2]["action"]
    for action in ("create_plot_with_cycle", "update_current_cycle", "start_next_cycle"):
        assert action in desc
    assert "start_new_cycle" in desc
    assert "close_and_start_new_cycle" in desc


def test_example_sheet_rows_are_the_five_common_workflows_in_order() -> None:
    """Round 8-2.7.1: the default template's worked examples are create,
    update, and start_next_cycle (the unified "advance to the next cycle"
    action) — start_new_cycle/close_and_start_new_cycle are special-cased out
    of the defaults (see test_legacy_rollover_actions_are_not_default_example_
    rows) so a working-level user never has to choose between them. Round
    8-7A adds final_plot as a worked example. Round 8-27D adds
    reactivate_plot_with_cycle (row 6, pushing final_plot to row 7): it was
    named in the import dialog but had no example here, which is exactly what
    made the file confusing to follow."""
    _headers, by_no = _example_rows([_fake_supplier()])
    assert by_no[4]["action"] == "create_plot_with_cycle"
    assert by_no[5]["action"] == "update_current_cycle"
    assert by_no[6]["action"] == "start_next_cycle"
    assert by_no[7]["action"] == "reactivate_plot_with_cycle"
    assert by_no[8]["action"] == "final_plot"
    assert 9 not in by_no


def test_template_example_rows_are_exactly_five_common_actions() -> None:
    _headers, by_no = _example_rows([_fake_supplier()])
    example_actions = [by_no[n]["action"] for n in (4, 5, 6, 7, 8)]
    assert example_actions == [
        "create_plot_with_cycle", "update_current_cycle", "start_next_cycle",
        "reactivate_plot_with_cycle", "final_plot",
    ]
    assert len(example_actions) == 5


def test_row_two_names_the_same_five_actions_the_example_rows_show() -> None:
    """Round 8-27D — the file's own description row and its worked examples
    are the two places a user looks; they disagreeing ("3 แบบ" over four
    example rows) is what this round fixed. Pinned here so they can only
    drift together."""
    _h1, sheet1 = _rows_by_number([_fake_supplier()])
    _h2, examples = _example_rows([_fake_supplier()])
    description = sheet1[2]["action"]
    example_actions = [examples[n]["action"] for n in (4, 5, 6, 7, 8)]
    assert "5 แบบ" in description
    for action in example_actions:
        assert action in description, action


def test_legacy_rollover_actions_are_not_default_example_rows() -> None:
    """start_new_cycle and close_and_start_new_cycle are exactly the two
    behaviors start_next_cycle unifies — a special case, not the everyday
    path — so neither appears as one of the default worked examples, even
    though the backend still fully supports both unchanged (see
    test_plot_import_service.py for their parse/validate/execute coverage)."""
    _headers, by_no = _example_rows([_fake_supplier()])
    example_actions = [row["action"] for row in by_no.values()]
    assert "start_new_cycle" not in example_actions
    assert "close_and_start_new_cycle" not in example_actions
    # Still supported actions overall — just not default examples.
    assert "start_new_cycle" in SUPPORTED_ACTIONS
    assert "close_and_start_new_cycle" in SUPPORTED_ACTIONS


def test_start_next_cycle_example_row_has_a_cycle_label() -> None:
    """cycleLabel is required for start_next_cycle (round 8-2.7.1 Part B item
    6) — the shipped example must actually carry one, or it would be a broken
    worked example when a user tries it verbatim."""
    _headers, by_no = _example_rows([_fake_supplier()])
    assert by_no[6]["action"] == "start_next_cycle"
    assert by_no[6]["cycleLabel"]


# --- round 8-13A: poNumber is optional -------------------------------------

def test_po_number_description_says_optional_not_required() -> None:
    desc = TEMPLATE_COLUMN_DESCRIPTIONS["poNumber"]
    assert "ไม่บังคับ" in desc
    assert "จำเป็น" not in desc  # no requiredness marker of any kind


def test_start_next_cycle_example_row_has_a_blank_po_number() -> None:
    """Round 8-13A — at least one new-cycle worked example deliberately
    leaves poNumber blank, to show a user it's a genuinely valid row without
    one. pCode stays present (still required)."""
    _headers, by_no = _example_rows([_fake_supplier()])
    assert by_no[6]["action"] == "start_next_cycle"
    assert "poNumber" not in by_no[6]  # blank cell => key omitted by the reader
    assert by_no[6]["pCode"] == "Melon-C"


def test_create_example_row_still_carries_a_po_number() -> None:
    # The OTHER new-cycle example is free to keep a PO — round 8-13A only
    # requires at least one blank example, not that every example be blank.
    _headers, by_no = _example_rows([_fake_supplier()])
    assert by_no[4]["action"] == "create_plot_with_cycle"
    assert by_no[4]["poNumber"] == "PO25001"


def test_create_example_row_has_the_full_spec_values() -> None:
    _headers, by_no = _example_rows([_fake_supplier("SUP001")])
    create = by_no[4]
    assert create == {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001",
        "plotCode": "P101", "plotName": "แปลงตัวอย่าง (สร้างใหม่)",
        "primaryPhone": "0845552162", "additionalPhones": "0855551234",
        "village": "ต.ตัวอย่าง", "district": "อ.ตัวอย่าง", "province": "เชียงใหม่",
        "latitude": "18.7883", "longitude": "98.9853", "rai": "5",
        # Round 8-9B.1 — EXAMPLE-only password (never a real credential).
        "inspectionPasswordStatus": "not_configured", "newInspectionPassword": "1357",
        "crop": "พริก", "variety": "พริกขี้หนู", "cycleLabel": "jun2026",
        "poNumber": "PO25001", "pCode": "Melon-A",
        "lotNo": "LOT-01",
        # Round 8-12A — the Supplier's own lot number, unrelated to Auto Lot.
        "supplierLotNo": "SUP-LOT-2026-01",
        # Round 8-21A — independent back-office reference fields.
        "oracleSupplierCode": "ORC-SUP-001", "oracleInvoice": "INV-2026-0001",
        "refAccount": "ACC-0001",
        "plantingDate": "2026-06-01", "plantCount": "1000",
        "expectedYieldFull": "800", "expectedYieldUnit": "kg",
    }


def test_update_rollover_example_cycle_values_match_spec() -> None:
    _headers, by_no = _example_rows([_fake_supplier("SUP001")])
    assert (by_no[5]["plotCode"], by_no[5]["crop"], by_no[5]["variety"],
            by_no[5]["cycleLabel"], by_no[5]["lotNo"], by_no[5]["plantingDate"],
            by_no[5]["plantCount"], by_no[5]["expectedYieldFull"],
            by_no[5]["expectedYieldUnit"]) == (
        "P002", "พริก", "พริกหยวก", "may2026", "LOT-03", "2026-05-15",
        "800", "1000", "kg")
    assert (by_no[6]["plotCode"], by_no[6]["crop"], by_no[6]["variety"],
            by_no[6]["cycleLabel"], by_no[6]["lotNo"], by_no[6]["plantingDate"],
            by_no[6]["plantCount"], by_no[6]["expectedYieldFull"],
            by_no[6]["expectedYieldUnit"]) == (
        "P003", "แตงโม", "กินรี", "aug2026", "LOT-04", "2026-08-01",
        "600", "3000", "kg")


def test_non_create_examples_leave_physical_plot_fields_empty() -> None:
    """Only create_plot_with_cycle (row 4) creates the physical Plot; every
    other action acts on one that already exists, so the template must not
    put village/province/GPS/rai in those rows (would mislead)."""
    _headers, by_no = _example_rows([_fake_supplier()])
    physical = ("plotName", "village", "district", "province",
                "latitude", "longitude", "rai")
    for n in (5, 6, 7, 8):
        # Blank cells are omitted by the reader, so absence == empty.
        assert not any(f in by_no[n] for f in physical), by_no[n]


def test_examples_use_the_first_visible_supplier_code() -> None:
    _headers, by_no = _example_rows([_fake_supplier("SUP042")])
    for n in (4, 5, 6, 7, 8):
        assert by_no[n]["supplierCode"] == "SUP042"


def test_no_suppliers_falls_back_to_sup001_in_examples() -> None:
    _headers, by_no = _example_rows([])
    for n in (4, 5, 6, 7, 8):
        assert by_no[n]["supplierCode"] == "SUP001"
