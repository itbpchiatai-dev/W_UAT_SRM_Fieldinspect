"""Retention job for partitioned log tables.

Drops partitions whose entire window is older than the configured
retention period. Defaults to 60 days; per-table overrides live in
app_settings under `logging.<table>.retention_days` (read via
AppSettingService — admin can change without redeploy).

Why f-string SQL here (AGENTS.md §3 rule 4 normally bans it):
DROP TABLE takes an identifier, not a bind value. The partition name
is read back from pg_inherits and then re-validated via _IDENT_RE
before splicing — defence in depth against a tampered catalog or a
test fixture that snuck in a weird table name.

See docs/logging.md §6.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.app_setting_service import AppSettingService

DEFAULT_RETENTION_DAYS = 60

# Per-table retention override keys (app_settings). Missing key → falls
# back to DEFAULT_RETENTION_DAYS. Edit via /settings/admin (super-admin).
RETENTION_KEYS = {
    "activity_logs": "logging.activity.retention_days",
    "system_logs": "logging.system.retention_days",
    "ai_call_logs": "logging.ai.retention_days",
}

# Strict identifier shape — partition names follow `<table>_<YYYY>_<MM>`.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _next_month(year: int, month: int) -> date:
    return date(year + (month // 12), month % 12 + 1, 1)


async def drop_old_partitions(db: AsyncSession) -> dict[str, list[str]]:
    """Drop partitions older than retention. Returns dropped partition names."""
    today = date.today()
    dropped: dict[str, list[str]] = {}
    settings = AppSettingService(db)

    for parent_table, key in RETENTION_KEYS.items():
        override = await settings.get(key, DEFAULT_RETENTION_DAYS)
        try:
            retention_days = int(override)
        except (TypeError, ValueError):
            retention_days = DEFAULT_RETENTION_DAYS
        cutoff = today - timedelta(days=retention_days)

        # Round 8-17C.1 — `:parent::regclass` (a bind param immediately
        # followed by a Postgres cast) is a known SQLAlchemy pitfall: the
        # driver mis-parses the `:parent::regclass` run and sends Postgres a
        # syntax error ("syntax error at or near ':'") every single time
        # this ran, silently — old log partitions were NEVER actually
        # dropped, quietly violating the 60-day retention policy. CAST(...)
        # is the same parameterized bind, just not adjacent to `::`.
        result = await db.execute(text(
            "SELECT inhrelid::regclass::text AS partition_name "
            "FROM pg_inherits "
            "WHERE inhparent = CAST(:parent AS regclass)"
        ), {"parent": parent_table})

        dropped[parent_table] = []
        for row in result.all():
            partition_name = row[0]
            # partition names look like `activity_logs_2026_01`
            parts = partition_name.rsplit("_", 2)
            if len(parts) < 3:
                continue
            try:
                year = int(parts[-2])
                month = int(parts[-1])
            except ValueError:
                continue
            partition_end = _next_month(year, month)
            if partition_end > cutoff:
                continue
            if not _IDENT_RE.match(partition_name):
                # Catalog returned something we won\'t splice into DDL.
                continue
            await db.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
            dropped[parent_table].append(partition_name)

    await db.commit()
    return dropped
