"""GET /plots/import-template query-filter contract (round 8-6A Part A/H).

Calls the route function directly with mocks, same style as
test_plot_import_endpoint.py / test_plot_cycle_rollover.py. The generic
(no-filter) path must stay byte-for-byte the pre-8-6A behavior; the
contextual (filtered) path is new this round. Never touches a real DB —
repo.list_plots / get_supplier_scope_filter / the workbook builders are all
mocked, so these tests assert wiring/contract, not query correctness (that's
plot_repository's own test coverage, unchanged this round).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import download_plot_import_template

_P = "app.api.v1.plots"


def _user(*, roles: list[str] = ("internal:admin",), supplier_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name=r) for r in roles],
        supplier_id=supplier_id,
        is_supplier_admin=False,
    )


def _db_with_supplier(supplier) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=supplier)
    db.execute = AsyncMock(return_value=result)
    # Round 8-27E — _count_excluded_plots reads db.scalar(); tests that don't
    # patch it out otherwise await a bare MagicMock. 0 = nothing excluded,
    # which is the uninteresting default for every test in this module (the
    # count's own behaviour lives in
    # test_plot_import_template_excluded_and_all_suppliers.py).
    db.scalar = AsyncMock(return_value=0)
    return db


def _fake_supplier(id_=None):
    return SimpleNamespace(id=id_ or uuid4(), code="SUP001", name="Supplier One", is_active=True)


# --- no filter -> unchanged generic template --------------------------------

async def test_no_query_params_returns_generic_template() -> None:
    with patch(f"{_P}._template_suppliers", AsyncMock(return_value=[])) as mk_suppliers, \
         patch(f"{_P}._plot_template_workbook", MagicMock(return_value=b"GENERIC")) as mk_generic, \
         patch(f"{_P}.repo.list_plots", AsyncMock()) as mk_list, \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock()) as mk_contextual:
        resp = await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
        )
    assert resp.body == b"GENERIC"
    mk_suppliers.assert_awaited_once()
    mk_generic.assert_called_once()
    mk_list.assert_not_awaited()
    mk_contextual.assert_not_called()


async def test_generic_response_has_no_store_cache_control() -> None:
    with patch(f"{_P}._template_suppliers", AsyncMock(return_value=[])), \
         patch(f"{_P}._plot_template_workbook", MagicMock(return_value=b"GENERIC")):
        resp = await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
        )
    assert resp.headers["cache-control"] == "no-store"
    assert resp.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- contextual filter requires supplier_id ---------------------------------

async def test_contextual_filter_without_supplier_id_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province="เชียงใหม่", crop=None, variety=None, q=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง"


@pytest.mark.parametrize("field,value", [
    ("crop", "พริก"), ("variety", "พริกขี้หนู"), ("q", "P00"),
])
async def test_any_single_contextual_filter_without_supplier_id_is_422(field, value) -> None:
    kwargs = {"supplier_id": None, "province": None, "crop": None, "variety": None, "q": None}
    kwargs[field] = value
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(current_user=_user(), db=MagicMock(), **kwargs)
    assert exc.value.status_code == 422


async def test_supplier_id_alone_is_a_valid_contextual_request() -> None:
    """supplier_id with no other filter is still contextual mode (not the
    generic no-filter path) — round 8-6A Part A item 2."""
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])) as mk_list, \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        resp = await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
        )
    assert resp.body == b"CTX"
    mk_list.assert_awaited_once()


# --- out-of-scope / missing supplier -> generic 404 -------------------------

async def test_supplier_not_found_or_out_of_scope_is_generic_404() -> None:
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc:
            await download_plot_import_template(
                current_user=_user(roles=["supplier:owner"], supplier_id=uuid4()),
                db=_db_with_supplier(None),  # query finds nothing, either reason
                supplier_id=sid, province=None, crop=None, variety=None, q=None,
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "ไม่พบ Supplier"


# --- filters forwarded to list_plots with the SAME semantics as list_plots -

async def test_filters_forwarded_to_list_plots_with_active_only_and_no_paging() -> None:
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])) as mk_list, \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province="เชียงใหม่", crop="พริก", variety="พริกขี้หนู", q="P00",
        )
    mk_list.assert_awaited_once()
    kwargs = mk_list.call_args.kwargs
    assert kwargs["supplier_id"] == sid
    assert kwargs["province"] == "เชียงใหม่"
    assert kwargs["crop"] == "พริก"
    assert kwargs["variety"] == "พริกขี้หนู"
    assert kwargs["q"] == "P00"
    # Round 8-6J — plot_status (default "all") replaces the old hardcoded
    # active_only=True; a caller that doesn't pass plot_status now gets BOTH
    # active and inactive plots in Sheet 1 (see test_plot_status_forwarded_*
    # below for the explicit active/inactive/all cases).
    assert kwargs["plot_status"] == "all"
    assert kwargs["offset"] == 0
    # Not the frontend's page size — every match, capped only for overflow
    # detection (Part A items 7/8).
    assert kwargs["limit"] == 5001


@pytest.mark.parametrize("status", ["all", "active", "inactive"])
async def test_plot_status_forwarded_to_list_plots_for_filtered_template(status) -> None:
    """Round 8-6J Part B/K item 1-3 — the filtered (single-Supplier)
    template forwards whichever plotStatus the caller asked for verbatim."""
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])) as mk_list, \
         patch(f"{_P}._count_excluded_plots", AsyncMock(return_value=0)), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
            plot_status=status,
        )
    assert mk_list.call_args.kwargs["plot_status"] == status


# --- 5,000-row cap: 422, never silently truncated ---------------------------

async def test_over_5000_matching_plots_is_422_not_truncated() -> None:
    sid = uuid4()
    too_many = [MagicMock() for _ in range(5001)]
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=too_many)), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock()) as mk_build:
        with pytest.raises(HTTPException) as exc:
            await download_plot_import_template(
                current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
                supplier_id=sid, province=None, crop=None, variety=None, q=None,
            )
    assert exc.value.status_code == 422
    assert "5,000" in exc.value.detail
    mk_build.assert_not_called()


async def test_exactly_5000_matching_plots_is_allowed() -> None:
    sid = uuid4()
    exactly_cap = [MagicMock() for _ in range(5000)]
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=exactly_cap)), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        resp = await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
        )
    assert resp.body == b"CTX"


# --- no matching active plot -> 422, no empty workbook ----------------------

async def test_no_matching_active_plot_is_422() -> None:
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[])), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock()) as mk_build:
        with pytest.raises(HTTPException) as exc:
            await download_plot_import_template(
                current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
                supplier_id=sid, province="จังหวัดที่ไม่มีแปลง", crop=None, variety=None, q=None,
            )
    assert exc.value.status_code == 422
    assert "ไม่พบแปลงตรงตามตัวกรอง" in exc.value.detail
    mk_build.assert_not_called()


async def test_contextual_response_has_no_store_cache_control() -> None:
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        resp = await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
        )
    assert resp.headers["cache-control"] == "no-store"


# --- route wiring: plots.read + RLS context (source-level, like the
#     existing test_rollover_route_requires_plots_update_and_rls_context) ----

def test_route_requires_plots_read_and_rls_context() -> None:
    src = inspect.getsource(plots_module)
    marker = '@router.get("/import-template"'
    start = src.index(marker)
    block = src[start:start + 400]
    assert "require_permission(PermissionKey.PLOTS_READ)" in block
    assert "get_rls_context" in block
