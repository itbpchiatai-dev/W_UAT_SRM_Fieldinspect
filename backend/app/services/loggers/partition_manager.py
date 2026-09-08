"""Partition manager for partitioned log tables.

All log tables are partitioned by RANGE(created_at) with monthly
partitions. Partitions must exist BEFORE the rows targeting them are
inserted; otherwise INSERT fails — and because the audit row is written
inside the caller's transaction, a missing partition does not just lose the
log line, it rolls back the business write that triggered it.

Round H — the DDL itself now lives in srm_ensure_log_partitions(), a
SECURITY DEFINER function created by migration 0055. This module used to
issue `CREATE TABLE ... PARTITION OF` directly, which the runtime role has
never been allowed to do (no CREATE on schema public), so the monthly job
failed every time it ran, silently, for months. See migration 0055 for the
full incident and the function's hardening.

See docs/logging.md §5.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Kept in sync with migration 0055's own hardcoded array — a test asserts it.
PARTITIONED_TABLES = ["activity_logs", "system_logs", "ai_call_logs"]

# Below this many months of remaining partitions, the system is close enough
# to the cliff to shout about it. Set well above the daily top-up cadence so
# an operator has months of warning, not days.
PARTITION_COVERAGE_WARN_MONTHS = 3

# Defence-in-depth for the one place a table name still reaches SQL from
# Python (months_of_coverage's catalog lookup).
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _assert_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"refusing to splice non-identifier into DDL: {name!r}")
    return name


async def ensure_partitions_exist(db: AsyncSession, months_ahead: int = 24) -> int:
    """Create partitions for current month + N months ahead. Idempotent.

    Round H — this no longer issues the DDL itself. The app connects as the
    runtime role, which has no CREATE on schema public, so every direct
    `CREATE TABLE ... PARTITION OF` raised InsufficientPrivilegeError. The
    scheduler job that calls this had therefore never once succeeded; the
    partitions it appeared to maintain were the ones migration 0001 created,
    and the day they ran out every audited write in the system began failing.

    The work is done by srm_ensure_log_partitions(), a SECURITY DEFINER
    function created by migration 0055 and owned by the table owner, so the
    runtime role can maintain log partitions without being able to create
    anything else. See that migration for the hardening.

    Returns the number of partitions created (0 when everything already
    exists), so the caller can log something meaningful.

    Raises whatever the DB raises — a caller that cannot create partitions
    must find out. This function used to be the only thing standing between a
    working system and a silent week-long outage; it does not swallow errors.
    """
    result = await db.execute(
        text("SELECT srm_ensure_log_partitions(CAST(:n AS int))"),
        {"n": months_ahead},
    )
    created = int(result.scalar_one())
    await db.commit()
    return created


async def months_of_coverage(db: AsyncSession, table: str = "activity_logs") -> int:
    """How many further whole months `table` has partitions for, counting the
    current month as 0. Negative is impossible; 0 means "this month is the
    last one" — i.e. writes start failing when the month turns.

    Cheap enough to call from a health/diagnostic route: one catalog query,
    no scan. Added in round H because the previous outage was invisible until
    it had been destroying writes for a week.
    """
    _assert_ident(table)
    result = await db.execute(
        text(
            "SELECT max(substring(c.relname from '[0-9]{4}_[0-9]{2}$')) "
            "  FROM pg_class c "
            "  JOIN pg_inherits i ON i.inhrelid = c.oid "
            "  JOIN pg_class p ON p.oid = i.inhparent "
            " WHERE p.relname = :t"
        ),
        {"t": table},
    )
    newest = result.scalar_one_or_none()
    if not newest:
        return -1
    year, month = (int(part) for part in newest.split("_"))
    today = date.today()
    return (year - today.year) * 12 + (month - today.month)
