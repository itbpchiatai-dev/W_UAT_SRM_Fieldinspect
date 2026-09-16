"""Round V — the plot Excel import records what it says it records, and its
colours tell the truth about each column.

Four changes, each with the failure it closes:

1. A create row with a blank plotCode — the ONLY valid create row now — never
   resolved its supplier. An early return in _validate_row still treated a
   blank code as "unusable" for every action, although round B had made it
   mean "generate the code" on create. Such a row previewed as VALID with no
   supplier, no scope check and no Master Data check, then crashed at commit
   on `assert state.supplier is not None`: exactly the file an admin uploads
   to register new plots.
2. A supplier code the plot code cannot be built from was only discovered at
   commit, as an unhandled error. It is a preview error now.
3. update_current_cycle validated plotName / village / district / province /
   latitude / longitude / rai and then wrote none of them, while reporting
   success. It writes them now (blank keeps the stored value).
4. crop / variety / cycleLabel / pCode are fixed once the cycle exists — the
   Auto Lot is built from them. An update row may leave them blank or repeat
   them; a different value is refused, naming what the cell has to be. The
   same rule holds for the web edit (PATCH) and the repository underneath.

And the template paints every column one of four kinds, built from one map
that has to agree with the rules above.
"""
from __future__ import annotations

import datetime
import io
import zipfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import plots as plots_api
from app.repositories import plot_cycle_repository as cycle_repo
from app.schemas.plot import PlotCycleUpdate
from app.services import plot_import
from app.services.excel_reader import read_first_sheet
from app.services.excel_workbook import build_xlsx
from app.services.plot_import import (
    IMPORT_COLUMNS,
    ImportContext,
    ImportHasErrors,
    build_preview,
    commit_import,
)

_M = "app.services.plot_import"


def _xlsx(rows: list[dict]) -> bytes:
    data: list[list] = [list(IMPORT_COLUMNS)]
    data += [[r.get(c) for c in IMPORT_COLUMNS] for r in rows]
    return build_xlsx([("plots", data)])


def _ctx(*, allowed=None) -> ImportContext:
    return ImportContext(allowed_supplier_id=allowed, can_create=True, can_update=True)


def _supplier(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        id=kw.get("id", uuid4()), code=kw.get("code", "SUP001"), is_active=kw.get("is_active", True),
    )


def _cycle(**kw) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, crop="พริก", variety="พริกขี้หนู", cycle_label="jun2026",
        p_code="Melon-A", lot_no="jun2026-SUP001-Melon-A-001", lot_no_source="auto",
        lot_running_no=1, po_number=None, supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        planting_date=None, plant_count=None, expected_yield_full=None, expected_yield_unit=None,
        updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _create_row(**over) -> dict:
    row = {
        "action": "create_plot_with_cycle", "supplierCode": "SUP001", "plotCode": "",
        "plotName": "แปลงใหม่", "crop": "พริก", "variety": "พริกขี้หนู",
        "cycleLabel": "jun2026", "pCode": "Melon-A", "plantingDate": "2026-06-01",
    }
    row.update(over)
    return row


def _update_row(**over) -> dict:
    row = {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "SUP001-2606-001"}
    row.update(over)
    return row


def _lookups(*, supplier=None, plot=None, active=None):
    supplier = supplier if supplier is not None else _supplier()
    return (
        patch(f"{_M}.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)),
        patch(f"{_M}.plot_repo.get_plot_by_code", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=active)),
    )


async def _preview(rows, *, ctx=None, **lookups):
    a, b, c = _lookups(**lookups)
    with a, b, c:
        return await build_preview(object(), _xlsx(rows), ctx=ctx or _ctx())


# --- 1. a blank-code create row is a real, checked row ----------------------

async def test_a_blank_code_create_row_resolves_its_supplier_and_previews_the_code() -> None:
    lookup = AsyncMock(return_value=_supplier())
    with patch(f"{_M}.supplier_repo.get_supplier_by_code", lookup):
        pv = await build_preview(object(), _xlsx([_create_row()]), ctx=_ctx())
    row = pv.rows[0]
    lookup.assert_awaited_once()
    assert row.status == "valid", row.message
    assert row.proposed_plot_code == "SUP001-2606-###"
    # the Auto Lot preview now has the real supplier code, not a placeholder
    assert row.proposed_lot_no == "jun2026-SUP001-Melon-A-###"


