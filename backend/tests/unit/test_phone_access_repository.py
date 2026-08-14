"""plot_access_phone_repository — round 8-3B public-lookup queries.

No DB fixture: behavioral mocks for the simple helpers + source inspection for
the join/filter/order shape of the tuple queries (exact match, active-only,
active plot+supplier, active-cycle eager-load, deterministic order)."""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.repositories import plot_access_phone_repository as repo

_TODAY = datetime.date(2026, 7, 18)


def _result_scalar(val):
    r = MagicMock()
    r.scalar_one_or_none.return_value = val
    return r


def _result_scalars(vals):
    r = MagicMock()
    r.scalars.return_value.all.return_value = vals
    return r


def _result_all(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


# --- get_access_row_for_plot_from_ids ---------------------------------------

async def test_get_access_row_empty_ids_returns_none_without_query() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    out = await repo.get_access_row_for_plot_from_ids(db, [], uuid4())
    assert out is None
    db.execute.assert_not_awaited()


async def test_get_access_row_returns_scalar() -> None:
    row = SimpleNamespace(id=uuid4())
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_scalar(row))
    out = await repo.get_access_row_for_plot_from_ids(db, [uuid4()], uuid4())
    assert out is row


async def test_get_access_row_none_when_not_found() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_scalar(None))
    out = await repo.get_access_row_for_plot_from_ids(db, [uuid4()], uuid4())
    assert out is None


def test_get_access_row_source_filters_and_optional_lock() -> None:
    src = inspect.getsource(repo.get_access_row_for_plot_from_ids)
    assert "PlotAccessPhone.id.in_(access_phone_ids)" in src
    assert "PlotAccessPhone.plot_id == plot_id" in src
    assert "PlotAccessPhone.is_active.is_(True)" in src
    assert ".with_for_update()" in src
    assert "if for_update" in src


# --- access_phone_ids_inspected_today ---------------------------------------

async def test_inspected_today_empty_ids_returns_empty_set() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    out = await repo.access_phone_ids_inspected_today(db, [], _TODAY)
    assert out == set()
    db.execute.assert_not_awaited()


async def test_inspected_today_builds_set_and_drops_none() -> None:
    a, b = uuid4(), uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_scalars([a, b, None]))
    out = await repo.access_phone_ids_inspected_today(db, [a, b], _TODAY)
    assert out == {a, b}


def test_inspected_today_source_keys_by_access_phone_and_today() -> None:
    src = inspect.getsource(repo.access_phone_ids_inspected_today)
    assert "Record.plot_access_phone_id.in_(access_phone_ids)" in src
    assert "Record.is_active.is_(True)" in src
    assert "Record.record_date == today" in src


# --- lookup_active_access_rows_by_phone / list_active_access_rows_by_ids -----

async def test_lookup_by_phone_returns_tuples() -> None:
    rows = [(SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4()))]
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result_all(rows))
    out = await repo.lookup_active_access_rows_by_phone(db, "0845552162")
    assert len(out) == 1 and isinstance(out[0], tuple) and len(out[0]) == 3


async def test_list_by_ids_empty_returns_empty_without_query() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    out = await repo.list_active_access_rows_by_ids(db, [])
    assert out == []
    db.execute.assert_not_awaited()


def test_active_rows_stmt_is_exact_match_active_only_joined_ordered() -> None:
    base = inspect.getsource(repo._active_access_rows_stmt)
    assert "select(PlotAccessPhone, Plot, Supplier)" in base
    assert "PlotAccessPhone.plot_id == Plot.id" in base
    assert "Plot.supplier_id == Supplier.id" in base
    assert "PlotAccessPhone.is_active.is_(True)" in base
    assert "Plot.is_active.is_(True)" in base
    assert "Supplier.is_active.is_(True)" in base
    assert "selectinload(Plot.active_cycle)" in base  # no N+1
    # exact-match, no partial/prefix search
    lookup_src = inspect.getsource(repo.lookup_active_access_rows_by_phone)
    assert "PlotAccessPhone.phone_normalized == phone_normalized" in lookup_src
    assert "ilike" not in lookup_src and "like(" not in lookup_src
    ids_src = inspect.getsource(repo.list_active_access_rows_by_ids)
    assert "PlotAccessPhone.id.in_(access_phone_ids)" in ids_src


def test_order_is_supplier_then_plot_then_id() -> None:
    src = inspect.getsource(repo)
    assert "Supplier.name.asc()" in src
    assert "Supplier.code.asc()" in src
    assert "Plot.plot_code.asc()" in src
    assert "Plot.id.asc()" in src
