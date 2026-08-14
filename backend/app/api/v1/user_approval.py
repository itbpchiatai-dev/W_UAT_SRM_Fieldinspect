"""Public token-gated approve/reject endpoints.

Three routes — none require auth (the URL token is the capability):
  GET    /                  → resolve token, return user + token status
  POST   /{token}/approve   → approve user, send approval reply email
  POST   /{token}/reject    → reject user, send rejection reply email

Token lifecycle:
  1. users.py.create_user(require_approval=True) generates a raw token,
     stores sha256(token) on the user row, embeds the raw token in the
     admin notification URLs.
  2. First admin to POST approve/reject consumes the row — the hash is
     cleared so subsequent visits get token_expired. Mirrors the
     "ลิงก์ใช้ได้ครั้งเดียว" copy in the email.
  3. Tokens auto-expire per approval_token_expires_at (default 7 days
     from create — see app_settings.notifications.approval_link_ttl_days).

Note: no `from __future__ import annotations` here — slowapi\'s
@limiter.limit decorator + PEP-563 string annotations + FastAPI\'s
forward-ref resolver combine into a PydanticUndefinedAnnotation at
boot. See app/api/v1/auth.py module docstring for the long version.
"""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import CamelBaseModel
from app.services.loggers.activity_logger import ActivityLogger
from app.services.notifications import (
    notify_user_approval_granted, notify_user_rejection,
)

router = APIRouter(tags=["user-approval"])


class PendingUserPublic(CamelBaseModel):
    """Minimal user info safe to expose to anyone holding the token."""
    email: str
    full_name: str
    requested_at: datetime
    expires_at: datetime


class TokenStatus(CamelBaseModel):
    """Wrapper so the SPA can render `expired` / `consumed` states without
    parsing 4xx errors."""
    status: str          # "valid" | "expired" | "consumed" | "not_found"
    pending: PendingUserPublic | None = None


class ApprovalDecision(CamelBaseModel):
    """Admin\'s editable reply text. Empty → use server default template."""
    reply_message: str | None = None


class RejectionDecision(CamelBaseModel):
    reply_message: str | None = None
    reason: str | None = None


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _load_by_token(db: AsyncSession, token: str) -> tuple[User | None, str]:
    """Return (user, status). status = 'valid' | 'expired' | 'consumed' | 'not_found'."""
    if not token or len(token) < 16:
        return None, "not_found"
    token_hash = _hash_token(token)
    result = await db.execute(
        select(User).where(User.approval_token_hash == token_hash)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Two cases collapse here: never-existed and already-consumed.
        # We surface "consumed" since that\'s the friendlier copy when an
        # admin sibling beat them to it — the alternative (revealing
        # never-existed vs consumed) is a small enumeration leak.
        return None, "consumed"
    if user.approval_token_expires_at is None:
        return None, "consumed"
    now = datetime.now(timezone.utc)
    if user.approval_token_expires_at < now:
        return user, "expired"
    if user.is_approved or user.is_rejected:
        return user, "consumed"
    return user, "valid"


def _consume(user: User) -> None:
    """Clear the token so the row is single-use."""
    user.approval_token_hash = None
    user.approval_token_expires_at = None


@router.get("/{token}", response_model=TokenStatus)
@limiter.limit("30/minute")  # HIGH-1: token-scan defence
async def resolve_token(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenStatus:
    user, st = await _load_by_token(db, token)
    if st != "valid" or user is None:
        return TokenStatus(status=st)
    return TokenStatus(
        status="valid",
        pending=PendingUserPublic(
            email=user.email,
            full_name=user.full_name,
            requested_at=user.created_at,
            expires_at=user.approval_token_expires_at,
        ),
    )


@router.post("/{token}/approve", response_model=TokenStatus)
@limiter.limit("30/minute")  # HIGH-1: token-scan defence
async def approve_via_token(
    token: str,
    payload: ApprovalDecision,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenStatus:
    user, st = await _load_by_token(db, token)
    if st != "valid" or user is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=st)

    user.is_approved = True
    _consume(user)

    audit = ActivityLogger(db)
    await audit.log(
        action="user.approved_via_link", action_type="update",
        resource_type="user", resource_id=str(user.id),
        request=request, risk_level="medium",
        metadata={"channel": "approval_link"},
    )

    await notify_user_approval_granted(
        db, user_email=user.email, user_name=user.full_name,
        custom_message=payload.reply_message,
    )
    return TokenStatus(status="approved")


@router.post("/{token}/reject", response_model=TokenStatus)
@limiter.limit("30/minute")  # HIGH-1: token-scan defence
async def reject_via_token(
    token: str,
    payload: RejectionDecision,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenStatus:
    user, st = await _load_by_token(db, token)
    if st != "valid" or user is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=st)

    user.is_rejected = True
    user.is_active = False
    user.rejection_reason = (payload.reason or "").strip()[:500] or None
    _consume(user)

    audit = ActivityLogger(db)
    await audit.log(
        action="user.rejected_via_link", action_type="update",
        resource_type="user", resource_id=str(user.id),
        request=request, risk_level="medium",
        metadata={"channel": "approval_link",
                  "has_reason": bool(user.rejection_reason)},
    )

    await notify_user_rejection(
        db, user_email=user.email, user_name=user.full_name,
        reason=user.rejection_reason,
        custom_message=payload.reply_message,
    )
    return TokenStatus(status="rejected")
