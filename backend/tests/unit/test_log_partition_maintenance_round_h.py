"""Log partition maintenance after round H.

The incident: from 2026-09-01 every audited write on UAT failed with
`no partition of relation "activity_logs" found for row`, rolled back the
business transaction it was part of, and — because the commit happens after
the response is sent — still answered 200. It ran for a week unnoticed.

Root cause: the app connects as the runtime role, which has no CREATE on
schema public, so the monthly job's `CREATE TABLE ... PARTITION OF` had NEVER
succeeded once. The August partitions came from migration 0001. And the
failure was invisible because the job's own failure report is written to
system_logs, which is partitioned by the same rule and had also run out.

These tests pin the three properties that keep it from recurring. DB-free —
they assert what SQL is sent and how failures are handled, never a live
database.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.loggers import retention
from app.services.loggers.partition_manager import (
    PARTITIONED_TABLES,
    ensure_partitions_exist,
    months_of_coverage,
)

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_09_08_0000-0055_log_partition_maintenance.py"
)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return []


class _FakeDB:
    def __init__(self, scalar=0):
        self.calls: list[tuple[str, dict]] = []
        self._scalar = scalar
        self.committed = False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        return _Result(self._scalar)

    async def commit(self):
        self.committed = True


# --------------------------------------------------------------- creating --

async def test_ensure_goes_through_the_definer_function_not_raw_ddl():
    """The whole point of round H: the app must NOT issue CREATE TABLE. It
    has never been allowed to, and doing so failed silently for months."""
    db = _FakeDB(scalar=3)

    created = await ensure_partitions_exist(db, months_ahead=24)

    assert created == 3
    assert db.committed
    sql = " ".join(s for s, _ in db.calls)
    assert "srm_ensure_log_partitions" in sql
    assert "CREATE TABLE" not in sql.upper()
    assert "PARTITION OF" not in sql.upper()


async def test_ensure_passes_months_ahead_as_a_bind_parameter():
    db = _FakeDB(scalar=0)

    await ensure_partitions_exist(db, months_ahead=24)

    sql, params = db.calls[0]
    assert params == {"n": 24}
    assert "24" not in sql  # not spliced into the statement text


async def test_ensure_lets_a_privilege_error_out():
    """It must not swallow errors. Swallowing is how this became a week-long
    outage instead of a failed job someone could see."""
    db = _FakeDB()
    db.execute = AsyncMock(side_effect=RuntimeError("permission denied for schema public"))

    with pytest.raises(RuntimeError, match="permission denied"):
        await ensure_partitions_exist(db)


async def test_default_window_is_years_not_months():
    """Two months of headroom meant two months from a broken job to an
    outage. The default is deliberately far larger."""
    import inspect

    default = inspect.signature(ensure_partitions_exist).parameters["months_ahead"].default
    assert default >= 12


# --------------------------------------------------------------- dropping --

async def test_retention_uses_the_definer_function_and_never_raw_drop():
    """DROP needs ownership, which the app role does not have — the direct
    `DROP TABLE` this used to issue could never have worked either."""
    class _DB(_FakeDB):
        async def execute(self, stmt, params=None):
            text = str(stmt)
            self.calls.append((text, params or {}))
            if "pg_inherits" in text:
                class _R:
                    def all(self_inner):
                        return [("activity_logs_2000_01",)]
                return _R()
            return _Result(True)

    db = _DB()
    with patch.object(retention, "AppSettingService") as mk_settings:
        mk_settings.return_value.get = AsyncMock(return_value=1)
        result = await retention.drop_old_partitions(db)

    sql = " ".join(s for s, _ in db.calls)
    assert "srm_drop_log_partition" in sql
    assert "DROP TABLE" not in sql.upper()
    assert "activity_logs_2000_01" in result["activity_logs"]
    # the partition name travels as a bind value, never spliced
    assert any(p.get("p") == "activity_logs_2000_01" for _s, p in db.calls)


# --------------------------------------------------------------- coverage --

async def test_coverage_reports_negative_when_there_are_no_partitions():
    db = _FakeDB(scalar=None)
    assert await months_of_coverage(db) < 0


async def test_coverage_counts_whole_months_from_the_newest_partition():
    """0 means "this month is the last one" — writes fail at the rollover,
    which is exactly the state UAT was in on 2026-09-01."""
    today = date.today()
    db = _FakeDB(scalar=f"{today.year:04d}_{today.month:02d}")

    assert await months_of_coverage(db) == 0


async def test_coverage_spans_a_year_boundary():
    """Naive month subtraction reads December -> January as -11."""
    today = date.today()
    ahead = date(today.year + 1, today.month, 1)
    db = _FakeDB(scalar=f"{ahead.year:04d}_{ahead.month:02d}")

    assert await months_of_coverage(db) == 12


async def test_coverage_asks_about_the_table_it_was_given():
    db = _FakeDB(scalar=None)

    await months_of_coverage(db, table="system_logs")

    _sql, params = db.calls[0]
    assert params == {"t": "system_logs"}


async def test_coverage_rejects_a_non_identifier_table_name():
    db = _FakeDB(scalar=None)
    with pytest.raises(ValueError):
        await months_of_coverage(db, table="activity_logs; DROP TABLE users")


# -------------------------------------------------------------- migration --

def test_migration_and_module_agree_on_which_tables_are_partitioned():
    """The migration hardcodes its own array (it cannot import app code).
    If the two drift, one of them silently stops maintaining a table."""
    source = MIGRATION.read_text(encoding="utf-8")
    in_migration = set(re.findall(r"'(\w*_logs)'", source))
    assert set(PARTITIONED_TABLES) <= in_migration


def test_definer_functions_pin_their_search_path():
    """A SECURITY DEFINER function without a fixed search_path can be aimed
    at an attacker-controlled object and run as the owner.

    Counts per FUNCTION BODY, not per file — the phrase also appears in this
    migration's prose, which is not what needs the guarantee."""
    source = MIGRATION.read_text(encoding="utf-8")
    bodies = source.split("CREATE OR REPLACE FUNCTION")[1:]

    assert len(bodies) == 2, "expected exactly the two maintenance functions"
    for body in bodies:
        declaration = body.split("$fn$")[0]
        assert "SECURITY DEFINER" in declaration
        assert "SET search_path = pg_catalog, public" in declaration


