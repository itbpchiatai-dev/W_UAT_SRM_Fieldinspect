"""DbConnectionService — connect to admin-registered external PostgreSQL
databases and run sandbox queries against them.

Design notes:
* Engines are created on demand and cached by (connection id, updated_at)
  so an edit to host/credentials transparently invalidates the old engine.
  NullPool is used so we never hold idle sockets open to external DBs.
* Sandbox safety is layered:
    - read-only requests run inside a `SET TRANSACTION READ ONLY` tx, so
      the *server* rejects any write (defence beyond app-side checks).
    - writes additionally require the connection's `allow_write` flag.
    - every query runs under a `statement_timeout` and the returned row
      set is capped. Both limits come from app_settings (admin-tunable),
      not hardcoded constants — see _sandbox_limits.
* The sandbox executes admin-authored SQL verbatim via `text(sql)`. That
  is the whole point of the feature; it is gated to super_admin and fully
  audit-logged by the router. We never interpolate user input into SQL
  ourselves (the timeout uses set_config(...) with a bound parameter).
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.crypto import decrypt_secret
from app.db.models.db_connection import DbConnection

# Default safety limits if the app_settings rows are missing. Admin can
# override via app_settings (keys below) without a code change.
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
_DEFAULT_MAX_ROWS = 1_000
SETTING_TIMEOUT_MS = "db_sandbox.statement_timeout_ms"
SETTING_MAX_ROWS = "db_sandbox.max_rows"

# id -> (cache_key, engine). cache_key encodes updated_at so edits rebuild.
_engine_cache: dict[str, tuple[str, AsyncEngine]] = {}


def _ssl_connect_args(ssl_mode: str) -> dict[str, Any]:
    """Map libpq-style sslmode to asyncpg's `ssl` connect arg.

    asyncpg has no 'prefer' fallback, so prefer/allow connect without TLS;
    require/verify-* demand TLS. disable is explicit no-TLS.
    """
    mode = (ssl_mode or "prefer").lower()
    if mode in {"require", "verify-ca", "verify-full"}:
        return {"ssl": True}
    if mode == "disable":
        return {"ssl": False}
    return {}  # prefer / allow → asyncpg default (no TLS)


def _build_url(conn: DbConnection) -> URL:
    # URL.create escapes credentials safely (no manual string building).
    return URL.create(
        "postgresql+asyncpg",
        username=conn.username,
        password=decrypt_secret(conn.password_encrypted),
        host=conn.host,
        port=conn.port,
        database=conn.database,
    )


def _get_engine(conn: DbConnection) -> AsyncEngine:
    cache_key = f"{conn.updated_at.isoformat()}"
    cached = _engine_cache.get(str(conn.id))
    if cached and cached[0] == cache_key:
        return cached[1]
    if cached:
        # Stale engine from a previous config — dispose lazily on next loop.
        _schedule_dispose(cached[1])
    engine = create_async_engine(
        _build_url(conn),
        poolclass=NullPool,
        connect_args=_ssl_connect_args(conn.ssl_mode),
    )
    _engine_cache[str(conn.id)] = (cache_key, engine)
    return engine


def _schedule_dispose(engine: AsyncEngine) -> None:
    import asyncio

    try:
        asyncio.get_running_loop().create_task(engine.dispose())
    except RuntimeError:
        pass


async def invalidate(conn_id: str) -> None:
    """Drop a cached engine (call after update/delete)."""
    cached = _engine_cache.pop(conn_id, None)
    if cached:
        await cached[1].dispose()


def _coerce(value: Any) -> Any:
    """Make a DB value JSON-serializable for the API response."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        # Money/decimals as string to avoid float precision loss.
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


async def test_connection(conn: DbConnection) -> tuple[bool, str, str | None, int | None]:
    """Open a connection and read server version. Returns
    (success, message, server_version, latency_ms)."""
    engine = _get_engine(conn)
    start = time.perf_counter()
    try:
        async with engine.connect() as db_conn:
            result = await db_conn.execute(text("SELECT version()"))
            version = result.scalar_one()
        latency = int((time.perf_counter() - start) * 1000)
        return True, "Connected", str(version), latency
    except Exception as exc:  # noqa: BLE001 — surface any driver error to the admin
        # Invalidate so a fixed config isn't shadowed by a broken cached engine.
        await invalidate(str(conn.id))
        return False, f"{type(exc).__name__}: {exc}", None, None


async def list_tables(conn: DbConnection) -> list[dict[str, str]]:
    """List user tables/views in the target DB for the sandbox table browser.

    Read-only and constant SQL (no user input). Excludes the system schemas
    (pg_catalog / information_schema). Returns dicts shaped for DbTable:
    [{"schema_name": ..., "name": ..., "type": "table" | "view"}].
    """
    engine = _get_engine(conn)
    async with engine.connect() as db_conn:
        async with db_conn.begin():
            await db_conn.execute(text("SET TRANSACTION READ ONLY"))
            result = await db_conn.execute(
                text(
                    "SELECT table_schema, table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY table_schema, table_name"
                )
            )
            return [
                {
                    "schema_name": row[0],
                    "name": row[1],
                    "type": "view" if "VIEW" in (row[2] or "").upper() else "table",
                }
                for row in result.fetchall()
            ]


async def run_query(
    conn: DbConnection,
    sql: str,
    *,
    read_only: bool,
    limit: int,
    statement_timeout_ms: int,
    max_rows: int,
) -> dict[str, Any]:
    """Execute admin-authored SQL against the target connection.

    `read_only=True` runs in a read-only transaction (server rejects
    writes). `read_only=False` requires the connection's allow_write flag —
    enforced by the caller (router) before reaching here.
    """
    effective_limit = min(limit, max_rows)
    engine = _get_engine(conn)
    start = time.perf_counter()

    async with engine.connect() as db_conn:
        # Open an explicit transaction so SET TRANSACTION READ ONLY applies.
        async with db_conn.begin():
            if read_only:
                # Constant SQL (no user input) — strongest write guard.
                await db_conn.execute(text("SET TRANSACTION READ ONLY"))
            # set_config(name, value, is_local=true) — parameterized, scoped
            # to this transaction; avoids interpolating into a SET statement.
            await db_conn.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"),
                {"ms": str(int(statement_timeout_ms))},
            )
            result = await db_conn.execute(text(sql))

            if result.returns_rows:
                columns = list(result.keys())
                fetched = result.fetchmany(effective_limit + 1)
                truncated = len(fetched) > effective_limit
                visible = fetched[:effective_limit]
                rows = [[_coerce(v) for v in row] for row in visible]
                duration = int((time.perf_counter() - start) * 1000)
                return {
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": truncated,
                    "duration_ms": duration,
                    "command": None,
                    "read_only": read_only,
                }

            # Non-row statement (UPDATE/INSERT/DDL) — report affected count.
            duration = int((time.perf_counter() - start) * 1000)
            return {
                "columns": [],
                "rows": [],
                "row_count": result.rowcount if result.rowcount != -1 else 0,
                "truncated": False,
                "duration_ms": duration,
                "command": _command_tag(sql),
                "read_only": read_only,
            }


def _command_tag(sql: str) -> str:
    """First keyword of the statement, for the UI ("UPDATE", "INSERT"...)."""
    stripped = sql.lstrip()
    return stripped.split(None, 1)[0].upper() if stripped else ""
