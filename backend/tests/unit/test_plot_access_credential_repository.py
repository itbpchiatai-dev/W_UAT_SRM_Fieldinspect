"""Round 8-9A — plot_access_credential_repository: version semantics, the
flush-only/no-commit contract, and the phone+digest blind-index lookup.

DB-less: db.execute is stubbed to return whatever row the case needs (same
style as test_plot_access_phone_repository.py).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.repositories import plot_access_credential_repository as repo

_HASH = "$2b$12$fakefakefakefakefakefakefakefakefakefakefakefakefakefaXX"
_DIGEST = "a" * 64
_DIGEST_2 = "b" * 64


def _db(existing=None):
    """AsyncSession stub whose execute() yields `existing` as scalar_one_or_none."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    return db


def _plot():
    return SimpleNamespace(id=uuid4())


def _existing_row(version: int = 1, is_active: bool = True):
    return SimpleNamespace(
        id=uuid4(), plot_id=uuid4(), password_hash="old-hash",
        password_lookup_digest=_DIGEST, credential_version=version,
        is_active=is_active, updated_by_id=None,
    )


# --- set / replace ----------------------------------------------------------

async def test_first_set_inserts_version_1_and_active() -> None:
    db, plot = _db(existing=None), _plot()
    row = await repo.set_or_replace_plot_credential(
        db, plot, password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    db.add.assert_called_once()
    assert row.credential_version == 1
    assert row.is_active is True
    assert row.plot_id == plot.id
    assert row.password_hash == _HASH
    assert row.password_lookup_digest == _DIGEST


async def test_replace_increments_version_and_updates_hash_and_digest() -> None:
    existing = _existing_row(version=3)
    db, plot = _db(existing=existing), _plot()
    user_id = uuid4()
    row = await repo.set_or_replace_plot_credential(
        db, plot, password_hash="new-hash", password_lookup_digest=_DIGEST_2,
        updated_by_id=user_id,
    )
    db.add.assert_not_called()          # updated in place, never re-inserted
    assert row is existing
    assert row.credential_version == 4
    assert row.password_hash == "new-hash"
    assert row.password_lookup_digest == _DIGEST_2
    assert row.updated_by_id == user_id


async def test_replace_reactivates_an_inactive_row_without_restarting_version() -> None:
    existing = _existing_row(version=2, is_active=False)
    row = await repo.set_or_replace_plot_credential(
        _db(existing=existing), _plot(),
        password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    assert row.is_active is True
    assert row.credential_version == 3


async def test_the_same_password_may_be_set_on_several_plots() -> None:
    """Locked business rule: plots may deliberately share a password, and
    setting it on one plot must not consult or disturb another."""
    plot_a, plot_b = _plot(), _plot()
    row_a = await repo.set_or_replace_plot_credential(
        _db(existing=None), plot_a,
        password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    row_b = await repo.set_or_replace_plot_credential(
        _db(existing=None), plot_b,
        password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    assert row_a.plot_id != row_b.plot_id
    assert row_a.password_lookup_digest == row_b.password_lookup_digest
    assert row_a.credential_version == row_b.credential_version == 1


async def test_set_locks_the_credential_row_for_update() -> None:
    """Plot → PlotAccessCredential lock order: the caller already holds the Plot
    lock, so this must take the credential row's own lock, not read it dirty."""
    db = _db(existing=_existing_row())
    await repo.set_or_replace_plot_credential(
        db, _plot(), password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    stmt = str(db.execute.await_args_list[0].args[0]).upper()
    assert "FOR UPDATE" in stmt


async def test_set_flushes_but_never_commits() -> None:
    db = _db(existing=None)
    await repo.set_or_replace_plot_credential(
        db, _plot(), password_hash=_HASH, password_lookup_digest=_DIGEST,
    )
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


def test_repository_never_commits_anywhere() -> None:
    """Transaction ownership stays with the endpoint (flush-only contract)."""
    src = Path(inspect.getfile(repo)).read_text(encoding="utf-8")
    assert "db.commit" not in src
    assert "db.rollback" not in src


def test_repository_never_receives_or_returns_plaintext() -> None:
    sig = inspect.signature(repo.set_or_replace_plot_credential)
    assert "password" not in sig.parameters          # only password_hash
    assert "password_hash" in sig.parameters
    assert "password_lookup_digest" in sig.parameters


# --- status reads -----------------------------------------------------------

async def test_get_credential_status_returns_the_row_regardless_of_active() -> None:
    inactive = _existing_row(version=5, is_active=False)
    got = await repo.get_credential_status_by_plot_id(_db(existing=inactive), uuid4())
    assert got is inactive


async def test_get_active_credential_returns_none_when_never_set() -> None:
    assert await repo.get_active_credential_by_plot_id(_db(existing=None), uuid4()) is None


async def test_get_active_credential_filters_on_is_active() -> None:
    db = _db(existing=_existing_row())
    await repo.get_active_credential_by_plot_id(db, uuid4())
    stmt = str(db.execute.await_args_list[0].args[0]).lower()
    assert "is_active" in stmt


# --- 8-9C blind-index lookup ------------------------------------------------

async def test_lookup_filters_on_phone_digest_and_every_active_flag() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    await repo.lookup_active_access_rows_by_phone_and_digest(db, "0812345678", _DIGEST)
    stmt = str(db.execute.await_args_list[0].args[0]).lower()
    assert "plot_access_phones.phone_normalized" in stmt
    assert "plot_access_credentials.password_lookup_digest" in stmt
    # every link in the chain must be active
    for table in ("plot_access_phones", "plots", "suppliers", "plot_access_credentials"):
        assert f"{table}.is_active" in stmt


async def test_lookup_returns_every_matching_plot_not_just_one() -> None:
    """One phone+password pair may legitimately match several plots; all of
    them come back."""
    rows = [("a1", "p1", "s1", "c1"), ("a2", "p2", "s2", "c2")]
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)

    got = await repo.lookup_active_access_rows_by_phone_and_digest(
        db, "0812345678", _DIGEST
    )
    assert len(got) == 2
    assert got == [tuple(r) for r in rows]
