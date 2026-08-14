#!/usr/bin/env python3
"""Add the opt-in Database Connections + Query Sandbox module to an existing
project generated from web-app-standard (v3.1.0).

Run from the root of the generated project:

    python patches/v3_1_0_db_connections_patch.py
    cd backend && alembic upgrade head
    # then generate a Fernet key and set DB_CONNECTIONS_ENCRYPTION_KEY in
    # backend/.env (see below), and re-seed:
    python -m app.seed

The patch is idempotent. It writes the module files, wires the router + seed +
frontend behind the FEATURE_DB_CONNECTIONS flag, sets the flag in project.config,
and adds Alembic migration 0011 chained onto the project's current head.

SECURITY: this feature stores external-DB credentials (Fernet-encrypted at rest)
and executes admin-authored SQL against arbitrary hosts. Read
docs/patterns/db-connections.md and obtain SECURITY_APPROVER sign-off before
enabling in production. Generate the encryption key with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path.cwd()
CHANGED: list[str] = []


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise FileNotFoundError(f"Missing expected file: {path}")
    return target.read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    old = target.read_text(encoding="utf-8") if target.exists() else None
    if old != text:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        CHANGED.append(path)


def replace_once(text: str, old: str, new: str, path: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{path}: expected snippet not found (project diverged from standard?)")
    return text.replace(old, new, 1)


def _as_list(value: str) -> list[str]:
    value = value.strip()
    if not value or value == "None":
        return []
    if value.startswith("(") or value.startswith("["):
        return re.findall(r'["\']([^"\']+)["\']', value)
    return [value.strip('"\'')]


def alembic_heads(exclude_revisions: Iterable[str] = ()) -> list[str]:
    versions = ROOT / "backend" / "alembic" / "versions"
    excluded = set(exclude_revisions)
    revisions: set[str] = set()
    parents: set[str] = set()
    for migration in versions.glob("*.py"):
        text = migration.read_text(encoding="utf-8")
        rev_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', text, re.M)
        down_match = re.search(r"^down_revision\s*=\s*(.+)$", text, re.M)
        if not rev_match:
            continue
        revision = rev_match.group(1)
        if revision in excluded:
            continue
        revisions.add(revision)
        if down_match:
            parents.update(_as_list(down_match.group(1)) or [])
    return sorted(revisions - parents - excluded)


# === verbatim module bodies (copied from web-app-standard scaffold) ==========
MODULE_FILES: dict[str, str] = {
    'backend/app/core/crypto.py': '''\
"""Symmetric encryption for secrets stored at rest in the app DB.

Used by the Database Connections feature to keep external-DB passwords
out of plaintext columns. Backed by Fernet (AES-128-CBC + HMAC) from the
`cryptography` package, keyed by the `DB_CONNECTIONS_ENCRYPTION_KEY` env
var — a url-safe base64 32-byte key.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

The key lives in `.env` only (never in source / project.config / DB).
Rotating it invalidates every stored ciphertext — re-enter passwords in
the UI after a rotation. `encrypt`/`decrypt` raise a clear RuntimeError
when the key is unset so the feature fails loudly instead of silently
persisting recoverable secrets.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretEncryptionError(RuntimeError):
    """Raised when encryption/decryption cannot proceed."""


@lru_cache
def _fernet() -> Fernet:
    key = (get_settings().DB_CONNECTIONS_ENCRYPTION_KEY or "").strip()
    if not key:
        raise SecretEncryptionError(
            "DB_CONNECTIONS_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \\"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\\"` and add it to backend/.env "
            "before managing database connections."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretEncryptionError(
            "DB_CONNECTIONS_ENCRYPTION_KEY is not a valid Fernet key "
            "(expected url-safe base64, 32 bytes)."
        ) from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 secret; returns url-safe base64 ciphertext (str)."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt ciphertext produced by `encrypt_secret`."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretEncryptionError(
            "Stored secret could not be decrypted — the encryption key may "
            "have changed since it was saved. Re-enter the password in the UI."
        ) from exc
''',
    'backend/app/db/models/db_connection.py': '''\
"""DbConnection — an admin-managed PostgreSQL connection target.

The host app can register multiple external PostgreSQL databases at
runtime (via Setup → Database Connections) and run ad-hoc queries against
them in the Query Sandbox — no code change or redeploy. The connection
password is stored Fernet-encrypted in `password_encrypted` (never
plaintext, never returned by the API). See app/core/crypto.py.

`allow_write` is a per-connection guard: even when a Sandbox request opts
out of read-only mode, writes are only executed if the connection itself
permits them. Defaults to read-only for safety.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class DbConnection(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "db_connections"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5432)
    database: Mapped[str] = mapped_column(String(128), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    # Fernet ciphertext — NEVER expose in any response schema.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # libpq-style sslmode the UI lets the admin pick; mapped to asyncpg's
    # `ssl` connect arg in the service. disable | prefer | require | verify-ca | verify-full
    ssl_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="prefer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-connection write guard for the sandbox (defaults OFF = read-only).
    allow_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "success" | "failed" — last connection-test outcome, surfaced in the list.
    last_test_status: Mapped[str | None] = mapped_column(String(20))

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
''',
    'backend/app/schemas/db_connection.py': '''\
"""Database Connections + Query Sandbox schemas (CamelBaseModel).

Read schemas deliberately OMIT the password — it is write-only. The
stored password is Fernet-encrypted and never leaves the server.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelBaseModel

SslMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]


class DbConnectionRead(CamelBaseModel):
    id: UUID
    name: str
    description: str | None = None
    host: str
    port: int
    database: str
    username: str
    ssl_mode: str
    is_active: bool
    allow_write: bool
    last_tested_at: datetime | None = None
    last_test_status: str | None = None
    created_at: datetime
    updated_at: datetime


class DbConnectionCreate(CamelBaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1)
    ssl_mode: SslMode = "prefer"
    is_active: bool = True
    allow_write: bool = False


class DbConnectionUpdate(CamelBaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=128)
    # Only re-encrypted + stored when a non-empty value is supplied; omit to
    # keep the existing password.
    password: str | None = None
    ssl_mode: SslMode | None = None
    is_active: bool | None = None
    allow_write: bool | None = None


class DbConnectionTestResult(CamelBaseModel):
    success: bool
    message: str
    server_version: str | None = None
    latency_ms: int | None = None


class DbTable(CamelBaseModel):
    """A user table/view in a target DB — powers the sandbox table browser."""

    schema_name: str
    name: str
    type: Literal["table", "view"]


class QueryRequest(CamelBaseModel):
    sql: str = Field(min_length=1)
    # Read-only is the safe default; the UI must explicitly opt out AND the
    # connection must have allow_write=True for a write to run.
    read_only: bool = True
    # Caps the returned row set; the server also enforces a hard ceiling
    # from app_settings (db_sandbox.max_rows).
    limit: int = Field(default=100, ge=1, le=10000)

    @field_validator("sql")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sql must not be blank")
        return v


