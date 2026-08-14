"""System logs — read-only admin view of recent job/integration/system events.

Paginated by `limit` + `offset`, ordered newest-first. Filters: optional
`status`, `category`, free-text `q` (ilike on event/error/correlation_id).
Sibling /export.csv endpoint streams matching rows as CSV (capped at
EXPORT_HARD_CAP so admin can't OOM the server with an unbounded query).
No mutation endpoints — the table is append-only via SystemLogger.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.system_log import SystemLog
from app.db.session import get_db
from app.schemas.auth import SystemLogRead
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["system_logs"])

EXPORT_HARD_CAP = 10000


def _apply_filters(stmt, *, status, category, q, date_from, date_to):
    if status:
        stmt = stmt.where(SystemLog.status == status)
    if category:
        stmt = stmt.where(SystemLog.category == category)
    if date_from:
        stmt = stmt.where(SystemLog.created_at >= datetime.combine(
            date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(SystemLog.created_at < datetime.combine(
            date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(
            SystemLog.event.ilike(pattern),
            SystemLog.error_message.ilike(pattern),
            SystemLog.error_type.ilike(pattern),
            SystemLog.correlation_id.ilike(pattern),
        ))
    return stmt


@router.get("", response_model=list[SystemLogRead], dependencies=[
    Depends(require_permission(PermissionKey.SYSTEM_LOGS_READ))
])
async def list_system_logs(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[SystemLogRead]:
    stmt = _apply_filters(
        select(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit).offset(offset),
        status=status, category=category, q=q,
        date_from=date_from, date_to=date_to,
    )
    result = await db.execute(stmt)
    return [SystemLogRead.model_validate(r) for r in result.scalars().all()]


@router.get("/export.csv", dependencies=[
    Depends(require_permission(PermissionKey.SYSTEM_LOGS_READ))
])
async def export_system_logs_csv(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StreamingResponse:
    """CSV export — same filters as list_system_logs, no offset/limit
    (capped at EXPORT_HARD_CAP). Bulk-export is itself an auditable
    action — the row in activity_logs lets future-us answer "who
    downloaded a year of system logs?" without OS-level access tracing."""
    stmt = _apply_filters(
        select(SystemLog).order_by(SystemLog.created_at.desc()).limit(EXPORT_HARD_CAP),
        status=status, category=category, q=q,
        date_from=date_from, date_to=date_to,
    )
    result = await db.execute(stmt)
    rows = list(result.scalars())
    await ActivityLogger(db).log(
        action="export.system_logs",
        action_type="export",
        user=user, request=request, risk_level="medium",
        metadata={
            "row_count": len(rows),
            "filters": {"status": status, "category": category, "q": q,
                        "date_from": str(date_from) if date_from else None,
                        "date_to": str(date_to) if date_to else None},
        },
    )
    await db.commit()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "category", "event", "status",
                     "duration_ms", "error_type", "error_message", "correlation_id"])
    for r in rows:
        writer.writerow([
            str(r.id), r.created_at.isoformat(), r.category, r.event, r.status,
            r.duration_ms if r.duration_ms is not None else "",
            r.error_type or "", r.error_message or "", r.correlation_id or "",
        ])
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="system-logs-{stamp}.csv"'},
    )
