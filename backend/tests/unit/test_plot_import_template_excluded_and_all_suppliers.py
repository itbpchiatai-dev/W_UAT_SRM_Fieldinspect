"""Round 8-6G — excluded-plot reporting + template_mode=all_suppliers.

Round 8-27E deleted the "รายการที่ไม่รวม" sheet: under the default
plot_status it only ever reported "this plot's Supplier is deactivated", and
a sheet is a poor place to tell someone that part of what they asked for is
missing. The endpoint returns the COUNT on the X-Excluded-Plot-Count header
now and the Plots page shows it on screen. The tests that rendered that
sheet went with it; what survives is the query contract (which plots count
as excluded, and that the filters reaching it still match Sheet 1's) plus
all of the all_suppliers authorization/limit coverage, untouched. No real DB: workbook builders take plain Plot/Supplier-shaped
SimpleNamespace fixtures (same style as test_plot_import_template_
contextual.py); endpoint-level tests call download_plot_import_template
directly with mocked db/current_user (same style as
test_plot_import_template_endpoint.py) — never a real DB, per this round's
Part G instruction.
"""
from __future__ import annotations

import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import (
    _EXCLUDED_COUNT_HEADER,
    _SHEET_EXAMPLES,
    _SHEET_NEW_CYCLE,
    _contextual_plot_template_workbook,
    _count_excluded_plots,
    download_plot_import_template,
)
from app.services.excel_reader import read_first_sheet

_P = "app.api.v1.plots"


def _user(*, roles: list[str], supplier_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), roles=[SimpleNamespace(name=r) for r in roles],
        supplier_id=supplier_id, is_supplier_admin=False,
    )


def _supplier(code: str = "SUP001", name: str = "Supplier One", is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(code=code, name=name, is_active=is_active)


def _cycle(**over) -> SimpleNamespace:
    base = dict(
        id=uuid4(), cycle_no=1, status="active", cycle_label="jun2026",
        crop="พริก", variety="พริกขี้หนู",
        po_number="PO25001", p_code="Melon-A",
        supplier_lot_no=None,
        oracle_supplier_code=None, oracle_invoice=None, ref_account=None,
        lot_no="LOT-01", planting_date=None, plant_count=1000,
        expected_yield_full=None, expected_yield_unit="kg",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _plot(**over) -> SimpleNamespace:
    base = dict(
        # round 8-9B.1 — the credential-status map is keyed by plot id
        id=uuid4(),
        plot_code="P001", name="แปลงหนึ่ง", is_active=True, province="เชียงใหม่",
        village=None, district=None, latitude=None, longitude=None, rai=None,
        supplier=_supplier(), active_cycle=_cycle(), cycles=[_cycle()],
        access_phones=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def _unzip(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content)) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def _fake_supplier_lookup_db(supplier) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=supplier)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    return db


def test_count_excluded_plots_never_accepts_crop_or_variety_params() -> None:
    """crop/variety filter on active-cycle data, which an excluded plot
    (almost always with no active cycle) cannot meaningfully "match".
    Confirmed at the signature level so a future edit can't quietly
    reintroduce it."""
    sig = inspect.signature(_count_excluded_plots)
    assert "crop" not in sig.parameters
    assert "variety" not in sig.parameters


# --- item 2 (continued): SUP010-shaped fixture — Sheet 1 vs excluded --------

def test_sheet_one_contains_only_the_plots_it_was_given() -> None:
    """A SUP010-shaped fixture. An excluded plot can no longer leak into the
    importable sheet for the strongest possible reason: the builder never
    receives one at all."""
    active_plots = [_plot(plot_code=f"P{i:03d}") for i in range(1, 11) if i != 2]
    assert len(active_plots) == 9

    _headers, rows = read_first_sheet(_contextual_plot_template_workbook(active_plots))
    data_rows = [v for n, v in rows if n > 2]
    assert len(data_rows) == 9
    assert all(v.get("plotCode") != "P002" for v in data_rows)


# --- item 3: all-suppliers mode aggregates active plots across suppliers ---

async def test_all_suppliers_mode_aggregates_active_plots_across_suppliers() -> None:
    plots = [
        _plot(plot_code="P001", supplier=_supplier(code="SUP001")),
        _plot(plot_code="P001", supplier=_supplier(code="SUP002")),
    ]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=plots)) as mk_list, \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
\
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")):
        resp = await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert resp.body == b"ALL"
    mk_list.assert_awaited_once()


