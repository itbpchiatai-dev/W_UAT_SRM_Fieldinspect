"""Activity logs — read-only admin view of the audit trail.

Default filter is the security-event subset (login / login_failed /
logout / permission_denied / role_change) because that's what the
"login activity" use case asks for. Pass `securityOnly=false` to see
the full audit stream (create/update/delete/export/read_sensitive too).
Free-text `q` ilikes action/email/IP/resource. Sibling /export.csv
streams matching rows as CSV (capped at EXPORT_HARD_CAP).

No mutation endpoints — the table is append-only via ActivityLogger.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import CurrentUser, require_permission
from app.auth.permissions import PermissionKey
from app.db.models.activity_log import ActivityLog
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import ActivityLogRead
from app.services.loggers.activity_logger import ActivityLogger

router = APIRouter(tags=["activity_logs"])

_LOGIN_ACTION_TYPES = ("login", "login_failed", "logout")
EXPORT_HARD_CAP = 10000


def _apply_filters(stmt, *, action_type, user_id, risk_level,
                   security_only, login_only, q, date_from, date_to):
    if login_only:
        stmt = stmt.where(ActivityLog.action_type.in_(_LOGIN_ACTION_TYPES))
    if security_only:
        stmt = stmt.where(ActivityLog.is_security_event == True)  # noqa: E712
    if action_type:
        stmt = stmt.where(ActivityLog.action_type == action_type)
    if user_id is not None:
        stmt = stmt.where(ActivityLog.user_id == user_id)
    if risk_level:
        stmt = stmt.where(ActivityLog.risk_level == risk_level)
    if date_from:
        stmt = stmt.where(ActivityLog.created_at >= datetime.combine(
            date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(ActivityLog.created_at < datetime.combine(
            date_to + timedelta(days=1), time.min, tzinfo=timezone.utc))
    if q:
        pattern = f"%{q}%"
        # user_email_masked stores "wa***@gmail.com" so an admin typing
        # "wannaphong" finds nothing. Outer-join users so the search
        # actually hits the real email; the display still shows the masked
        # form (we never expose User.email in the response).
        stmt = stmt.outerjoin(User, ActivityLog.user_id == User.id).where(or_(
            ActivityLog.action.ilike(pattern),
            ActivityLog.user_email_masked.ilike(pattern),
            ActivityLog.resource_id.ilike(pattern),
            ActivityLog.ip_address.ilike(pattern),
            User.email.ilike(pattern),
        ))
    return stmt


@router.get("", response_model=list[ActivityLogRead], dependencies=[
    Depends(require_permission(PermissionKey.ACTIVITY_LOGS_READ))
])
async def list_activity_logs(
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    action_type: str | None = None,
    user_id: UUID | None = None,
    risk_level: str | None = None,
    security_only: bool = False,
    login_only: bool = False,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ActivityLogRead]:
    stmt = _apply_filters(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit).offset(offset),
        action_type=action_type, user_id=user_id, risk_level=risk_level,
        security_only=security_only, login_only=login_only, q=q,
        date_from=date_from, date_to=date_to,
    )
    result = await db.execute(stmt)
    return [ActivityLogRead.model_validate(r) for r in result.scalars().all()]


@router.get("/export.csv", dependencies=[
    Depends(require_permission(PermissionKey.ACTIVITY_LOGS_READ))
])
async def export_activity_logs_csv(
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    action_type: str | None = None,
    user_id: UUID | None = None,
    risk_level: str | None = None,
    security_only: bool = False,
    login_only: bool = False,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StreamingResponse:
    """CSV export — same filters as list_activity_logs, no offset/limit
    (capped at EXPORT_HARD_CAP). The export itself is also audited."""
    stmt = _apply_filters(
        select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(EXPORT_HARD_CAP),
        action_type=action_type, user_id=user_id, risk_level=risk_level,
        security_only=security_only, login_only=login_only, q=q,
        date_from=date_from, date_to=date_to,
    )
    result = await db.execute(stmt)
    rows = list(result.scalars())
    await ActivityLogger(db).log(
        action="export.activity_logs",
        action_type="export",
        user=user, request=request, risk_level="medium",
        metadata={
            "row_count": len(rows),
            "filters": {
                "action_type": action_type,
                "user_id": str(user_id) if user_id else None,
                "risk_level": risk_level,
                "security_only": security_only, "login_only": login_only,
                "q": q,
                "date_from": str(date_from) if date_from else None,
                "date_to": str(date_to) if date_to else None,
            },
        },
    )
    await db.commit()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at", "user_email_masked", "action", "action_type",
                     "resource_type", "resource_id", "risk_level", "is_security_event",
                     "ip_address", "http_status"])
    for r in rows:
        writer.writerow([
            str(r.id), r.created_at.isoformat(),
            r.user_email_masked or "",
            r.action, r.action_type,
            r.resource_type or "", r.resource_id or "",
            r.risk_level, r.is_security_event,
            r.ip_address or "", r.http_status if r.http_status is not None else "",
        ])
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="activity-logs-{stamp}.csv"'},
    )
