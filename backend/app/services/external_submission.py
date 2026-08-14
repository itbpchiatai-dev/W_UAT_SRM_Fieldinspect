"""External-submission system user — the recorded_by_id every record
created via the public (unauthenticated) inspection-code flow (round 8)
is attributed to. recorded_by_id is NOT NULL and must never be
client-controlled, and there's no real logged-in user in that flow, so
every such record points here instead. This account is never
authenticated as (no password_hash, holds no role, never used in any
permission check) — it exists purely as an audit-trail foreign key.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User

EXTERNAL_FIELD_HELPER_EMAIL = "external-field-helper@system.local"


async def get_external_submission_user(db: AsyncSession) -> User | None:
    result = await db.execute(
        select(User).where(func.lower(User.email) == EXTERNAL_FIELD_HELPER_EMAIL)
    )
    return result.scalar_one_or_none()
