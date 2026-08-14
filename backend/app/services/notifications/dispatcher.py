"""Notification dispatcher — read settings, pick transport, send.

Why a separate dispatcher: the two trigger points (signup, approval)
shouldn\'t know which channel is active or whether notifications are
enabled at all. They just call notify_*; this module decides.

Failure policy: never raise. If transport fails, log and return — the
underlying business action (user create / approve) already succeeded;
losing a notification is recoverable, a 500 on the API isn\'t.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.app_setting import AppSetting
from app.services.notifications.graph_email import send_html_email
from app.services.notifications.templates import (
    approval_user_th, rejection_user_th, signup_admin_th,
)

log = logging.getLogger(__name__)


async def _settings_lookup(db: AsyncSession, key: str, default: Any) -> Any:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if not row or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return row.value


async def _is_enabled(db: AsyncSession) -> bool:
    return bool(await _settings_lookup(db, "notifications.email.enabled", False))


async def _admin_recipients(db: AsyncSession) -> list[str]:
    raw = await _settings_lookup(db, "notifications.email.admin_recipients", [])
    if isinstance(raw, str):
        # Stored as comma list — split + strip.
        raw = [s.strip() for s in raw.split(",") if s.strip()]
    return [r for r in raw if isinstance(r, str) and "@" in r]


async def notify_admin_new_signup(
    db: AsyncSession, *, user_email: str, user_name: str | None,
    approval_token: str | None = None,
) -> None:
    """Email admins that a new user is pending approval. No-op when disabled.

    `approval_token` (raw, NOT the hash) is embedded into the approve +
    reject URLs so the admin can act with one click. Caller is the user-
    create endpoint, which generates the token and persists the hash.
    """
    if not await _is_enabled(db):
        return
    recipients = await _admin_recipients(db)
    if not recipients:
        log.info("notify_admin_new_signup: enabled but no admin recipients configured")
        return
    settings = get_settings()
    base = settings.AUTH_FRONTEND_BASE_URL.rstrip("/") or "http://localhost:5173"
    if approval_token:
        approve_url = f"{base}/approve/{approval_token}?action=approve"
        reject_url = f"{base}/approve/{approval_token}?action=reject"
    else:
        # Fallback when token generation wasn\'t wired (legacy callers) —
        # admin lands on the user-management page and approves there.
        approve_url = reject_url = f"{base}/settings/users"
    app_name = settings.APP_NAME or "ระบบ"
    subject, html = signup_admin_th(
        user_email=user_email,
        user_name=user_name or "",
        app_name=app_name,
        approve_url=approve_url,
        reject_url=reject_url,
    )
    await send_html_email(recipients, subject, html)


async def notify_user_approval_granted(
    db: AsyncSession, *, user_email: str, user_name: str | None,
    custom_message: str | None = None,
) -> None:
    """Email a user their account was approved. No-op when disabled.

    `custom_message` is the admin\'s edited reply text from the public
    approval page; empty falls back to the default template body.
    """
    if not await _is_enabled(db):
        return
    if not user_email:
        return
    base = get_settings().AUTH_FRONTEND_BASE_URL.rstrip("/") or "http://localhost:5173"
    login_url = f"{base}/login"
    subject, html = approval_user_th(
        user_name=user_name or "",
        login_url=login_url,
        message=custom_message,
    )
    await send_html_email([user_email], subject, html)


async def notify_user_rejection(
    db: AsyncSession, *, user_email: str, user_name: str | None,
    reason: str | None = None,
    custom_message: str | None = None,
) -> None:
    """Email a user their access request was declined. No-op when disabled.

    `reason` is the structured short reason from the admin form (shown as
    a quoted block in the email). `custom_message` is the longer free-
    form reply admin typed on the approval page.
    """
    if not await _is_enabled(db):
        return
    if not user_email:
        return
    subject, html = rejection_user_th(
        user_name=user_name or "",
        reason=reason,
        message=custom_message,
    )
    await send_html_email([user_email], subject, html)