async def test_all_suppliers_mode_excluded_plots_are_reported_not_rendered() -> None:
    """Round 8-27E — excluded plots never reach the workbook builder now;
    only their COUNT leaves the endpoint, on a response header."""
    active = [_plot(plot_code="P001")]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=active)), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=3)) as mk_excluded, \
         patch(f"{_P}.plot_cycle_repo.get_latest_active_records_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")) as mk_build:
        resp = await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    mk_build.assert_called_once_with(
        active, latest_cycles={},
        # Round 8-10B — latest_active_records is gone: it only ever fed the
        # retired finalInspectionRecordId column, so a template download no
        # longer queries for it at all.
        credential_status={},   # round 8-9B.1 — batch-loaded, never per plot
    )
    assert mk_excluded.call_args.kwargs["supplier_id"] is None
    assert resp.headers[_EXCLUDED_COUNT_HEADER] == "3"


async def test_the_excluded_count_header_is_always_present_even_at_zero() -> None:
    """A client must never have to tell "nothing excluded" apart from "old
    server that doesn't send the header"."""
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")):
        resp = await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert resp.headers[_EXCLUDED_COUNT_HEADER] == "0"


async def test_the_blank_template_also_reports_a_zero_excluded_count() -> None:
    with patch(f"{_P}._template_suppliers", AsyncMock(return_value=[])):
        resp = await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
        )
    assert resp.headers[_EXCLUDED_COUNT_HEADER] == "0"


# --- items 4/5: non-'all' scope callers get 403, never a scoped result -----

async def test_supplier_owner_calling_all_suppliers_mode_is_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["supplier:owner"], supplier_id=uuid4()), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert exc.value.status_code == 403


async def test_field_officer_calling_all_suppliers_mode_is_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["farmlog:field_officer"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert exc.value.status_code == 403


async def test_supplier_staff_calling_all_suppliers_mode_is_403() -> None:
    """supplier:staff resolves to scope 'assigned' (app/api/deps/scope.py),
    not 'supplier' — same 403 either way."""
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["supplier:staff"], supplier_id=uuid4()), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert exc.value.status_code == 403


# --- round 8-6J item 5: all_suppliers mode forwards plot_status ------------

async def test_all_suppliers_mode_forwards_plot_status_to_list_and_excluded() -> None:
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=[_plot()])) as mk_list, \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)) as mk_excluded, \
         patch(f"{_P}.plot_cycle_repo.get_latest_active_records_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")):
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers", plot_status="inactive",
        )
    assert mk_list.call_args.kwargs["plot_status"] == "inactive"
    assert mk_excluded.call_args.kwargs["plot_status"] == "inactive"


def test_all_suppliers_authorization_reuses_resolve_scope_helper_not_a_new_check() -> None:
    """Part B/A — must reuse _resolve_scope (the same helper get_rls_context/
    get_supplier_scope_filter use), never a separate role/scope decision
    invented for this one endpoint."""
    src = inspect.getsource(download_plot_import_template)
    assert "_resolve_scope(current_user, _role_names(current_user))" in src


# --- items 6/7: all_suppliers + any filter -> 422, never silently applied --

async def test_all_suppliers_with_supplier_id_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=uuid4(), province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert exc.value.status_code == 422


@pytest.mark.parametrize("field,value", [
    ("province", "เชียงใหม่"), ("crop", "พริก"), ("variety", "พริกขี้หนู"), ("q", "P00"),
])
async def test_all_suppliers_with_secondary_filter_is_422(field, value) -> None:
    kwargs = {"supplier_id": None, "province": None, "crop": None, "variety": None, "q": None}
    kwargs[field] = value
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            template_mode="all_suppliers", **kwargs,
        )
    assert exc.value.status_code == 422


async def test_unrecognized_template_mode_value_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="bogus_mode",
        )
    assert exc.value.status_code == 422


# --- item 8: deterministic ordering — supplierCode/plotCode, never UUID ----

def test_all_suppliers_query_orders_by_supplier_code_then_plot_code_not_uuid() -> None:
    src = inspect.getsource(plots_module._list_active_plots_all_suppliers)
    assert ".order_by(Supplier.code.asc(), Plot.plot_code.asc())" in src
    assert "Plot.supplier_id.asc()" not in src


# --- item 9: >5,000 actionable plots (all_suppliers) -> 422, no truncate ---