async def test_a_blank_code_create_row_is_scope_checked() -> None:
    """Before round V this row returned before the scope check ran."""
    pv = await _preview([_create_row()], ctx=_ctx(allowed=uuid4()))
    assert pv.rows[0].status == "error"
    assert "นอกขอบเขต" in pv.rows[0].message


async def test_a_blank_code_create_row_for_an_inactive_supplier_is_refused() -> None:
    pv = await _preview([_create_row()], supplier=_supplier(is_active=False))
    assert pv.rows[0].status == "error"
    assert "ปิดใช้งาน" in pv.rows[0].message


async def test_a_blank_code_create_row_commits_instead_of_crashing() -> None:
    """The failure this round found live: preview said valid, commit raised
    AssertionError. Now the plot is created with no code of its own."""
    created_plot = SimpleNamespace(id=uuid4())
    create_plot = AsyncMock(return_value=created_plot)
    a, b, c = _lookups()
    with a, b, c, patch(f"{_M}.plot_repo.create_plot", create_plot), \
         patch(f"{_M}.plot_cycle_repo.create_cycle", AsyncMock(return_value=_cycle())):
        result = await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())
    assert result.created_plots == 1
    payload = create_plot.await_args.args[1]
    assert payload.plot_code is None
    assert create_plot.await_args.kwargs["month_source"] == datetime.date(2026, 6, 1)


# --- 2. a plot code that cannot be generated is a preview error -------------

async def test_an_unusable_supplier_code_is_a_preview_error_not_a_commit_crash() -> None:
    pv = await _preview([_create_row()], supplier=_supplier(code="เจียไต๋"))
    assert pv.rows[0].status == "error"
    assert "ใช้สร้างรหัสแปลงอัตโนมัติไม่ได้" in pv.rows[0].message
    assert "กรอกรหัสแปลงเอง" not in pv.rows[0].message   # that way out is closed

    a, b, c = _lookups(supplier=_supplier(code="เจียไต๋"))
    with a, b, c, patch(f"{_M}.plot_repo.create_plot", AsyncMock()) as create_plot:
        with pytest.raises(ImportHasErrors):
            await commit_import(object(), _xlsx([_create_row()]), ctx=_ctx())
    create_plot.assert_not_awaited()


async def test_a_plot_code_too_long_to_generate_is_a_preview_error() -> None:
    # 42 + "-2606-" + a 4-digit running number is 52 > 50
    pv = await _preview([_create_row()], supplier=_supplier(code="A" * 42))
    assert pv.rows[0].status == "error"
    assert "ยาวเกิน" in pv.rows[0].message


# --- 3. an update writes the plot's own details -----------------------------

async def _commit_update(row: dict, *, active=None):
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    active = active or _cycle()
    update_plot = AsyncMock()
    update_cycle = AsyncMock()
    a, b, c = _lookups(plot=plot, active=active)
    with a, b, c, \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=active)), \
         patch(f"{_M}.plot_cycle_repo.update_cycle", update_cycle), \
         patch(f"{_M}.plot_cycle_repo.sync_plot_mirror_from_cycle", AsyncMock()), \
         patch(f"{_M}.plot_repo.update_plot", update_plot):
        await commit_import(object(), _xlsx([row]), ctx=_ctx())
    return update_plot, update_cycle


async def test_an_update_row_writes_the_plot_details_it_carries() -> None:
    update_plot, _ = await _commit_update(_update_row(
        plotName="แปลงเปลี่ยนชื่อ", village="บ้านใหม่", district="อ.เมือง",
        province="เชียงราย", latitude="19.9", longitude="99.8", rai="0",
    ))
    update_plot.assert_awaited_once()
    written = update_plot.await_args.args[2].model_dump(exclude_unset=True)
    assert written == {
        "name": "แปลงเปลี่ยนชื่อ", "village": "บ้านใหม่", "district": "อ.เมือง",
        "province": "เชียงราย", "latitude": Decimal("19.9"), "longitude": Decimal("99.8"),
        "rai": Decimal("0"),       # zero is a value, not a blank
    }


