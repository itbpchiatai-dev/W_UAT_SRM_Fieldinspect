"""plot_access_phone_repository.replace_plot_access_phones (round 8-3A).

No DB fixture exists in this repo — mocks the AsyncSession and exercises the
real full-replacement logic directly (same style as
test_plot_repository_sync_current_status.py). The final list_active query is the
second db.execute call; assertions are made on the mutated existing rows and the
db.add'd new rows (the actual state change), not on the mocked return value.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.repositories import plot_access_phone_repository as repo
from app.schemas.plot import PlotAccessPhoneConfig

_T0 = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)


def _row(phone: str, access_type: str, is_active: bool = True, **o) -> SimpleNamespace:
    d = dict(
        id=uuid4(), plot_id=None, phone_normalized=phone,
        access_type=access_type, is_active=is_active, created_at=_T0,
    )
    d.update(o)
    return SimpleNamespace(**d)


def _plot() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4())


def _result(rows):
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    return r


def _db(existing, final=None):
    """db.execute is called twice: (1) the with_for_update lock-select of
    existing rows, (2) the final list_active_plot_access_phones."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_result(existing), _result(final if final is not None else [])]
    )
    return db


def _cfg(primary=None, additional=None) -> PlotAccessPhoneConfig:
    return PlotAccessPhoneConfig(
        primaryPhone=primary, additionalPhones=additional or []
    )


# --- insert into an empty plot ---------------------------------------------

async def test_inserts_primary_and_additional_when_none_exist() -> None:
    plot = _plot()
    db = _db(existing=[])
    await repo.replace_plot_access_phones(
        db, plot, _cfg(primary="0845552162", additional=["0812345678"])
    )
    added = [c.args[0] for c in db.add.call_args_list]
    assert {(a.phone_normalized, a.access_type) for a in added} == {
        ("0845552162", "primary"),
        ("0812345678", "additional"),
    }
    assert all(a.is_active is True and a.plot_id == plot.id for a in added)


# --- replace primary --------------------------------------------------------

async def test_replace_primary_deactivates_old_and_creates_new() -> None:
    old = _row("0845550000", "primary", is_active=True)
    db = _db(existing=[old])
    await repo.replace_plot_access_phones(db, _plot(), _cfg(primary="0845552162"))
    assert old.is_active is False  # old primary deactivated
    added = [c.args[0] for c in db.add.call_args_list]
    assert [(a.phone_normalized, a.access_type) for a in added] == [
        ("0845552162", "primary")
    ]


# --- move primary → additional (reactivate same row, change type) ----------

async def test_move_primary_to_additional_reuses_row() -> None:
    """Round 8-3C: additional-only is invalid, so the config that moves
    0845552162 out of primary must simultaneously name a new primary — using
    an ALREADY-EXISTING row for it too, so both rows are reused and nothing is
    inserted (the property this test exists to prove)."""
    moved = _row("0845552162", "primary", is_active=True)
    new_primary = _row("0891112222", "additional", is_active=True)
    db = _db(existing=[moved, new_primary])
    await repo.replace_plot_access_phones(
        db, _plot(), _cfg(primary="0891112222", additional=["0845552162"])
    )
    assert moved.access_type == "additional"
    assert moved.is_active is True
    assert new_primary.access_type == "primary"
    assert new_primary.is_active is True
    db.add.assert_not_called()  # both existing rows reused, nothing inserted


# --- deactivate rows dropped from the config -------------------------------

async def test_deactivates_rows_not_in_new_config() -> None:
    # Round 8-3C: additional-only is invalid, so an unrelated primary row is
    # kept in the config (unaffected) alongside the additional rows under test.
    primary_row = _row("0899999999", "primary", is_active=True)
    keep = _row("0811111111", "additional", is_active=True)
    drop = _row("0822222222", "additional", is_active=True)
    db = _db(existing=[primary_row, keep, drop])
    await repo.replace_plot_access_phones(
        db, _plot(), _cfg(primary="0899999999", additional=["0811111111"])
    )
    assert primary_row.is_active is True
    assert keep.is_active is True
    assert drop.is_active is False
    db.add.assert_not_called()


# --- reactivate a previously-deactivated number ----------------------------

async def test_reactivates_existing_inactive_row() -> None:
    # Round 8-3C: additional-only is invalid, so an unrelated primary row is
    # kept in the config alongside the dormant row under test.
    primary_row = _row("0899999999", "primary", is_active=True)
    dormant = _row("0845552162", "additional", is_active=False)
    db = _db(existing=[primary_row, dormant])
    await repo.replace_plot_access_phones(
        db, _plot(), _cfg(primary="0899999999", additional=["0845552162"])
    )
    assert dormant.is_active is True
    db.add.assert_not_called()


# --- clear all --------------------------------------------------------------

async def test_empty_config_deactivates_everything() -> None:
    a = _row("0811111111", "primary", is_active=True)
    b = _row("0822222222", "additional", is_active=True)
    db = _db(existing=[a, b])
    await repo.replace_plot_access_phones(db, _plot(), _cfg())
    assert a.is_active is False
    assert b.is_active is False
    db.add.assert_not_called()


# --- flush-only, never commits ---------------------------------------------

async def test_flushes_but_never_commits() -> None:
    db = _db(existing=[_row("0811111111", "primary")])
    await repo.replace_plot_access_phones(db, _plot(), _cfg(primary="0845552162"))
    db.flush.assert_awaited()
    db.commit.assert_not_called()


async def test_two_phase_deactivate_before_reactivate() -> None:
    """When active rows exist, phase 1 deactivates + flushes BEFORE phase 2
    reactivates/inserts, so a single flush can't trip the partial unique
    indexes on itself → at least two flushes."""
    db = _db(existing=[_row("0845550000", "primary", is_active=True)])
    await repo.replace_plot_access_phones(db, _plot(), _cfg(primary="0845552162"))
    assert db.flush.await_count >= 2


# --- structural guards ------------------------------------------------------

def test_existing_rows_query_is_scoped_to_the_plot_and_locked() -> None:
    """Per-plot scope means the SAME phone on another plot is untouched;
    with_for_update takes the phone-row lock AFTER the caller's Plot lock."""
    src = inspect.getsource(repo.replace_plot_access_phones)
    assert "PlotAccessPhone.plot_id == plot.id" in src
    assert ".with_for_update()" in src
    assert "Plot-before-phones" in src or "Plot row lock" in src


def test_list_active_filters_active_and_orders_primary_first() -> None:
    src = inspect.getsource(repo.list_active_plot_access_phones)
    assert "PlotAccessPhone.is_active.is_(True)" in src
    assert "order_by" in src