async def test_all_suppliers_over_5000_actionable_plots_is_422_not_truncated() -> None:
    too_many = [MagicMock() for _ in range(5001)]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=too_many)), \
         patch(f"{_P}._contextual_plot_template_workbook") as mk_build:
        with pytest.raises(HTTPException) as exc:
            await download_plot_import_template(
                current_user=_user(roles=["internal:admin"]), db=MagicMock(),
                supplier_id=None, province=None, crop=None, variety=None, q=None,
                template_mode="all_suppliers",
            )
    assert exc.value.status_code == 422
    assert "5,000" in exc.value.detail
    mk_build.assert_not_called()


async def test_all_suppliers_exactly_5000_actionable_plots_is_allowed() -> None:
    exactly_cap = [MagicMock() for _ in range(5000)]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=exactly_cap)), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
         patch(f"{_P}.plot_cycle_repo.get_latest_active_records_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")):
        resp = await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    assert resp.body == b"ALL"


async def test_all_suppliers_no_active_plots_is_422() -> None:
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=[])), \
         patch(f"{_P}._contextual_plot_template_workbook") as mk_build:
        with pytest.raises(HTTPException) as exc:
            await download_plot_import_template(
                current_user=_user(roles=["internal:admin"]), db=MagicMock(),
                supplier_id=None, province=None, crop=None, variety=None, q=None,
                template_mode="all_suppliers",
            )
    assert exc.value.status_code == 422
    mk_build.assert_not_called()


# --- item 10: N+1 / eager-load contract (source-level, established
#     codebase pattern — see test_plot_repository_loading.py) ---------------

def test_all_suppliers_query_eager_loads_supplier_active_cycle_access_phones() -> None:
    src = inspect.getsource(plots_module._list_active_plots_all_suppliers)
    assert "selectinload(Plot.supplier)" in src
    assert "selectinload(Plot.active_cycle)" in src
    assert "selectinload(Plot.access_phones)" in src


def test_excluded_plots_query_is_a_count_with_no_eager_loads() -> None:
    """Round 8-27E — nothing renders these rows any more, so loading whole
    Plot objects (with supplier + every cycle) only to discard them would be
    pure waste. It counts instead, and is uncapped: the old 5,000 cap existed
    for the size of the rendered sheet and could have under-reported."""
    src = inspect.getsource(plots_module._count_excluded_plots)
    assert "select(func.count())" in src
    assert "selectinload" not in src
    assert "limit" not in src


# --- item 11: download path never flushes/commits/mutates ------------------

async def test_all_suppliers_download_never_commits_or_flushes() -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
         patch(f"{_P}.plot_cycle_repo.get_latest_active_records_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")):
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=db,
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    db.commit.assert_not_called()
    db.flush.assert_not_called()


async def test_filtered_supplier_download_never_commits_or_flushes() -> None:
    sid = uuid4()
    db = _fake_supplier_lookup_db(_supplier())
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=db,
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
        )
    db.commit.assert_not_called()
    db.flush.assert_not_called()


# --- filtered-Supplier path wires _fetch_excluded_plots with matching
#     filters (province/q), and deliberately NEVER crop/variety -------------

async def test_filtered_supplier_path_counts_excluded_plots_with_matching_filters() -> None:
    sid = uuid4()
    db = _fake_supplier_lookup_db(_supplier())
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)) as mk_excluded, \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=db,
            supplier_id=sid, province="เชียงใหม่", crop="พริก", variety="พริกขี้หนู", q="P00",
        )
    mk_excluded.assert_awaited_once()
    kwargs = mk_excluded.call_args.kwargs
    assert kwargs["supplier_id"] == sid
    assert kwargs["province"] == "เชียงใหม่"
    assert kwargs["q"] == "P00"
    assert "crop" not in kwargs
    assert "variety" not in kwargs


# --- items 12/13/14: sheet order, importer reads only sheet1, excluded/
#     example plot codes never leak into the importable sheet --------------

def test_all_suppliers_workbook_sheet_order_still_puts_new_cycle_first() -> None:
    content = _contextual_plot_template_workbook([_plot()])
    parts = _unzip(content)
    workbook = parts["xl/workbook.xml"]
    assert workbook.index(_SHEET_NEW_CYCLE) < workbook.index(_SHEET_EXAMPLES)


def test_importer_never_sees_example_plot_codes() -> None:
    content = _contextual_plot_template_workbook([_plot(plot_code="P001")])
    _headers, rows = read_first_sheet(content)
    plot_codes = {v.get("plotCode") for n, v in rows if n > 2 and "plotCode" in v}
    assert plot_codes == {"P001"}
    assert "P101" not in plot_codes  # example plot code — never real data
