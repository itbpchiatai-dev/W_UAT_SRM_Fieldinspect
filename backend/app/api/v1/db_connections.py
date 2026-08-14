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