async def test_a_blank_plot_detail_keeps_the_stored_value() -> None:
    update_plot, _ = await _commit_update(_update_row(village="บ้านใหม่"))
    assert update_plot.await_args.args[2].model_dump(exclude_unset=True) == {"village": "บ้านใหม่"}


async def test_an_update_row_with_no_plot_details_does_not_touch_the_plot() -> None:
    update_plot, update_cycle = await _commit_update(_update_row(plantCount="1500"))
    update_plot.assert_not_awaited()
    update_cycle.assert_awaited_once()


# --- 4. the orange columns are fixed once the cycle exists ------------------
# Round Y — variety left this list because it left the FILE: a row that
# carries one at all is refused by _removed_input_column_errors (see
# test_plot_import_variety_round_y.py), which is a stricter rule than the
# "same value is fine" one the remaining three columns follow.

@pytest.mark.parametrize("column,stored,changed", [
    ("crop", "พริก", "เมล่อน"),
    ("cycleLabel", "jun2026", "jul2026"),
    ("pCode", "Melon-A", "Melon-B"),
])
async def test_changing_a_one_time_column_on_update_is_refused(column, stored, changed) -> None:
    pv = await _preview([_update_row(**{column: changed})],
                        plot=SimpleNamespace(id=uuid4(), is_active=True), active=_cycle())
    row = pv.rows[0]
    assert row.status == "error"
    # names the column and the value the cell has to be
    assert column in row.message and f"'{stored}'" in row.message
    assert "ครั้งเดียว" in row.message


async def test_blank_or_repeated_one_time_columns_are_fine() -> None:
    plot = SimpleNamespace(id=uuid4(), is_active=True)
    blank = await _preview([_update_row()], plot=plot, active=_cycle())
    same = await _preview(
        [_update_row(crop=" พริก ", variety="พริกขี้หนู", cycleLabel=" jun2026 ", pCode="Melon-A")],
        plot=plot, active=_cycle(),
    )
    assert blank.rows[0].status == "valid", blank.rows[0].message
    assert same.rows[0].status == "valid", same.rows[0].message


async def test_an_update_never_writes_a_one_time_field() -> None:
    _, update_cycle = await _commit_update(
        _update_row(crop="พริก", variety="พริกขี้หนู", cycleLabel="jun2026", pCode="Melon-A",
                    plantCount="1500"),
    )
    fields = update_cycle.await_args.args[2]
    assert not ({"crop", "variety", "cycle_label", "p_code"} & set(fields))
    assert fields["plant_count"] == 1500


async def test_update_cycle_ignores_one_time_fields_even_when_handed_them() -> None:
    cycle = SimpleNamespace(crop="พริก", variety="พริกขี้หนู", cycle_label="jun2026",
                            p_code="Melon-A", plant_count=None)
    db = MagicMock()
    db.flush = AsyncMock()
    await cycle_repo.update_cycle(db, cycle, {
        "crop": "X", "variety": "Y", "cycle_label": "Z", "p_code": "Q", "plant_count": 7,
    })
    assert (cycle.crop, cycle.variety, cycle.cycle_label, cycle.p_code) == (
        "พริก", "พริกขี้หนู", "jun2026", "Melon-A")
    assert cycle.plant_count == 7


def test_changed_one_time_fields_compares_what_would_be_stored() -> None:
    cycle = SimpleNamespace(crop="พริก", variety=None, cycle_label="jun2026", p_code="Melon-A")
    assert cycle_repo.changed_one_time_fields(cycle, {}) == []                       # absent
    assert cycle_repo.changed_one_time_fields(cycle, {"cycle_label": " jun2026 "}) == []
    assert cycle_repo.changed_one_time_fields(cycle, {"variety": None}) == []        # None == None
    assert cycle_repo.changed_one_time_fields(cycle, {"cycle_label": ""}) == ["cycle_label"]
    assert cycle_repo.changed_one_time_fields(cycle, {"p_code": "melon-a"}) == ["p_code"]  # case kept


