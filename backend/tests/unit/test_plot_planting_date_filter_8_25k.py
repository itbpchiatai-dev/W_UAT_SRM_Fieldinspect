"""วันที่เริ่ม...ถึง filter (round 8-25K) — GET /plots and POST /plots/
search-by-phone.

Same two-layer split and same EXISTS-against-active-cycle-only shape as
test_plot_cycle_label_filter_8_18.py: repository tests compile the SQLAlchemy
statement (literal binds) to prove the shape without a real database;
endpoint tests call the route functions directly and patch the repository.
"""
from __future__ import annotations

import datetime

from fastapi import Response
from unittest.mock import ANY, AsyncMock, patch

from app.api.v1.plots import list_plots, search_plots_by_phone
from app.repositories.plot_repository import list_plots as repo_list_plots
from app.repositories.plot_repository import (
    search_plots_by_phone as repo_search_plots_by_phone,
)
from app.schemas.plot import PlotPhoneSearchRequest

_P = "app.api.v1.plots"
_FROM = datetime.date(2026, 8, 1)
_TO = datetime.date(2026, 8, 31)


def _compiled(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        from types import SimpleNamespace
        return SimpleNamespace(all=lambda: self._rows)


def _capturing_db():
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult([])

    from types import SimpleNamespace
    return SimpleNamespace(execute=_execute), captured


# --- repository: list_plots — EXISTS against the ACTIVE cycle only ----------

async def test_list_plots_planting_date_filter_is_exists_against_active_cycle_only():
    db, captured = _capturing_db()
    await repo_list_plots(db, planting_date_from=_FROM, planting_date_to=_TO)
    sql = _compiled(captured["stmt"])
    assert "EXISTS" in sql
    assert "plot_cycles" in sql
    assert "status = 'active'" in sql
    assert "planting_date >= '2026-08-01'" in sql
    assert "planting_date <= '2026-08-31'" in sql


async def test_list_plots_planting_date_never_a_join_no_dedup_needed():
    db, captured = _capturing_db()
    await repo_list_plots(db, planting_date_from=_FROM)
    sql = _compiled(captured["stmt"])
    assert "JOIN plot_cycles" not in sql


async def test_list_plots_planting_date_from_only():
    db, captured = _capturing_db()
    await repo_list_plots(db, planting_date_from=_FROM)
    sql = _compiled(captured["stmt"])
    assert "planting_date >= '2026-08-01'" in sql
    assert "planting_date <=" not in sql


async def test_list_plots_planting_date_to_only():
    db, captured = _capturing_db()
    await repo_list_plots(db, planting_date_to=_TO)
    sql = _compiled(captured["stmt"])
    assert "planting_date <= '2026-08-31'" in sql
    assert "planting_date >=" not in sql


async def test_list_plots_no_planting_date_bounds_is_a_no_op():
    db, captured = _capturing_db()
    await repo_list_plots(db)
    sql = _compiled(captured["stmt"])
    # Not a bare "planting_date not in sql" check — Plot's own
    # current_planting_date column is always in the SELECT list, so that
    # substring is present even with no filter applied. "plot_cycles"
    # absence (no EXISTS subquery at all) is what actually proves the no-op,
    # same check test_list_plots_blank_or_none_cycle_label_is_a_no_op uses.
    assert "plot_cycles" not in sql


async def test_list_plots_planting_date_combines_with_other_filters():
    db, captured = _capturing_db()
    await repo_list_plots(db, crop="พริก", planting_date_from=_FROM, cycle_label="jun2026")
    sql = _compiled(captured["stmt"])
    assert "current_crop" in sql
    assert "cycle_label = 'jun2026'" in sql
    assert "planting_date >= '2026-08-01'" in sql


# --- repository: search_plots_by_phone — same filter, same semantics -------

async def test_search_plots_by_phone_planting_date_filter_is_exists_against_active_cycle():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678", planting_date_from=_FROM, planting_date_to=_TO)
    sql = _compiled(captured["stmt"])
    assert "status = 'active'" in sql
    assert "planting_date >= '2026-08-01'" in sql
    assert "planting_date <= '2026-08-31'" in sql


async def test_search_plots_by_phone_no_planting_date_bounds_is_a_no_op():
    db, captured = _capturing_db()
    await repo_search_plots_by_phone(db, "0812345678")
    sql = _compiled(captured["stmt"])
    assert "plot_cycles" not in sql


# --- closed/cancelled cycle never matches -----------------------------------
# DB-less structural proof (compiled SQL) that only 'active' participates —
# same reasoning as test_apply_cycle_label_filter_source_hardcodes_active_
# status in test_plot_cycle_label_filter_8_18.py.

def test_apply_planting_date_filter_source_hardcodes_active_status():
    import inspect
    from app.repositories import plot_repository

    src = inspect.getsource(plot_repository._apply_planting_date_filter)
    assert 'PlotCycle.status == "active"' in src
    assert 'PlotCycle.status == "harvested"' not in src
    assert 'PlotCycle.status == "cancelled"' not in src


# --- endpoint: GET /plots forwards planting_date_from/to --------------------

async def test_list_plots_endpoint_forwards_planting_date_bounds():
    with patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[])) as mocked:
        await list_plots(db=AsyncMock(), planting_date_from=_FROM, planting_date_to=_TO)
    mocked.assert_awaited_once_with(
        ANY, supplier_id=None, province=None, crop=None, variety=None,
        limit=50, offset=0, q=None, active_only=False, plot_status="all",
        cycle_label=None, planting_date_from=_FROM, planting_date_to=_TO,
    )


async def test_list_plots_endpoint_defaults_planting_date_bounds_to_none():
    with patch(f"{_P}.repo.list_plots", AsyncMock(return_value=[])) as mocked:
        await list_plots(db=AsyncMock())
    assert mocked.call_args.kwargs["planting_date_from"] is None
    assert mocked.call_args.kwargs["planting_date_to"] is None


# --- endpoint: POST /plots/search-by-phone forwards planting_date_from/to --

async def test_search_plots_by_phone_endpoint_forwards_planting_date_bounds():
    with patch(f"{_P}.repo.search_plots_by_phone", AsyncMock(return_value=[])) as mocked:
        await search_plots_by_phone(
            payload=PlotPhoneSearchRequest(
                phone="0812345678", planting_date_from=_FROM, planting_date_to=_TO,
            ),
            response=Response(), db=AsyncMock(),
        )
    assert mocked.call_args.kwargs["planting_date_from"] == _FROM
    assert mocked.call_args.kwargs["planting_date_to"] == _TO


async def test_planting_date_request_schema_defaults_to_none():
    req = PlotPhoneSearchRequest(phone="0812345678")
    assert req.planting_date_from is None
    assert req.planting_date_to is None
