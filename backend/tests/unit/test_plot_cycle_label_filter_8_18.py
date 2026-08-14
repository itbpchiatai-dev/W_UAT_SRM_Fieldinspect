""""รอบปลูกปัจจุบัน" filter (round 8-18) — GET /plots, POST /plots/search-by-
phone, GET /plots/import-template, and the new GET /plots/cycle-labels.

Same two-layer split as test_plot_phone_search_8_17a2.py / test_plot_status_
filter.py: repository tests compile the SQLAlchemy statement (literal
binds) to prove the EXISTS-against-active-cycle shape without a real
database; endpoint tests call the route functions directly and patch the
repository (RLS/permission wiring verified structurally, not by triggering
FastAPI's DI).
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import (
    download_plot_import_template,
    list_plot_cycle_labels,
    list_plots,
)
from app.repositories.plot_repository import (
    list_plot_cycle_labels as repo_list_plot_cycle_labels,
)
from app.repositories.plot_repository import list_plots as repo_list_plots
from app.repositories.plot_repository import (
    search_plots_by_phone as repo_search_plots_by_phone,
)

_P = "app.api.v1.plots"


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


def _capturing_db(rows=None):
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(rows or [])

    return SimpleNamespace(execute=_execute), captured


# --- repository: list_plots — EXISTS against the ACTIVE cycle only ----------

async def test_list_plots_cycle_label_filter_is_exists_against_active_cycle_only():
    db, captured = _capturing_db()
    await repo_list_plots(db, cycle_label="jun2026")
    sql = _compiled(captured["stmt"])
    assert "EXISTS" in sql
    assert "plot_cycles" in sql
    assert "status = 'active'" in sql
    assert "cycle_label = 'jun2026'" in sql


async def test_list_plots_cycle_label_never_a_join_no_dedup_needed():
    """EXISTS, not a JOIN — a JOIN against plot_cycles would risk duplicate
    Plot rows and need an extra DISTINCT; EXISTS never does."""
    db, captured = _capturing_db()
    await repo_list_plots(db, cycle_label="jun2026")
    sql = _compiled(captured["stmt"])
    assert "JOIN plot_cycles" not in sql


async def test_list_plots_cycle_label_trimmed_before_matching():
    db, captured = _capturing_db()
    await repo_list_plots(db, cycle_label="  jun2026  ")
    sql = _compiled(captured["stmt"])
    assert "cycle_label = 'jun2026'" in sql
    assert "jun2026  " not in sql


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_list_plots_blank_or_none_cycle_label_is_a_no_op(blank):
    db, captured = _capturing_db()
    await repo_list_plots(db, cycle_label=blank)
    sql = _compiled(captured["stmt"])
    assert "plot_cycles" not in sql


async def test_list_plots_cycle_label_combines_with_other_filters():
    db, captured = _capturing_db()
    await repo_list_plots(db, supplier_id=uuid4(), crop="พริก", cycle_label="jun2026")
    sql = _compiled(captured["stmt"])
    assert "cycle_label = 'jun2026'" in sql
    assert "current_crop" in sql


# --- repository: search_plots_by_phone — same filter, same semantics -------

async def test_search_plots_by_phone_cycle_label_filter_is_exists_against_active_cycle():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", cycle_label="jun2026")
    sql = _compiled(captured["stmt"])
    assert "status = 'active'" in sql
    assert "cycle_label = 'jun2026'" in sql


async def test_search_plots_by_phone_no_cycle_label_is_a_no_op():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    sql = _compiled(captured["stmt"])
    assert "cycle_label" not in sql


# --- repository: list_plot_cycle_labels — distinct, active-only, sorted ----

async def test_list_plot_cycle_labels_filters_active_status_only():
    db, captured = _capturing_db()
    await repo_list_plot_cycle_labels(db)
    sql = _compiled(captured["stmt"])
    assert "status = 'active'" in sql


async def test_list_plot_cycle_labels_is_distinct_and_sorted():
    db, captured = _capturing_db()
    await repo_list_plot_cycle_labels(db)
    sql = _compiled(captured["stmt"])
    assert "DISTINCT" in sql.upper()
    assert "ORDER BY" in sql.upper()
    assert "cycle_label" in sql.lower()


async def test_list_plot_cycle_labels_excludes_null_and_blank():
    db, captured = _capturing_db()
    await repo_list_plot_cycle_labels(db)
    sql = _compiled(captured["stmt"])
    assert "IS NOT NULL" in sql.upper()


async def test_list_plot_cycle_labels_joins_plot_for_supplier_scoping():
    """cycle_label has no Plot mirror column — this must join Plot so
    supplier_id filtering/RLS applies the same way list_plot_provinces
    does."""
    supplier_id = uuid4()
    db, captured = _capturing_db()
    await repo_list_plot_cycle_labels(db, supplier_id=supplier_id)
    sql = _compiled(captured["stmt"])
    assert f"plots.supplier_id = '{supplier_id.hex}'" in sql or str(supplier_id) in sql


async def test_list_plot_cycle_labels_returns_only_the_label_strings():
    db, captured = _capturing_db(rows=["jun2026", "aug2026"])
    result = await repo_list_plot_cycle_labels(db)
    assert result == ["jun2026", "aug2026"]


# --- repository: closed/cancelled cycle never matches -----------------------
# DB-less structural proof (compiled SQL) that only 'active' participates —
# a real integration test would need a live Postgres session; the WHERE
# clause literal is the authoritative, DB-free way to prove this repo-wide.

def test_apply_cycle_label_filter_source_hardcodes_active_status():
    from app.repositories import plot_repository

    src = inspect.getsource(plot_repository._apply_cycle_label_filter)
    assert 'PlotCycle.status == "active"' in src
    # Only 'active' is ever compared against PlotCycle.status — a closed
    # ('harvested') or 'cancelled' cycle's label is never matched.
    assert 'PlotCycle.status == "harvested"' not in src
    assert 'PlotCycle.status == "cancelled"' not in src


# --- endpoint: GET /plots forwards cycle_label ------------------------------

async def test_list_plots_endpoint_forwards_cycle_label():
    with patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[])) as mocked:
        await list_plots(db=AsyncMock(), cycle_label="jun2026")
    mocked.assert_awaited_once_with(
        ANY, supplier_id=None, province=None, crop=None, variety=None,
        limit=50, offset=0, q=None, active_only=False, plot_status="all",
        cycle_label="jun2026",
    )


# --- endpoint: GET /plots/cycle-labels --------------------------------------

async def test_list_plot_cycle_labels_endpoint_forwards_scope_filters():
    supplier_id = uuid4()
    with patch(f"{_P}.repo.list_plot_cycle_labels", AsyncMock(return_value=["jun2026"])) as mocked:
        result = await list_plot_cycle_labels(
            db=AsyncMock(), supplier_id=supplier_id, plot_status="active",
        )
    mocked.assert_awaited_once_with(ANY, supplier_id=supplier_id, plot_status="active")
    assert result == ["jun2026"]


def test_cycle_labels_route_requires_plots_read_and_rls_context():
    src = inspect.getsource(plots_module)
    idx = src.index('@router.get("/cycle-labels"')
    end = src.index("async def list_plot_cycle_labels(")
    block = src[idx:end]
    assert "PermissionKey.PLOTS_READ" in block
    assert "get_rls_context" in block


def test_cycle_labels_route_is_get_and_returns_list_of_str():
    matched = [r for r in plots_module.router.routes if getattr(r, "path", "") == "/cycle-labels"]
    assert len(matched) == 1
    assert matched[0].methods == {"GET"}
    assert matched[0].response_model == list[str]


# --- endpoint: Excel template forwards cycle_label + mutual exclusivity ----

def _user(*, roles: list[str] = ("internal:admin",), supplier_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), roles=[SimpleNamespace(name=r) for r in roles],
        supplier_id=supplier_id, is_supplier_admin=False,
    )


def _db_with_supplier(supplier) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=supplier)
    db.execute = AsyncMock(return_value=result)
    return db


def _fake_supplier(id_=None):
    return SimpleNamespace(id=id_ or uuid4(), code="SUP001", name="Supplier One", is_active=True)


async def test_template_cycle_label_alone_without_supplier_id_is_422():
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            cycle_label="jun2026",
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง"


async def test_template_cycle_label_forwarded_to_list_plots():
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])) as mk_list, \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])), \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
            cycle_label="jun2026",
        )
    assert mk_list.call_args.kwargs["cycle_label"] == "jun2026"


async def test_template_cycle_label_never_forwarded_to_excluded_plots():
    """Same treatment as crop/variety — an excluded plot (almost always with
    no active cycle) can't meaningfully "match" an active-cycle filter."""
    sid = uuid4()
    with patch(f"{_P}.get_supplier_scope_filter", AsyncMock(return_value=[])), \
         patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[MagicMock()])), \
         patch(f"{_P}._fetch_excluded_plots", AsyncMock(return_value=[])) as mk_excluded, \
         patch(f"{_P}._contextual_plot_template_workbook", MagicMock(return_value=b"CTX")):
        await download_plot_import_template(
            current_user=_user(), db=_db_with_supplier(_fake_supplier(sid)),
            supplier_id=sid, province=None, crop=None, variety=None, q=None,
            cycle_label="jun2026",
        )
    assert "cycle_label" not in mk_excluded.call_args.kwargs


async def test_template_cycle_label_combined_with_all_suppliers_is_422():
    with pytest.raises(HTTPException) as exc:
        await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
            template_mode="all_suppliers", cycle_label="jun2026",
        )
    assert exc.value.status_code == 422


async def test_template_no_params_at_all_still_returns_generic_template():
    """cycle_label defaults to None — the pre-8-18 no-filter path is
    unaffected."""
    with patch(f"{_P}._template_suppliers", AsyncMock(return_value=[])), \
         patch(f"{_P}._plot_template_workbook", MagicMock(return_value=b"GENERIC")), \
         patch(f"{_P}.repo.list_plots", AsyncMock()) as mk_list:
        resp = await download_plot_import_template(
            current_user=_user(), db=MagicMock(),
            supplier_id=None, province=None, crop=None, variety=None, q=None,
        )
    assert resp.body == b"GENERIC"
    mk_list.assert_not_awaited()