class QueryResult(CamelBaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int
    # For non-SELECT statements (UPDATE/INSERT/...) Postgres returns a
    # command tag + affected-row count instead of a result set.
    command: str | None = None
    read_only: bool = True
''',
    'backend/app/services/db_connection_service.py': '''\
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
''',
    'backend/app/api/v1/db_connections.py': '''\
"""Database Connections + Query Sandbox API (Setup).

Admin-managed external PostgreSQL targets (CRUD), a connection test, and
a read-only-by-default query sandbox. Gated to super_admin via three
permission keys (db_connections.read/manage/query); every mutation and
every executed query is written to activity_logs at high risk level.

Passwords are Fernet-encrypted at rest (app/core/crypto.py) and never
returned. The sandbox executes admin-authored SQL verbatim — that is the
feature's purpose; it is NOT user-facing input. We log the statement's
command tag + length, never the raw SQL (it may contain literal PII).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.core.crypto import SecretEncryptionError, encrypt_secret
from app.db.models.db_connection import DbConnection
from app.db.session import get_db
from app.schemas.db_connection import (
    DbConnectionCreate,
    DbConnectionRead,
    DbConnectionTestResult,
    DbConnectionUpdate,
    DbTable,
    QueryRequest,
    QueryResult,
)
from app.services import db_connection_service as svc
from app.services.app_setting_service import AppSettingService
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["db_connections"])


async def _get_or_404(db: AsyncSession, conn_id: UUID) -> DbConnection:
    conn = (
        await db.execute(select(DbConnection).where(DbConnection.id == conn_id))
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    return conn


@router.get("", response_model=list[DbConnectionRead], dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_READ))
])
async def list_connections(db: AsyncSession = Depends(get_db)) -> list[DbConnectionRead]:
    result = await db.execute(select(DbConnection).order_by(DbConnection.name))
    return [DbConnectionRead.model_validate(c) for c in result.scalars().all()]


@router.get("/{conn_id}", response_model=DbConnectionRead, dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_READ))
])
async def get_connection(conn_id: UUID, db: AsyncSession = Depends(get_db)) -> DbConnectionRead:
    return DbConnectionRead.model_validate(await _get_or_404(db, conn_id))


@router.post("", response_model=DbConnectionRead, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permission(PermissionKey.DB_CONNECTIONS_MANAGE))])
async def create_connection(
    payload: DbConnectionCreate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DbConnectionRead:
    existing = (
        await db.execute(select(DbConnection).where(DbConnection.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A connection with this name already exists")
    try:
        encrypted = encrypt_secret(payload.password)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    conn = DbConnection(
        name=payload.name, description=payload.description,
        host=payload.host, port=payload.port, database=payload.database,
        username=payload.username, password_encrypted=encrypted,
        ssl_mode=payload.ssl_mode, is_active=payload.is_active,
        allow_write=payload.allow_write, created_by_user_id=user.id,
    )
    db.add(conn)
    await db.flush()
    await ActivityLogger(db).log(
        action="db_connection.created", action_type="create",
        resource_type="db_connection", resource_id=str(conn.id),
        user=user, request=request, risk_level="high",
        metadata={"name": conn.name, "host": conn.host, "database": conn.database,
                  "allowWrite": conn.allow_write},
    )
    return DbConnectionRead.model_validate(conn)


@router.put("/{conn_id}", response_model=DbConnectionRead, dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_MANAGE))
])
async def update_connection(
    conn_id: UUID,
    payload: DbConnectionUpdate,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DbConnectionRead:
    conn = await _get_or_404(db, conn_id)

    if payload.name is not None and payload.name != conn.name:
        clash = (
            await db.execute(select(DbConnection).where(DbConnection.name == payload.name))
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="A connection with this name already exists")

    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for field, value in data.items():
        setattr(conn, field, value)
    if password:  # only re-encrypt when a non-empty password is supplied
        try:
            conn.password_encrypted = encrypt_secret(password)
        except SecretEncryptionError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail=str(exc)) from exc

    await db.flush()
    await svc.invalidate(str(conn.id))  # drop cached engine — config changed
    await ActivityLogger(db).log(
        action="db_connection.updated", action_type="update",
        resource_type="db_connection", resource_id=str(conn.id),
        user=user, request=request, risk_level="high",
        metadata={"name": conn.name, "passwordChanged": bool(password),
                  "fields": sorted(data.keys())},
    )
    return DbConnectionRead.model_validate(conn)


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_MANAGE))
])
async def delete_connection(
    conn_id: UUID,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    conn = await _get_or_404(db, conn_id)
    name = conn.name
    await db.delete(conn)
    await svc.invalidate(str(conn_id))
    await ActivityLogger(db).log(
        action="db_connection.deleted", action_type="delete",
        resource_type="db_connection", resource_id=str(conn_id),
        user=user, request=request, risk_level="high",
        metadata={"name": name},
    )


@router.post("/{conn_id}/test", response_model=DbConnectionTestResult, dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_READ))
])
async def test_connection(
    conn_id: UUID,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> DbConnectionTestResult:
    conn = await _get_or_404(db, conn_id)
    success, message, version, latency = await svc.test_connection(conn)
    conn.last_test_status = "success" if success else "failed"
    from datetime import datetime, timezone
    conn.last_tested_at = datetime.now(timezone.utc)
    await db.flush()
    await ActivityLogger(db).log(
        action="db_connection.tested", action_type="other",
        resource_type="db_connection", resource_id=str(conn.id),
        user=user, request=request, risk_level="medium",
        metadata={"name": conn.name, "success": success},
    )
    return DbConnectionTestResult(
        success=success, message=message, server_version=version, latency_ms=latency,
    )


@router.get("/{conn_id}/tables", response_model=list[DbTable], dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_QUERY))
])
async def list_tables(conn_id: UUID, db: AsyncSession = Depends(get_db)) -> list[DbTable]:
    """List the target DB's tables/views for the sandbox browser.

    Schema metadata only (no row data) — not audit-logged: it is fetched
    automatically when a connection is selected and would only add noise.
    """
    conn = await _get_or_404(db, conn_id)
    if not conn.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connection is disabled")
    try:
        rows = await svc.list_tables(conn)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface the driver error to the admin sandbox
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{type(exc).__name__}: {exc}") from exc
    return [DbTable.model_validate(r) for r in rows]


@router.post("/{conn_id}/query", response_model=QueryResult, dependencies=[
    Depends(require_permission(PermissionKey.DB_CONNECTIONS_QUERY))
])
async def run_query(
    conn_id: UUID,
    payload: QueryRequest,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> QueryResult:
    conn = await _get_or_404(db, conn_id)
    if not conn.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Connection is disabled")

    # Write-guard: a write request needs BOTH an explicit read_only=false AND
    # the connection's allow_write flag. Otherwise force read-only.
    write_attempt = not payload.read_only
    if write_attempt and not conn.allow_write:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("This connection is read-only. Enable 'Allow write' on the "
                    "connection to run write statements."),
        )
    effective_read_only = not (write_attempt and conn.allow_write)

    settings_svc = AppSettingService(db)
    timeout_ms = int(await settings_svc.get(svc.SETTING_TIMEOUT_MS, svc._DEFAULT_STATEMENT_TIMEOUT_MS))
    max_rows = int(await settings_svc.get(svc.SETTING_MAX_ROWS, svc._DEFAULT_MAX_ROWS))

    try:
        result = await svc.run_query(
            conn, payload.sql,
            read_only=effective_read_only, limit=payload.limit,
            statement_timeout_ms=timeout_ms, max_rows=max_rows,
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — return the DB error to the admin sandbox
        await ActivityLogger(db).log(
            action="db_connection.query_failed", action_type="other",
            resource_type="db_connection", resource_id=str(conn.id),
            user=user, request=request, risk_level="high",
            metadata={"name": conn.name, "readOnly": effective_read_only,
                      "sqlLength": len(payload.sql), "error": type(exc).__name__},
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{type(exc).__name__}: {exc}") from exc

    # Audit the executed query — command tag + length only, never raw SQL
    # (it may carry literal PII; §3 rule 2).
    await ActivityLogger(db).log(
        action="db_connection.query", action_type="read_sensitive",
        resource_type="db_connection", resource_id=str(conn.id),
        user=user, request=request, risk_level="high",
        metadata={"name": conn.name, "readOnly": effective_read_only,
                  "command": result["command"], "rowCount": result["row_count"],
                  "sqlLength": len(payload.sql)},
    )
    return QueryResult(**result)
''',
    'backend/tests/security/test_db_connection_secrets.py': '''\
"""Database Connections — secret-handling guarantees.

1. The Read schema never carries the password (write-only field).
2. Fernet round-trip works and decrypt fails loudly on a rotated key.
3. The connection-credential perms are in the override deny-list so a
   non-super-admin holder of permissions.grant_override cannot acquire
   them per-user.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


def test_read_schema_has_no_password_field() -> None:
    from app.schemas.db_connection import DbConnectionRead

    assert "password" not in DbConnectionRead.model_fields
    assert "password_encrypted" not in DbConnectionRead.model_fields


def test_fernet_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", key)

    # Rebuild settings + clear the lru_cache so the new key is picked up.
    from app.core import config, crypto

    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()

    token = crypto.encrypt_secret("s3cr3t-pw")
    assert token != "s3cr3t-pw"
    assert crypto.decrypt_secret(token) == "s3cr3t-pw"


def test_decrypt_with_wrong_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config, crypto

    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()
    token = crypto.encrypt_secret("hello")

    # Rotate the key — old ciphertext must fail to decrypt, not return garbage.
    monkeypatch.setenv("DB_CONNECTIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()
    with pytest.raises(crypto.SecretEncryptionError):
        crypto.decrypt_secret(token)

    # Cleanup so other tests see a clean cache.
    config.get_settings.cache_clear()
    crypto._fernet.cache_clear()


def test_credential_perms_are_super_admin_only() -> None:
    # The deny-list lives as a local inside add_override; assert against the
    # seed source so a future rename keeps the guard in lockstep.
    import inspect

    from app.api.v1 import users

    src = inspect.getsource(users)
    for key in ("db_connections.read", "db_connections.manage", "db_connections.query"):
        assert key in src, f"{key} missing from users.py privilege deny-list"
''',
    'frontend/src/api/dbConnections.ts': '''\
/**
 * Database Connections + Query Sandbox API (Setup).
 *
 * Backs /settings/db-connections (CRUD + test) and /settings/query-sandbox.
 * The password is write-only — it is never present on a read response.
 */
import { apiClient } from './client';

export type SslMode = 'disable' | 'prefer' | 'require' | 'verify-ca' | 'verify-full';

export interface DbConnection {
  id: string;
  name: string;
  description: string | null;
  host: string;
  port: number;
  database: string;
  username: string;
  sslMode: SslMode;
  isActive: boolean;
  allowWrite: boolean;
  lastTestedAt: string | null;
  lastTestStatus: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DbConnectionCreate {
  name: string;
  description?: string | null;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  sslMode: SslMode;
  isActive: boolean;
  allowWrite: boolean;
}

// Partial update; omit `password` to keep the stored one.
export type DbConnectionUpdate = Partial<DbConnectionCreate>;

export interface DbConnectionTestResult {
  success: boolean;
  message: string;
  serverVersion: string | null;
  latencyMs: number | null;
}

export interface DbTable {
  schemaName: string;
  name: string;
  type: 'table' | 'view';
}

export interface QueryRequest {
  sql: string;
  readOnly: boolean;
  limit: number;
}

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  durationMs: number;
  command: string | null;
  readOnly: boolean;
}

const BASE = '/api/v1/db-connections';

export async function listConnections(): Promise<DbConnection[]> {
  const res = await apiClient.get<DbConnection[]>(BASE);
  return res.data;
}

export async function createConnection(payload: DbConnectionCreate): Promise<DbConnection> {
  const res = await apiClient.post<DbConnection>(BASE, payload);
  return res.data;
}

export async function updateConnection(
  id: string,
  payload: DbConnectionUpdate,
): Promise<DbConnection> {
  const res = await apiClient.put<DbConnection>(`${BASE}/${id}`, payload);
  return res.data;
}

export async function deleteConnection(id: string): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`);
}

export async function testConnection(id: string): Promise<DbConnectionTestResult> {
  const res = await apiClient.post<DbConnectionTestResult>(`${BASE}/${id}/test`);
  return res.data;
}

export async function listTables(id: string): Promise<DbTable[]> {
  const res = await apiClient.get<DbTable[]>(`${BASE}/${id}/tables`);
  return res.data;
}

export async function runQuery(id: string, payload: QueryRequest): Promise<QueryResult> {
  const res = await apiClient.post<QueryResult>(`${BASE}/${id}/query`, payload);
  return res.data;
}
''',
    'frontend/src/pages/settings/DatabaseConnections.tsx': '''\
/**
 * DatabaseConnections — Setup page to register/edit external PostgreSQL
 * targets (super_admin). Supports many connections; the Query Sandbox
 * runs against one at a time.
 *
 * The password field is write-only: blank on edit means "keep existing".
 * Saving never echoes the stored password back.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Database, Loader2, Pencil, Plug, Plus, Trash2, XCircle } from 'lucide-react';
import {
  type DbConnection,
  type DbConnectionCreate,
  type SslMode,
  createConnection,
  deleteConnection,
  listConnections,
  testConnection,
  updateConnection,
} from '../../api/dbConnections';

const SSL_MODES: SslMode[] = ['disable', 'prefer', 'require', 'verify-ca', 'verify-full'];

type FormState = DbConnectionCreate;

const EMPTY_FORM: FormState = {
  name: '', description: '', host: '', port: 5432, database: '', username: '',
  password: '', sslMode: 'prefer', isActive: true, allowWrite: false,
};

function toForm(c: DbConnection): FormState {
  return {
    name: c.name, description: c.description ?? '', host: c.host, port: c.port,
    database: c.database, username: c.username, password: '', sslMode: c.sslMode,
    isActive: c.isActive, allowWrite: c.allowWrite,
  };
}

export function DatabaseConnections() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<DbConnection | null>(null);
  const [creating, setCreating] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const { data: connections = [], isLoading } = useQuery({
    queryKey: ['db-connections'],
    queryFn: listConnections,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['db-connections'] });

  const saveM = useMutation({
    mutationFn: async (form: FormState) => {
      if (editing) {
        const payload = { ...form };
        if (!payload.password) delete (payload as Partial<FormState>).password;
        return updateConnection(editing.id, payload);
      }
      return createConnection(form);
    },
    onSuccess: () => { invalidate(); setEditing(null); setCreating(false); },
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteConnection(id),
    onSuccess: invalidate,
  });

  const testM = useMutation({
    mutationFn: (id: string) => testConnection(id),
    onSuccess: (res, id) => {
      setTestResult((prev) => ({
        ...prev,
        [id]: res.success
          ? `✓ ${res.message}${res.latencyMs != null ? ` (${res.latencyMs}ms)` : ''}`
          : `✗ ${res.message}`,
      }));
      invalidate();
    },
  });

  if (isLoading) {
    return (
      <div className="container mx-auto flex min-h-[40vh] items-center justify-center px-4">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const showForm = creating || editing !== null;

  return (
    <div className="container mx-auto max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Database className="h-5 w-5" /> {t('settings.dbConnections.title')}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.dbConnections.description')}</p>
        </div>
        {!showForm && (
          <button
            type="button"
            onClick={() => { setCreating(true); setEditing(null); }}
            className="inline-flex shrink-0 items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" /> {t('settings.dbConnections.new')}
          </button>
        )}
      </header>

      {showForm && (
        <ConnectionForm
          initial={editing ? toForm(editing) : EMPTY_FORM}
          isEdit={editing !== null}
          isPending={saveM.isPending}
          error={saveM.isError ? extractError(saveM.error) : null}
          onCancel={() => { setCreating(false); setEditing(null); saveM.reset(); }}
          onSubmit={(form) => saveM.mutate(form)}
        />
      )}

      <section className="mt-6 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        <table className="w-full text-sm">
          <thead className="border-b border-border text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.name')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.target')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.mode')}</th>
              <th className="px-4 py-3 font-medium">{t('settings.dbConnections.fields.lastTest')}</th>
              <th className="px-4 py-3 text-right font-medium">{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {connections.length === 0 ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">{t('common.noResults')}</td></tr>
            ) : connections.map((c) => (
              <tr key={c.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3">
                  <div className="font-medium">{c.name}</div>
                  {c.description && <div className="text-xs text-muted-foreground">{c.description}</div>}
                  {!c.isActive && <span className="text-xs text-muted-foreground">({t('settings.dbConnections.disabled')})</span>}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{c.username}@{c.host}:{c.port}/{c.database}</td>
                <td className="px-4 py-3">
                  {c.allowWrite
                    ? <span className="rounded bg-destructive/10 px-2 py-0.5 text-xs text-destructive">{t('settings.dbConnections.readWrite')}</span>
                    : <span className="rounded bg-secondary px-2 py-0.5 text-xs text-muted-foreground">{t('settings.dbConnections.readOnly')}</span>}
                </td>
                <td className="px-4 py-3 text-xs">
                  {testResult[c.id]
                    ? <span className={testResult[c.id].startsWith('✓') ? 'text-green-600' : 'text-destructive'}>{testResult[c.id]}</span>
                    : c.lastTestStatus === 'success'
                      ? <span className="inline-flex items-center gap-1 text-green-600"><CheckCircle2 className="h-3 w-3" /> {t('settings.dbConnections.ok')}</span>
                      : c.lastTestStatus === 'failed'
                        ? <span className="inline-flex items-center gap-1 text-destructive"><XCircle className="h-3 w-3" /> {t('settings.dbConnections.failed')}</span>
                        : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-1">
                    <IconButton title={t('settings.dbConnections.test')} onClick={() => testM.mutate(c.id)} busy={testM.isPending && testM.variables === c.id}>
                      <Plug className="h-4 w-4" />
                    </IconButton>
                    <IconButton title={t('common.edit')} onClick={() => { setEditing(c); setCreating(false); }}>
                      <Pencil className="h-4 w-4" />
                    </IconButton>
                    <IconButton title={t('common.delete')} danger
                      onClick={() => { if (window.confirm(t('settings.dbConnections.confirmDelete', { name: c.name }))) deleteM.mutate(c.id); }}>
                      <Trash2 className="h-4 w-4" />
                    </IconButton>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function IconButton({ children, title, onClick, danger, busy }: {
  children: React.ReactNode; title: string; onClick: () => void; danger?: boolean; busy?: boolean;
}) {
  return (
    <button
      type="button" title={title} onClick={onClick} disabled={busy}
      className={`rounded-md p-2 transition-colors hover:bg-secondary disabled:opacity-50 ${danger ? 'text-destructive' : 'text-muted-foreground'}`}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : children}
    </button>
  );
}

function ConnectionForm({ initial, isEdit, isPending, error, onCancel, onSubmit }: {
  initial: FormState; isEdit: boolean; isPending: boolean; error: string | null;
  onCancel: () => void; onSubmit: (form: FormState) => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(initial);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));
  const inputCls = 'rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <section className="mt-6 rounded-lg border border-border bg-card p-6 shadow-sm">
      <h2 className="text-base font-semibold">
        {isEdit ? t('settings.dbConnections.edit') : t('settings.dbConnections.new')}
      </h2>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={t('settings.dbConnections.fields.name')}>
          <input className={inputCls} value={form.name} onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.description')}>
          <input className={inputCls} value={form.description ?? ''} onChange={(e) => set('description', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.host')}>
          <input className={inputCls} value={form.host} onChange={(e) => set('host', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.port')}>
          <input type="number" className={inputCls} value={form.port} onChange={(e) => set('port', Number(e.target.value))} />
        </Field>
        <Field label={t('settings.dbConnections.fields.database')}>
          <input className={inputCls} value={form.database} onChange={(e) => set('database', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.username')}>
          <input className={inputCls} autoComplete="off" value={form.username} onChange={(e) => set('username', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.password')} hint={isEdit ? t('settings.dbConnections.passwordEditHint') : undefined}>
          <input type="password" className={inputCls} autoComplete="new-password"
            placeholder={isEdit ? '••••••••' : ''} value={form.password} onChange={(e) => set('password', e.target.value)} />
        </Field>
        <Field label={t('settings.dbConnections.fields.sslMode')}>
          <select className={inputCls} value={form.sslMode} onChange={(e) => set('sslMode', e.target.value as SslMode)}>
            {SSL_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </Field>
      </div>

      <div className="mt-4 flex flex-col gap-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.isActive} onChange={(e) => set('isActive', e.target.checked)} />
          {t('settings.dbConnections.fields.active')}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.allowWrite} onChange={(e) => set('allowWrite', e.target.checked)} />
          <span>{t('settings.dbConnections.fields.allowWrite')}</span>
        </label>
        {form.allowWrite && (
          <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {t('settings.dbConnections.allowWriteWarning')}
          </p>
        )}
      </div>

      {error && <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}

      <div className="mt-6 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
          {t('common.cancel')}
        </button>
        <button
          type="button"
          disabled={isPending || !form.name || !form.host || !form.database || !form.username || (!isEdit && !form.password)}
          onClick={() => onSubmit(form)}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
        >
          {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          {t('common.save')}
        </button>
      </div>
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

function extractError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    if (detail) return detail;
  }
  return err instanceof Error ? err.message : 'Error';
}

export default DatabaseConnections;
''',
    'frontend/src/pages/settings/QuerySandbox.tsx': '''\
/**
 * QuerySandbox — run ad-hoc SQL against a registered connection
 * (super_admin). Read-only by default; the write toggle is only honoured
 * when the selected connection has allow_write enabled (the server
 * re-enforces this). Results are capped server-side (db_sandbox.max_rows).
 */
import { useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Eye, Loader2, Play, Table2, Terminal } from 'lucide-react';
import {
  type DbConnection,
  type DbTable,
  type QueryResult,
  listConnections,
  listTables,
  runQuery,
} from '../../api/dbConnections';

export function QuerySandbox() {
  const { t } = useTranslation();
  const [connId, setConnId] = useState('');
  const [sql, setSql] = useState('');
  const [readOnly, setReadOnly] = useState(true);
  const [limit, setLimit] = useState(100);
  const sqlRef = useRef<HTMLTextAreaElement>(null);

  const { data: connections = [] } = useQuery({
    queryKey: ['db-connections'],
    queryFn: listConnections,
  });

  const active = connections.filter((c) => c.isActive);
  const selected: DbConnection | undefined = connections.find((c) => c.id === connId);

  const { data: tables = [], isLoading: tablesLoading } = useQuery({
    queryKey: ['db-tables', connId],
    queryFn: () => listTables(connId),
    enabled: !!connId,
  });

  const runM = useMutation({
    mutationFn: () => runQuery(connId, { sql, readOnly, limit }),
  });

  // Insert a table's identifier into the SQL editor: prefill a SELECT when
  // empty, otherwise splice it in at the cursor.
  const insertTable = (tbl: DbTable) => {
    const ident = tbl.schemaName === 'public' ? tbl.name : `${tbl.schemaName}.${tbl.name}`;
    setSql((prev) => {
      if (!prev.trim()) return `SELECT * FROM ${ident} LIMIT ${limit};`;
      const el = sqlRef.current;
      if (!el) return `${prev} ${ident}`;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      return prev.slice(0, start) + ident + prev.slice(end);
    });
    sqlRef.current?.focus();
  };

  const canRunWrite = selected?.allowWrite ?? false;
  const inputCls = 'rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  const result: QueryResult | undefined = runM.data;

  return (
    <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header>
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Terminal className="h-5 w-5" /> {t('settings.querySandbox.title')}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{t('settings.querySandbox.description')}</p>
      </header>

      <section className="mt-6 flex flex-col gap-4 rounded-lg border border-border bg-card p-6 shadow-sm">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.querySandbox.connection')}</span>
            <select className={inputCls} value={connId} onChange={(e) => { setConnId(e.target.value); setReadOnly(true); }}>
              <option value="">{t('settings.querySandbox.selectConnection')}</option>
              {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.querySandbox.limit')}</span>
            <input type="number" min={1} max={10000} className={inputCls} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          <label className="flex items-end gap-2 text-sm">
            <input
              type="checkbox"
              disabled={!canRunWrite}
              checked={!readOnly && canRunWrite}
              onChange={(e) => setReadOnly(!e.target.checked)}
              className="mb-2.5"
            />
            <span className="mb-2">
              {t('settings.querySandbox.allowWrite')}
              {!canRunWrite && <span className="block text-xs text-muted-foreground">{t('settings.querySandbox.writeLocked')}</span>}
            </span>
          </label>
        </div>

        {!readOnly && canRunWrite && (
          <p className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" /> {t('settings.querySandbox.writeWarning')}
          </p>
        )}

        <div className="flex flex-col gap-4 lg:flex-row">
          <label className="flex flex-1 flex-col gap-1 text-sm">
            <span className="font-medium">SQL</span>
            <textarea
              ref={sqlRef}
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              rows={8}
              spellCheck={false}
              placeholder="SELECT * FROM ..."
              className={`${inputCls} font-mono`}
            />
          </label>
          <TablesPanel
            hasConnection={!!connId}
            loading={tablesLoading}
            tables={tables}
            onPick={insertTable}
          />
        </div>

        <div className="flex justify-end">
          <button
            type="button"
            disabled={runM.isPending || !connId || !sql.trim()}
            onClick={() => runM.mutate()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
          >
            {runM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {t('settings.querySandbox.run')}
          </button>
        </div>
      </section>

      {runM.isError && (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive whitespace-pre-wrap">
          {extractError(runM.error)}
        </p>
      )}

      {result && <ResultPanel result={result} />}
    </div>
  );
}

function TablesPanel({ hasConnection, loading, tables, onPick }: {
  hasConnection: boolean;
  loading: boolean;
  tables: DbTable[];
  onPick: (tbl: DbTable) => void;
}) {
  const { t } = useTranslation();
  return (
    <aside className="flex w-full flex-col rounded-md border border-border bg-background lg:w-64 lg:shrink-0">
      <div className="border-b border-border px-3 py-2 text-sm font-medium">
        {t('settings.querySandbox.tables')}
        {hasConnection && tables.length > 0 && (
          <span className="ml-1 text-xs text-muted-foreground">({tables.length})</span>
        )}
      </div>
      <div className="max-h-64 overflow-auto p-1 lg:max-h-[14.5rem]">
        {!hasConnection ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t('settings.querySandbox.tablesSelectFirst')}</p>
        ) : loading ? (
          <div className="flex justify-center py-4"><Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /></div>
        ) : tables.length === 0 ? (
          <p className="px-2 py-3 text-xs text-muted-foreground">{t('settings.querySandbox.tablesEmpty')}</p>
        ) : (
          <ul>
            {tables.map((tbl) => (
              <li key={`${tbl.schemaName}.${tbl.name}`}>
                <button
                  type="button"
                  onClick={() => onPick(tbl)}
                  title={`${tbl.schemaName}.${tbl.name}`}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-secondary"
                >
                  {tbl.type === 'view'
                    ? <Eye className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    : <Table2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                  <span className="truncate font-mono">{tbl.name}</span>
                  {tbl.schemaName !== 'public' && (
                    <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{tbl.schemaName}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {hasConnection && tables.length > 0 && (
        <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted-foreground">
          {t('settings.querySandbox.tablesHint')}
        </p>
      )}
    </aside>
  );
}

function ResultPanel({ result }: { result: QueryResult }) {
  const { t } = useTranslation();
  return (
    <section className="mt-4 rounded-lg border border-border bg-card shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 text-xs text-muted-foreground">
        <span>{t('settings.querySandbox.rowsReturned', { count: result.rowCount })}</span>
        {result.command && <span className="font-mono">{result.command}</span>}
        <span>{result.durationMs} ms</span>
        {result.readOnly && <span className="rounded bg-secondary px-2 py-0.5">{t('settings.dbConnections.readOnly')}</span>}
        {result.truncated && (
          <span className="rounded bg-amber-500/10 px-2 py-0.5 text-amber-600">{t('settings.querySandbox.truncated')}</span>
        )}
      </div>
      {result.columns.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">{t('settings.querySandbox.noRows')}</p>
      ) : (
        <div className="max-h-[55vh] overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 border-b border-border bg-card text-muted-foreground">
              <tr>{result.columns.map((col) => <th key={col} className="px-3 py-2 font-medium">{col}</th>)}</tr>
            </thead>
            <tbody className="font-mono">
              {result.rows.map((row, i) => (
                <tr key={i} className="border-b border-border last:border-0">
                  {row.map((cell, j) => (
                    <td key={j} className="max-w-xs truncate px-3 py-1.5" title={fmt(cell)}>{fmt(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function extractError(err: unknown): string {
  if (err && typeof err === 'object' && 'response' in err) {
    const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    if (detail) return detail;
  }
  return err instanceof Error ? err.message : 'Error';
}

export default QuerySandbox;
''',
}


_DB_CONNECTIONS_APP_ROUTES = '''\
            <Route
              path="/settings/db-connections"
              element={
                <RequirePermission perm="db_connections.read">
                  <SettingsDbConnections />
                </RequirePermission>
              }
            />
            <Route
              path="/settings/query-sandbox"
              element={
                <RequirePermission perm="db_connections.query">
                  <SettingsQuerySandbox />
                </RequirePermission>
              }
            />
'''
_DB_CONNECTIONS_I18N_TH = '''\
    "dbConnections": {
      "title": "ฐานข้อมูล (Database Connections)",
      "description": "จัดการการเชื่อมต่อ PostgreSQL ได้หลายฐานผ่าน UI — ไม่ต้องแก้โค้ด",
      "new": "เพิ่มการเชื่อมต่อ",
      "edit": "แก้ไขการเชื่อมต่อ",
      "test": "ทดสอบการเชื่อมต่อ",
      "disabled": "ปิดใช้งาน",
      "readOnly": "อ่านอย่างเดียว",
      "readWrite": "อ่าน-เขียน",
      "ok": "ปกติ",
      "failed": "ล้มเหลว",
      "confirmDelete": "ลบการเชื่อมต่อ {{name}}?",
      "passwordEditHint": "เว้นว่างไว้ = ใช้รหัสผ่านเดิม",
      "allowWriteWarning": "เปิดโหมดเขียน: Query Sandbox จะสามารถรัน INSERT/UPDATE/DELETE/DDL บนฐานข้อมูลจริงได้ ใช้ด้วยความระมัดระวัง",
      "fields": {
        "name": "ชื่อ",
        "description": "คำอธิบาย",
        "target": "ปลายทาง",
        "mode": "โหมด",
        "lastTest": "ผลทดสอบล่าสุด",
        "host": "Host",
        "port": "Port",
        "database": "Database",
        "username": "Username",
        "password": "Password",
        "sslMode": "SSL Mode",
        "active": "เปิดใช้งาน",
        "allowWrite": "อนุญาตให้เขียน (read-write) ใน Sandbox"
      }
    },
    "querySandbox": {
      "title": "Query Sandbox",
      "description": "ทดสอบรัน SQL กับการเชื่อมต่อที่เลือก — read-only โดยค่าเริ่มต้น",
      "connection": "การเชื่อมต่อ",
      "selectConnection": "— เลือกการเชื่อมต่อ —",
      "limit": "จำกัดจำนวนแถว",
      "allowWrite": "รันแบบเขียนได้ (read-write)",
      "writeLocked": "การเชื่อมต่อนี้ตั้งเป็น read-only",
      "writeWarning": "โหมดเขียนเปิดอยู่ — คำสั่งจะเปลี่ยนข้อมูลจริงในฐานข้อมูล",
      "run": "รัน Query",
      "rowsReturned": "{{count}} แถว",
      "truncated": "ผลถูกตัด (เกิน limit)",
      "noRows": "ไม่มีแถวข้อมูล (คำสั่งสำเร็จ)",
      "tables": "ตาราง",
      "tablesSelectFirst": "เลือก connection เพื่อดูรายชื่อตาราง",
      "tablesEmpty": "ไม่พบตาราง",
      "tablesHint": "คลิกชื่อตารางเพื่อแทรกลงในช่อง SQL"
    },
'''
_DB_CONNECTIONS_I18N_EN = '''\
    "dbConnections": {
      "title": "Database Connections",
      "description": "Manage multiple PostgreSQL connections via the UI — no code change",
      "new": "Add connection",
      "edit": "Edit connection",
      "test": "Test connection",
      "disabled": "disabled",
      "readOnly": "Read-only",
      "readWrite": "Read-write",
      "ok": "OK",
      "failed": "Failed",
      "confirmDelete": "Delete connection {{name}}?",
      "passwordEditHint": "Leave blank to keep the current password",
      "allowWriteWarning": "Write mode enabled: the Query Sandbox can run INSERT/UPDATE/DELETE/DDL against the live database. Use with care.",
      "fields": {
        "name": "Name",
        "description": "Description",
        "target": "Target",
        "mode": "Mode",
        "lastTest": "Last test",
        "host": "Host",
        "port": "Port",
        "database": "Database",
        "username": "Username",
        "password": "Password",
        "sslMode": "SSL Mode",
        "active": "Active",
        "allowWrite": "Allow writes (read-write) in Sandbox"
      }
    },
    "querySandbox": {
      "title": "Query Sandbox",
      "description": "Test SQL against a selected connection — read-only by default",
      "connection": "Connection",
      "selectConnection": "— Select a connection —",
      "limit": "Row limit",
      "allowWrite": "Run in write mode (read-write)",
      "writeLocked": "This connection is read-only",
      "writeWarning": "Write mode is on — statements will change live data",
      "run": "Run query",
      "rowsReturned": "{{count}} rows",
      "truncated": "Result truncated (over limit)",
      "noRows": "No rows (statement succeeded)",
      "tables": "Tables",
      "tablesSelectFirst": "Select a connection to browse its tables",
      "tablesEmpty": "No tables found",
      "tablesHint": "Click a table to insert it into the editor"
    },
'''
_MIGRATION_TEMPLATE = '''\
"""db_connections — admin-managed external PostgreSQL connection targets

Revision ID: 0011_db_connections
Revises: 0010_normalize_user_emails
Create Date: 2026-01-02 07:00:00.000000
"""
from __future__ import annotations

from alembic import op


revision = "0011_db_connections"
down_revision = "__DOWN_REVISION__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE db_connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            host VARCHAR(255) NOT NULL,
            port INTEGER NOT NULL DEFAULT 5432,
            database VARCHAR(128) NOT NULL,
            username VARCHAR(128) NOT NULL,
            password_encrypted TEXT NOT NULL,
            ssl_mode VARCHAR(20) NOT NULL DEFAULT 'prefer',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            allow_write BOOLEAN NOT NULL DEFAULT FALSE,
            last_tested_at TIMESTAMPTZ,
            last_test_status VARCHAR(20),
            created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_db_connections_name ON db_connections (name);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS db_connections CASCADE")
'''


def write_module_files() -> None:
    for rel, body in MODULE_FILES.items():
        write(rel, body)


def patch_config() -> None:
    path = "backend/app/core/config.py"
    text = read(path)
    text = replace_once(
        text,
        "    DB_POOL_SIZE: int = 10\n    DB_MAX_OVERFLOW: int = 20\n",
        "    DB_POOL_SIZE: int = 10\n    DB_MAX_OVERFLOW: int = 20\n"
        "    # Database Connections module — opt-in. When false (default) the router\n"
        "    # is not mounted and seed.py skips its permissions / menus / settings.\n"
        "    FEATURE_DB_CONNECTIONS: bool = False\n"
        "    # Fernet key — required only when FEATURE_DB_CONNECTIONS=true.\n"
        "    DB_CONNECTIONS_ENCRYPTION_KEY: str = \"\"\n",
        path,
    )
    write(path, text)


def patch_permissions() -> None:
    path = "backend/app/auth/permissions.py"
    text = read(path)
    text = replace_once(
        text,
        "    # App settings\n    ADMIN_SETTINGS_UPDATE = \"admin_settings.update\"\n",
        "    # App settings\n    ADMIN_SETTINGS_UPDATE = \"admin_settings.update\"\n\n"
        "    # Database Connections module (opt-in — FEATURE_DB_CONNECTIONS).\n"
        "    DB_CONNECTIONS_READ = \"db_connections.read\"\n"
        "    DB_CONNECTIONS_MANAGE = \"db_connections.manage\"\n"
        "    DB_CONNECTIONS_QUERY = \"db_connections.query\"\n",
        path,
    )
    write(path, text)


def patch_users() -> None:
    path = "backend/app/api/v1/users.py"
    text = read(path)
    text = replace_once(
        text,
        "        \"roles.create\", \"roles.update\", \"roles.delete\", \"roles.assign\",\n        \"menus.delete\",\n",
        "        \"roles.create\", \"roles.update\", \"roles.delete\", \"roles.assign\",\n        \"menus.delete\",\n"
        "        # Database Connections module (opt-in) — stored external-DB credentials\n"
        "        # + arbitrary SQL execution. super_admin only; never per-user grantable.\n"
        "        \"db_connections.read\", \"db_connections.manage\", \"db_connections.query\",\n",
        path,
    )
    write(path, text)


def patch_seed() -> None:
    path = "backend/app/seed.py"
    text = read(path)
    text = replace_once(
        text,
        "from app.auth.password import PasswordPolicyError, hash_password\n",
        "from app.auth.password import PasswordPolicyError, hash_password\nfrom app.core.config import get_settings\n",
        path,
    )
    constants = (
        "\n\n# --- Optional module: Database Connections + Query Sandbox ---------------\n"
        "# Gated by FEATURE_DB_CONNECTIONS (config.py). Seeded only when the flag is on.\n"
        "_DB_CONNECTIONS_PERMISSIONS: list[tuple[str, str, str, bool]] = [\n"
        "    (\"db_connections.read\",         \"ดู Database Connections\", \"database\", True),\n"
        "    (\"db_connections.manage\",       \"จัดการ DB Connections\",   \"database\", False),\n"
        "    (\"db_connections.query\",        \"รัน Query Sandbox\",       \"database\", False),\n"
        "]\n"
        "_DB_CONNECTIONS_MENUS: list[tuple[str, str, str, str | None, str, str | None, int, str]] = [\n"
        "    (\"settings.db_connections\", \"ฐานข้อมูล\", \"Database Connections\", \"Database\", \"/settings/db-connections\", \"settings\", 60, \"db_connections.read\"),\n"
        "    (\"settings.query_sandbox\", \"Query Sandbox\", \"Query Sandbox\", \"Terminal\", \"/settings/query-sandbox\", \"settings\", 65, \"db_connections.query\"),\n"
        "]\n"
        "_DB_CONNECTIONS_SETTINGS: list[tuple[str, object, str, str]] = [\n"
        "    (\"db_sandbox.statement_timeout_ms\", 30000, \"integer\", \"database\"),\n"
        "    (\"db_sandbox.max_rows\",             1000,  \"integer\", \"database\"),\n"
        "]\n\n\ndef _scope() -> str:"
    )
    text = replace_once(text, "\n\ndef _scope() -> str:", constants, path)
    # _seed_permissions
    text = replace_once(
        text,
        "    out: dict[str, Permission] = {}\n    for key, display_name, category, is_menu in DEFAULT_PERMISSIONS:\n",
        "    out: dict[str, Permission] = {}\n    permissions = list(DEFAULT_PERMISSIONS)\n"
        "    if get_settings().FEATURE_DB_CONNECTIONS:\n        permissions += _DB_CONNECTIONS_PERMISSIONS\n"
        "    for key, display_name, category, is_menu in permissions:\n",
        path,
    )
    # _seed_menus
    text = replace_once(
        text,
        "    by_key: dict[str, MenuItem] = {}\n    # First pass — items without parent.\n    for key, label_th, label_en, icon, path, parent_key, order, perm_key in DEFAULT_MENUS:\n",
        "    by_key: dict[str, MenuItem] = {}\n    menus = list(DEFAULT_MENUS)\n"
        "    if get_settings().FEATURE_DB_CONNECTIONS:\n        menus += _DB_CONNECTIONS_MENUS\n"
        "    # First pass — items without parent.\n    for key, label_th, label_en, icon, path, parent_key, order, perm_key in menus:\n",
        path,
    )
    text = replace_once(
        text,
        "    for key, *_rest, parent_key, _order, _perm_key in DEFAULT_MENUS:\n",
        "    for key, *_rest, parent_key, _order, _perm_key in menus:\n",
        path,
    )
    # _seed_app_settings
    text = replace_once(
        text,
        "        (\"notifications.email.admin_recipients\", [],    \"json\",    \"notifications\"),\n    ]\n",
        "        (\"notifications.email.admin_recipients\", [],    \"json\",    \"notifications\"),\n    ]\n"
        "    if get_settings().FEATURE_DB_CONNECTIONS:\n        defaults += _DB_CONNECTIONS_SETTINGS\n",
        path,
    )
    write(path, text)


def patch_installed_routers() -> None:
    path = "backend/app/api/v1/installed_routers.py"
    text = read(path)
    if "db_connections_router" in text:
        return
    text = text.replace(
        "from fastapi import APIRouter\n",
        "from fastapi import APIRouter\n\n"
        "from app.api.v1.db_connections import router as db_connections_router\n"
        "from app.core.config import get_settings\n",
        1,
    )
    text = text.rstrip("\n") + (
        "\n\n# Database Connections module — mounted only when the runtime flag is on.\n"
        "if get_settings().FEATURE_DB_CONNECTIONS:\n"
        "    ROUTERS.append((db_connections_router, \"/api/v1/db-connections\"))\n"
    )
    write(path, text)


def patch_app_tsx() -> None:
    path = "frontend/src/App.tsx"
    text = read(path)
    if "SettingsDbConnections" in text:
        return
    text = replace_once(
        text,
        "import { MODULE_ROUTES } from './routes';\n",
        "import { DatabaseConnections as SettingsDbConnections } from './pages/settings/DatabaseConnections';\n"
        "import { QuerySandbox as SettingsQuerySandbox } from './pages/settings/QuerySandbox';\n"
        "import { MODULE_ROUTES } from './routes';\n",
        path,
    )
    text = replace_once(
        text,
        "            {MODULE_ROUTES}\n",
        _DB_CONNECTIONS_APP_ROUTES + "            {MODULE_ROUTES}\n",
        path,
    )
    write(path, text)


def patch_settings_index() -> None:
    path = "frontend/src/pages/SettingsIndex.tsx"
    text = read(path)
    if "db-connections" in text:
        return
    text = replace_once(
        text,
        "import { Activity, KeyRound, ListChecks, Menu as MenuIcon, ScrollText, ShieldCheck, Users } from 'lucide-react';",
        "import { Activity, Database, KeyRound, ListChecks, Menu as MenuIcon, ScrollText, ShieldCheck, Terminal, Users } from 'lucide-react';",
        path,
    )
    text = replace_once(
        text,
        "  { to: '/settings/activity-logs', perm: 'activity_logs.read', titleKey: 'settings.activityLogs.title', descriptionKey: 'settings.activityLogs.description', Icon: Activity },\n",
        "  { to: '/settings/activity-logs', perm: 'activity_logs.read', titleKey: 'settings.activityLogs.title', descriptionKey: 'settings.activityLogs.description', Icon: Activity },\n"
        "  { to: '/settings/db-connections', perm: 'db_connections.read', titleKey: 'settings.dbConnections.title', descriptionKey: 'settings.dbConnections.description', Icon: Database },\n"
        "  { to: '/settings/query-sandbox', perm: 'db_connections.query', titleKey: 'settings.querySandbox.title', descriptionKey: 'settings.querySandbox.description', Icon: Terminal },\n",
        path,
    )
    write(path, text)


def patch_i18n() -> None:
    for loc, frag in (("th", _DB_CONNECTIONS_I18N_TH), ("en", _DB_CONNECTIONS_I18N_EN)):
        path = f"frontend/src/i18n/locales/{loc}.json"
        if not (ROOT / path).exists():
            continue
        text = read(path)
        if '"dbConnections"' in text:
            continue
        text = replace_once(text, '    "activityLogs": {', frag + '    "activityLogs": {', path)
        write(path, text)


def patch_env_example() -> None:
    path = "backend/.env.example"
    if not (ROOT / path).exists():
        return
    text = read(path)
    if "FEATURE_DB_CONNECTIONS" in text:
        return
    block = (
        "\n# Feature Modules (opt-in — default off; see docs/patterns/db-connections.md)\n"
        "FEATURE_DB_CONNECTIONS=false\n"
        "# Required ONLY when FEATURE_DB_CONNECTIONS=true. Fernet key (url-safe base64,\n"
        "# 32 bytes) — generate with:\n"
        "#   python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        "DB_CONNECTIONS_ENCRYPTION_KEY=\n"
    )
    write(path, text.rstrip("\n") + "\n" + block)


def set_project_config_flag() -> None:
    path = "project.config"
    if not (ROOT / path).exists():
        return
    text = read(path)
    if "FEATURE_DB_CONNECTIONS" in text:
        return
    write(path, text.rstrip("\n") + "\n\n# Feature Modules (opt-in)\nFEATURE_DB_CONNECTIONS=true\n")


def add_migration() -> None:
    path = "backend/alembic/versions/2026_01_02_0700-0011_db_connections.py"
    if (ROOT / path).exists():
        return
    heads = alembic_heads(exclude_revisions={"0011_db_connections"})
    if len(heads) != 1:
        raise RuntimeError(
            f"Expected exactly one Alembic head before adding migration 0011, found {heads}. "
            "Resolve existing migration heads first."
        )
    write(path, _MIGRATION_TEMPLATE.replace("__DOWN_REVISION__", heads[0]))


def main() -> int:
    try:
        write_module_files()
        patch_config()
        patch_permissions()
        patch_users()
        patch_seed()
        patch_installed_routers()
        patch_app_tsx()
        patch_settings_index()
        patch_i18n()
        patch_env_example()
        set_project_config_flag()
        add_migration()
    except Exception as exc:
        print(f"Patch failed: {exc}", file=sys.stderr)
        return 1

    if CHANGED:
        print("Patched files:")
        for path in CHANGED:
            print(f"  - {path}")
        print("\nNext steps:")
        print("  1. Generate a Fernet key and set DB_CONNECTIONS_ENCRYPTION_KEY in backend/.env:")
        print("     python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        print("  2. cd backend && alembic upgrade head")
        print("  3. python -m app.seed   (seeds the 3 permissions + 2 menus + sandbox limits)")
        print("  4. restart backend + frontend")
        print("\nSECURITY: get SECURITY_APPROVER sign-off — see docs/patterns/db-connections.md")
    else:
        print("No changes needed; project already has this module.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
