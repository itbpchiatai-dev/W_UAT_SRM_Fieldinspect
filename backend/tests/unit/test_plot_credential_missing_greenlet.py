"""Round 8-9F.0 — the PUT credential endpoint must never serialise an expired row.

Live runtime evidence (dev, 2026-08-03): PUT
/api/v1/plots/{plot_id}/inspection-access-credential returned 500 with

    sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called;
    can't call await_only() here

and a traceback through orm_pre_session_exec -> _autoflush ->
_emit_insert_statements. The chain:

  1. the repository flushes the INSERT/UPDATE and returns the row
  2. ActivityLogger.log() only does db.add(), so the log row stays PENDING
  3. _credential_status(row) reads row.updated_at, which is server-generated
     (TimestampMixin: server_default=now(), onupdate=now()) and is therefore an
     EXPIRED attribute right after the flush
  4. that read triggers a lazy SELECT, and SQLAlchemy autoflushes before any ORM
     query -- so it also tries to INSERT the pending ActivityLog
  5. both are IO driven from a SYNCHRONOUS attribute access, which asyncpg
     cannot serve -> MissingGreenlet -> HTTP 500

The fix is one explicit `await db.refresh(row)` between (2) and (3): the pending
log INSERT is autoflushed inside an await, and is_active/credential_version/
updated_at come back materialised. Every test here fails if that line is removed.

DB-less, like the rest of this suite: the route function is called directly.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.v1.plots import set_plot_inspection_access_credential
from app.schemas.plot import PlotInspectionCredentialSet

_P = "app.api.v1.plots"
_NOW = datetime.datetime(2026, 8, 3, tzinfo=datetime.timezone.utc)
# Placeholder only — never a real credential, and never written anywhere.
_PIN = "135790"


class _ExpiredUpdatedAtRow:
    """A credential row shaped like the real one immediately AFTER a flush:
    everything Python assigned is readable, but the server-generated
    `updated_at` raises exactly the way an expired attribute does under
    asyncpg — until a refresh materialises it.

    credential_version is deliberately readable from the start: the repository
    assigns it in Python, which is why the endpoint can safely put it in the
    activity-log metadata BEFORE the refresh."""

    def __init__(self, version: int = 1, is_active: bool = True) -> None:
        self.id = uuid4()
        self.plot_id = uuid4()
        self.password_hash = "$2b$12$placeholder-hash"
        self.password_lookup_digest = "a" * 64
        self.credential_version = version
        self.is_active = is_active
        self._refreshed = False

    def materialize(self) -> None:
        self._refreshed = True

    @property
    def updated_at(self):
        if not self._refreshed:
            raise RuntimeError(
                "MissingGreenlet: greenlet_spawn has not been called; "
                "can't call await_only() here"
            )
        return _NOW


def _user():
    return SimpleNamespace(id=uuid4(), email="admin@example.com")


def _db_that_materializes(row: _ExpiredUpdatedAtRow, calls: list[str] | None = None):
    """A db whose refresh() actually materialises the row, so the endpoint can
    only succeed if it genuinely awaits it."""
    db = AsyncMock()

    async def _refresh(target, **_kwargs):
        if calls is not None:
            calls.append("refresh")
        assert target is row, "refresh must target the credential row"
        target.materialize()

    db.refresh = AsyncMock(side_effect=_refresh)
    return db


def _logger(calls: list[str] | None = None, captured: dict | None = None):
    logger = AsyncMock()

    async def _log(**kwargs):
        if calls is not None:
            calls.append("log")
        if captured is not None:
            captured.update(kwargs)

    logger.log = AsyncMock(side_effect=_log)
    return logger


def _patched(row, db, *, logger=None, plot=None):
    """The four patches every test here needs: both plot reads, the pepper-
    dependent digest, the repository write, and ActivityLogger."""
    plot = plot or SimpleNamespace(id=uuid4())
    return (
        patch(f"{_P}.repo.get_plot", AsyncMock(return_value=plot)),
        patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_P}.build_plot_access_password_lookup_digest", return_value="a" * 64),
        patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
              AsyncMock(return_value=row)),
        patch(f"{_P}.ActivityLogger", return_value=logger or _logger()),
        plot,
    )


async def _put(row, db, *, logger=None, plot=None):
    p_get, p_lock, p_digest, p_set, p_log, resolved = _patched(
        row, db, logger=logger, plot=plot
    )
    with p_get, p_lock, p_digest, p_set, p_log:
        return await set_plot_inspection_access_credential(
            plot_id=uuid4(),
            payload=PlotInspectionCredentialSet(password=_PIN),
            current_user=_user(),
            db=db,
        ), resolved


# --- first set --------------------------------------------------------------

async def test_first_set_refreshes_before_serialising_and_returns_version_1() -> None:
    row = _ExpiredUpdatedAtRow(version=1)
    db = _db_that_materializes(row)

    result, _plot = await _put(row, db)

    db.refresh.assert_awaited_once()
    assert result.configured is True
    assert result.credential_version == 1
    # Reading this at all is the proof: without the refresh it raises.
    assert result.updated_at == _NOW


# --- replace ----------------------------------------------------------------

async def test_replace_refreshes_before_serialising_and_reports_the_new_version() -> None:
    row = _ExpiredUpdatedAtRow(version=2)
    db = _db_that_materializes(row)

    result, _plot = await _put(row, db)

    db.refresh.assert_awaited_once()
    assert result.credential_version == 2
    assert result.updated_at == _NOW


# --- ordering ---------------------------------------------------------------

async def test_the_security_event_is_logged_before_the_refresh() -> None:
    """Ordering IS the fix. The log entry must already be pending when the
    refresh runs, so the refresh's own autoflush writes it inside an await
    rather than leaving it to detonate under a lazy attribute load."""
    calls: list[str] = []
    row = _ExpiredUpdatedAtRow(version=1)
    db = _db_that_materializes(row, calls)

    await _put(row, db, logger=_logger(calls))

    assert calls == ["log", "refresh"]


# --- the regression guard ---------------------------------------------------

async def test_serialising_without_a_working_refresh_still_blows_up() -> None:
    """If the `await db.refresh(row)` line is deleted, the endpoint goes back to
    reading an expired attribute — this test is what catches that. Simulated by
    a refresh that is awaited but materialises nothing."""
    row = _ExpiredUpdatedAtRow(version=1)
    db = AsyncMock()
    db.refresh = AsyncMock()

    with pytest.raises(RuntimeError, match="MissingGreenlet"):
        await _put(row, db)


# --- transaction behaviour --------------------------------------------------

async def test_a_refresh_failure_propagates_so_the_request_rolls_back() -> None:
    """A failed refresh must never be swallowed into a half-truthful 200 —
    get_db owns the transaction and has to roll the whole PUT back."""
    row = _ExpiredUpdatedAtRow(version=1)
    db = AsyncMock()
    db.refresh = AsyncMock(side_effect=RuntimeError("connection lost"))

    with pytest.raises(RuntimeError, match="connection lost"):
        await _put(row, db)

    db.commit.assert_not_awaited()


async def test_the_endpoint_never_commits_by_itself() -> None:
    row = _ExpiredUpdatedAtRow(version=1)
    db = _db_that_materializes(row)

    await _put(row, db)

    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


# --- security ---------------------------------------------------------------

async def test_activity_metadata_carries_only_the_credential_version() -> None:
    captured: dict = {}
    row = _ExpiredUpdatedAtRow(version=3)
    db = _db_that_materializes(row)
    plot = SimpleNamespace(id=uuid4())

    await _put(row, db, logger=_logger(captured=captured), plot=plot)

    assert captured["metadata"] == {"credential_version": 3}
    assert captured["is_security_event"] is True
    assert captured["risk_level"] == "high"
    assert captured["resource_id"] == str(plot.id)
    blob = repr(captured)
    for leaked in (_PIN, "$2b$", "a" * 64, "phone", "digest"):
        assert leaked not in blob


async def test_the_refreshed_response_still_exposes_only_status_fields() -> None:
    row = _ExpiredUpdatedAtRow(version=1)
    db = _db_that_materializes(row)

    result, _plot = await _put(row, db)

    assert set(result.model_dump(by_alias=True)) == {
        "configured", "credentialVersion", "updatedAt",
    }
    dumped = result.model_dump_json(by_alias=True)
    for leaked in (_PIN, "$2b$", "a" * 64, "digest", "pepper", "hash"):
        assert leaked not in dumped
