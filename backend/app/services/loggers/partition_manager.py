"""Partition manager for partitioned log tables.

All log tables are partitioned by RANGE(created_at) with monthly
partitions. Partitions must exist BEFORE the rows targeting them are
inserted; otherwise INSERT fails. Run on startup + schedule monthly.

Why f-string SQL here (AGENTS.md §3 rule 4 normally bans it):
PostgreSQL DDL — CREATE TABLE, PARTITION OF, identifiers — cannot be
bound via $1 parameters. Every value below is internally derived
(PARTITIONED_TABLES whitelist + date arithmetic), never user input.
The _IDENT_RE assertion makes that boundary explicit.

See docs/logging.md §5.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PARTITIONED_TABLES = ["activity_logs", "system_logs", "ai_call_logs"]

# Defence-in-depth: even though every value comes from a hardcoded list
# or date math, we re-validate identifier shape before splicing into DDL.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _assert_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"refusing to splice non-identifier into DDL: {name!r}")
    return name


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    """Return first day of the month after `d`."""
    year = d.year + (d.month // 12)
    month = d.month % 12 + 1
    return date(year, month, 1)


async def ensure_partitions_exist(db: AsyncSession, months_ahead: int = 2) -> None:
    """Create partitions for current month + N months ahead. Idempotent."""
    start = _month_start(date.today())

    for _ in range(months_ahead + 1):
        end = _next_month(start)
        for table in PARTITIONED_TABLES:
            _assert_ident(table)
            partition_name = _assert_ident(f"{table}_{start.strftime('%Y_%m')}")
            # DDL — identifiers/dates only, no user input. See module docstring.
            await db.execute(text(
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF {table} "
                f"FOR VALUES FROM (\'{start.isoformat()}\') TO (\'{end.isoformat()}\')"
            ))
        start = end

    await db.commit()