async def test_the_web_edit_refuses_the_same_change_in_thai() -> None:
    plot = SimpleNamespace(id=uuid4())
    cycle = _cycle(status="active")
    update_cycle = AsyncMock()
    with patch("app.api.v1.plots.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)), \
         patch("app.api.v1.plots.plot_cycle_repo.update_cycle", update_cycle):
        with pytest.raises(HTTPException) as exc:
            await plots_api.update_plot_cycle(
                plot_id=plot.id, cycle_id=cycle.id,
                payload=PlotCycleUpdate(crop="เมล่อน", pCode="Melon-B"), db=MagicMock(),
            )
    assert exc.value.status_code == 422
    assert "ชนิดพืช" in exc.value.detail and "P.Code" in exc.value.detail
    update_cycle.assert_not_awaited()


# --- the template's colours agree with the rules ----------------------------

def test_the_orange_columns_are_exactly_the_ones_the_importer_fixes() -> None:
    """If a column is painted orange the importer must refuse a change to it,
    and the other way round — the colour is only worth anything if it's true."""
    orange = {c for c, k in plots_api._TEMPLATE_COLUMN_KIND.items() if k == plots_api._KIND_ONE_TIME}
    # supplierCode is orange too: chosen on create, and on an existing row it
    # addresses the plot (a different code is a different — or no — plot).
    assert orange - {"supplierCode"} == set(plot_import._ONE_TIME_FIELD_COLUMNS.values())


def test_gray_columns_are_never_read_back() -> None:
    """A gray cell is the system's: whatever a user types there changes
    nothing. systemLotNo in particular must NOT trip the "a filled lotNo cell is
    refused" rule round A set for the retired input column."""
    raw = {"action": "update_current_cycle", "supplierCode": "SUP001", "plotCode": "X",
           "supplierName": "ใครก็ได้", "systemLotNo": "SOMETHING-001",
           "currentPlotStatus": "ปิดใช้งาน", "inspectionPasswordStatus": "configured"}
    parsed, errors = plot_import._parse_row(raw, frozenset(raw))
    assert errors == []
    dumped = vars(parsed)
    assert "SOMETHING-001" not in dumped.values() and "ใครก็ได้" not in dumped.values()


def test_the_legend_names_every_column_once_under_its_colour() -> None:
    legend = plots_api._legend_sheet()
    colour_rows = [r for r in legend if r and hasattr(r[0], "style")
                   and r[0].style in plots_api._KIND_HEADER_STYLE.values()]
    assert len(colour_rows) == 4
    listed = [name for r in colour_rows for name in r[2].split(", ")]
    assert sorted(listed) == sorted(plots_api._column_title(c) for c in IMPORT_COLUMNS)


def test_the_blank_template_paints_rows_the_importer_skips() -> None:
    content = plots_api._plot_template_workbook([SimpleNamespace(code="SUP001")])
    headers, rows = read_first_sheet(content)
    assert headers == IMPORT_COLUMNS
    assert [n for n, _ in rows] == [2]          # only the description row has values
    sheet1 = zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml").decode()
    assert sheet1.count("<row ") == 2 + plots_api._BLANK_INPUT_ROWS


def test_an_existing_plot_row_shows_whose_plot_and_its_system_lot() -> None:
    plot = SimpleNamespace(
        supplier=SimpleNamespace(code="SUP001", name="ไร่ตัวอย่าง"), plot_code="SUP001-2606-001",
        name="แปลง", village=None, district=None, province=None, latitude=None,
        longitude=None, rai=None, access_phones=[], active_cycle=_cycle(),
    )
    values = plots_api._update_cycle_row_values(plot)
    assert values["supplierName"] == "ไร่ตัวอย่าง"
    assert values["systemLotNo"] == "jun2026-SUP001-Melon-A-001"