def test_ddl_inside_the_definer_functions_is_schema_qualified():
    """With pg_catalog first in search_path — which is what makes the SET
    safe — an unqualified CREATE TABLE lands in pg_catalog and Postgres
    refuses it: "System catalog modifications are currently disallowed".
    The first run of this migration against a real database hit exactly that.
    """
    source = MIGRATION.read_text(encoding="utf-8")
    bodies = "".join(source.split("CREATE OR REPLACE FUNCTION")[1:])
    # Drop SQL comments — they discuss these statements in prose.
    bodies = "\n".join(
        line for line in bodies.splitlines() if not line.lstrip().startswith("--")
    )

    for statement in ("CREATE TABLE", "DROP TABLE", "PARTITION OF"):
        for occurrence in re.finditer(re.escape(statement) + r"\s+(\S+)", bodies):
            target = occurrence.group(1)
            assert target.startswith("public."), (
                f"{statement} target {target!r} is not schema-qualified"
            )
    assert "to_regclass('public.'" in bodies


def test_drop_helper_validates_its_argument():
    """It takes a name from the caller, so the pattern check is what stops it
    dropping a real table."""
    source = MIGRATION.read_text(encoding="utf-8")
    assert "refusing to drop" in source
    assert "^(activity_logs|system_logs|ai_call_logs)_[0-9]{4}_[0-9]{2}$" in source


def test_downgrade_keeps_the_partitions():
    """Dropping them would delete log rows, and they predate this migration."""
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source.split("def downgrade()")[1]
    assert "DROP FUNCTION" in downgrade
    assert "DROP TABLE" not in downgrade.upper()
