"""app/services/loggers/retention.py — the pg_inherits lookup query (round
8-17C.1).

Round 8-17C's live QA found `WHERE inhparent = :parent::regclass` fails with
a real Postgres syntax error every time drop_old_partitions() runs — a bind
parameter immediately followed by a `::cast` is a known SQLAlchemy/asyncpg
parsing pitfall. Old activity_logs/system_logs/ai_call_logs partitions were
silently NEVER dropped, quietly violating the documented 60-day retention
policy. Fixed by using `CAST(:parent AS regclass)` instead — same
parameterized bind value, just not textually adjacent to `::`.

DB-free: db.execute is faked to capture the exact SQL text sent for the
pg_inherits SELECT and returns zero rows, so drop_old_partitions() never
reaches its DROP TABLE branch — this file is only about the SELECT's SQL
shape, not partition-drop behavior (unchanged, out of scope here).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.loggers import retention
from app.services.loggers.retention import RETENTION_KEYS, drop_old_partitions


class _FakeResult:
    def all(self):
        return []


class _FakeDB:
    def __init__(self):
        self.select_calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        text = str(stmt)
        if "pg_inherits" in text:
            self.select_calls.append((text, params or {}))
        return _FakeResult()

    async def commit(self):
        pass


async def test_pg_inherits_query_never_has_bind_param_adjacent_to_cast():
    """Regression guard for the exact bug: a bind param immediately followed
    by `::` breaks SQLAlchemy's asyncpg parameter substitution."""
    db = _FakeDB()
    with patch.object(retention, "AppSettingService") as mk_settings:
        mk_settings.return_value.get = AsyncMock(return_value=retention.DEFAULT_RETENTION_DAYS)
        await drop_old_partitions(db)

    assert db.select_calls, "expected at least one pg_inherits SELECT"
    for sql, _params in db.select_calls:
        assert ":parent::regclass" not in sql
        assert ":parent ::regclass" not in sql


async def test_pg_inherits_query_uses_cast_function_instead():
    db = _FakeDB()
    with patch.object(retention, "AppSettingService") as mk_settings:
        mk_settings.return_value.get = AsyncMock(return_value=retention.DEFAULT_RETENTION_DAYS)
        await drop_old_partitions(db)

    for sql, _params in db.select_calls:
        assert "CAST(:parent AS regclass)" in sql


async def test_pg_inherits_query_still_parameterized_not_fstring_spliced():
    """The fix must still be a genuine bind parameter (:parent in `params`),
    never an f-string-spliced table name — that would reopen a SQL
    injection surface on RETENTION_KEYS (even though today those keys are
    a fixed, hardcoded dict, not user input)."""
    db = _FakeDB()
    with patch.object(retention, "AppSettingService") as mk_settings:
        mk_settings.return_value.get = AsyncMock(return_value=retention.DEFAULT_RETENTION_DAYS)
        await drop_old_partitions(db)

    seen_tables = {params.get("parent") for _sql, params in db.select_calls}
    assert seen_tables == set(RETENTION_KEYS.keys())
    for sql, _params in db.select_calls:
        # None of the actual table names were spliced into the SELECT text.
        for table in RETENTION_KEYS:
            assert table not in sql
