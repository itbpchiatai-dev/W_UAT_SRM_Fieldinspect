"""Round 8-6G — "รายการที่ไม่รวม" (excluded-plot) sheet + template_mode=
all_suppliers. No real DB: workbook builders take plain Plot/Supplier-shaped
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
    _EXCLUDED_HEADERS,
    _SHEET_EXAMPLES,
    _SHEET_EXCLUDED,
    _SHEET_NEW_CYCLE,
    _contextual_plot_template_workbook,
    _excluded_row_values,
    _fetch_excluded_plots,
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


# --- item 2: excluded-row reason/label mapping ------------------------------

def test_excluded_plot_shows_plot_inactive_reason() -> None:
    plot = _plot(
        plot_code="P002", is_active=False,
        cycles=[_cycle(cycle_no=1, status="cancelled", cycle_label=None)],
    )
    values = _excluded_row_values(plot)
    assert values["plotCode"] == "P002"
    assert values["plotStatus"] == "ปิดใช้งาน"
    assert values["cycleStatus"] == "ยกเลิก"
    assert values["cycleLabel"] is None
    assert values["exclusionReason"] == "แปลงปิดใช้งาน ไม่สามารถเริ่มรอบปลูกใหม่ได้"


def test_excluded_plot_under_inactive_supplier_shows_supplier_reason() -> None:
    plot = _plot(is_active=True, supplier=_supplier(is_active=False))
    values = _excluded_row_values(plot)
    assert values["exclusionReason"] == "Supplier ปิดใช้งาน"
    # The PLOT itself is active; it's the Supplier that's inactive.
    assert values["plotStatus"] == "ใช้งานอยู่"


def test_excluded_plot_with_no_cycle_history_shows_no_open_cycle_label() -> None:
    plot = _plot(is_active=False, cycles=[])
    values = _excluded_row_values(plot)
    assert values["cycleStatus"] == "ไม่มีรอบที่เปิดอยู่"
    assert values["cycleLabel"] is None


def test_excluded_row_picks_the_latest_cycle_by_cycle_no() -> None:
    plot = _plot(is_active=False, cycles=[
        _cycle(cycle_no=1, status="harvested", cycle_label="old2025"),
        _cycle(cycle_no=2, status="cancelled", cycle_label="latest2026"),
    ])
    values = _excluded_row_values(plot)
    assert values["cycleStatus"] == "ยกเลิก"
    assert values["cycleLabel"] == "latest2026"


def test_excluded_row_never_exposes_internal_id_or_secret_fields() -> None:
    plot = _plot(is_active=False)
    plot.id = uuid4()
    plot.qr_key = "should-never-appear"
    values = _excluded_row_values(plot)
    assert set(values.keys()) == set(_EXCLUDED_HEADERS)
    assert str(plot.id) not in values.values()
    assert "should-never-appear" not in values.values()
    assert "action" not in _EXCLUDED_HEADERS


def test_fetch_excluded_plots_never_accepts_crop_or_variety_params() -> None:
    """Part C — crop/variety filter on active-cycle data, which an excluded
    plot (almost always with no active cycle) cannot meaningfully "match".
    Confirmed at the signature level so a future edit can't quietly
    reintroduce it."""
    sig = inspect.signature(_fetch_excluded_plots)
    assert "crop" not in sig.parameters
    assert "variety" not in sig.parameters


# --- item 2 (continued): SUP010-shaped fixture — Sheet 1 vs excluded --------

def test_sheet_one_and_excluded_sheet_partition_active_and_inactive_plots() -> None:
    """A SUP010-shaped fixture: 9 active plots + 1 inactive (P002) — Sheet 1
    must contain exactly the 9 active plots, never P002; the excluded sheet
    must contain exactly P002 with a clear, human-readable reason (Acceptance
    criteria)."""
    active_plots = [_plot(plot_code=f"P{i:03d}") for i in range(1, 11) if i != 2]
    assert len(active_plots) == 9
    excluded = [_plot(
        plot_code="P002", is_active=False,
        cycles=[_cycle(cycle_no=1, status="cancelled", cycle_label=None)],
    )]

    content = _contextual_plot_template_workbook(active_plots, excluded)
    _headers, rows = read_first_sheet(content)
    data_rows = [v for n, v in rows if n > 2]
    assert len(data_rows) == 9
    assert all(v.get("plotCode") != "P002" for v in data_rows)

    parts = _unzip(content)
    sheet3 = parts["xl/worksheets/sheet3.xml"]
    assert "P002" in sheet3
    assert "แปลงปิดใช้งาน" in sheet3


def test_excluded_sheet_has_the_required_notice_and_no_action_column() -> None:
    content = _contextual_plot_template_workbook([_plot()], [_plot(plot_code="P002", is_active=False)])
    parts = _unzip(content)
    sheet3 = parts["xl/worksheets/sheet3.xml"]
    assert "ชีตนี้เป็นข้อมูลสำหรับตรวจสอบ ระบบจะไม่นำเข้าข้อมูลจากชีตนี้" in sheet3
    assert "action" not in _EXCLUDED_HEADERS


def test_excluded_sheet_is_empty_but_present_when_nothing_was_excluded() -> None:
    content = _contextual_plot_template_workbook([_plot()], None)
    parts = _unzip(content)
    workbook = parts["xl/workbook.xml"]
    assert _SHEET_EXCLUDED in workbook


# --- item 3: all-suppliers mode aggregates active plots across suppliers ---

async def test_all_suppliers_mode_aggregates_active_plots_across_suppliers() -> None:
    plots = [
        _plot(plot_code="P001", supplier=_supplier(code="SUP001")),
        _plot(plot_code="P001", supplier=_supplier(code="SUP002")),
    ]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=plots)) as mk_list, \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])), \
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


async def test_all_suppliers_mode_inactive_plots_go_to_excluded_not_sheet_one() -> None:
    """Wiring test: whatever _list_active_plots_all_suppliers/
    _fetch_excluded_plots (both mocked here) return becomes Sheet 1 / the
    excluded sheet respectively — the real partitioning logic (plotStatus-
    aware since round 8-6J) is these two functions' own responsibility,
    covered by their dedicated tests elsewhere."""
    active = [_plot(plot_code="P001")]
    excluded = [_plot(plot_code="P002", is_active=False)]
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=active)), \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=excluded)) as mk_excluded, \
         patch(f"{_P}.plot_cycle_repo.get_latest_active_records_for_cycles", AsyncMock(return_value={})), \
         patch(f"{_P}.credential_repo.get_credential_status_for_plots", AsyncMock(return_value={})), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"ALL")) as mk_build:
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers",
        )
    mk_build.assert_called_once_with(
        active, excluded, latest_cycles={}, plot_status="all",
        # Round 8-10B — latest_active_records is gone: it only ever fed the
        # retired finalInspectionRecordId column, so a template download no
        # longer queries for it at all.
        credential_status={},   # round 8-9B.1 — batch-loaded, never per plot
    )
    assert mk_excluded.call_args.kwargs["supplier_id"] is None


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
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])) as mk_excluded, \
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


def test_excluded_plots_query_orders_by_supplier_code_then_plot_code_not_uuid() -> None:
    src = inspect.getsource(plots_module._fetch_excluded_plots)
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
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])), \
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


def test_excluded_plots_query_eager_loads_supplier_and_cycles() -> None:
    src = inspect.getsource(plots_module._fetch_excluded_plots)
    assert "selectinload(Plot.supplier)" in src
    assert "selectinload(Plot.cycles)" in src


# --- item 11: download path never flushes/commits/mutates ------------------

async def test_all_suppliers_download_never_commits_or_flushes() -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    with patch(f"{_P}._list_active_plots_all_suppliers", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])), \
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
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(roles=["internal:admin"]), db=db,
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
        )
    db.commit.assert_not_called()
    db.flush.assert_not_called()


# --- filtered-Supplier path wires _fetch_excluded_plots with matching
#     filters (province/q), and deliberately NEVER crop/variety -------------

async def test_filtered_supplier_path_fetches_excluded_plots_with_matching_filters() -> None:
    sid = uuid4()
    db = _fake_supplier_lookup_db(_supplier())
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[_plot()])), \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])) as mk_excluded, \
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
    content = _contextual_plot_template_workbook([_plot()], [])
    parts = _unzip(content)
    workbook = parts["xl/workbook.xml"]
    assert workbook.index(_SHEET_NEW_CYCLE) < workbook.index(_SHEET_EXCLUDED) < workbook.index(_SHEET_EXAMPLES)


def test_importer_never_sees_excluded_or_example_plot_codes() -> None:
    active = [_plot(plot_code="P001")]
    excluded = [_plot(plot_code="P999", is_active=False)]
    content = _contextual_plot_template_workbook(active, excluded)
    _headers, rows = read_first_sheet(content)
    plot_codes = {v.get("plotCode") for n, v in rows if n > 2 and "plotCode" in v}
    assert plot_codes == {"P001"}
    assert "P999" not in plot_codes
    assert "P101" not in plot_codes  # example plot code — never real data
