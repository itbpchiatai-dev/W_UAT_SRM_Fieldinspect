"""ActivityLogger — persists user/system events to activity_logs.

Use directly OR wrap mutation endpoints with @audited (app.api.decorators).
PII in metadata is scrubbed before insert. Caller commits the session.

See docs/logging.md §1.3.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import mask_email
from app.db.models.activity_log import ActivityLog

MUTATION_ACTIONS = {"create", "update", "delete"}
SENSITIVE_ACTIONS = {"read_sensitive", "export"}
SECURITY_ACTIONS = {
    "login", "login_failed", "logout", "permission_denied", "role_change",
}

_SENSITIVE_METADATA_KEYS = {
    "password", "password_hash", "token", "access_token", "refresh_token",
    "credit_card", "national_id", "passport", "api_key", "secret",
}


def _scrub_pii(metadata: dict[str, Any]) -> dict[str, Any]:
    scrubbed: dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in _SENSITIVE_METADATA_KEYS:
            scrubbed[key] = "***"
        elif isinstance(value, dict):
            scrubbed[key] = _scrub_pii(value)
        else:
            scrubbed[key] = value
    return scrubbed


class ActivityLogger:
    """Persist mutations, sensitive reads, and security events.

    Two caller shapes are supported and both write the same row:

    - Host shape (preferred for in-tree code): pass ``user=`` (a User
      object) and ``action_type=`` (one of MUTATION/SENSITIVE/SECURITY
      strings). ``request=`` is recommended — when supplied, IP / UA /
      request_id / endpoint / method are captured automatically.
    - Alternate shape (used when the caller only has a UUID — e.g. seed
      scripts or background jobs): pass ``action=`` plus optional
      ``actor_id=`` (UUID), ``target_id=``, ``is_security_event=``,
      ``risk_level=``, ``extra=`` (alias of ``metadata=``).

    If both ``user`` and ``actor_id`` are supplied, ``user`` wins (its
    ``.id`` is used and ``.email`` is masked into ``user_email_masked``).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        *,
        action: str,
        user: Any | None = None,
        actor_id: Any | None = None,
        target_id: Any | None = None,
        action_type: str = "other",
        resource_type: str | None = None,
        resource_id: str | None = None,
        is_security_event: bool | None = None,
        risk_level: str = "low",
        metadata: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        request: Request | None = None,
        http_status: int | None = None,
    ) -> None:

        # Actor resolution — user wins over actor_id when both supplied.
        if user is not None:
            resolved_user_id = getattr(user, "id", None)
            resolved_email_masked = (
                mask_email(user.email) if getattr(user, "email", None) else None
            )
        else:
            resolved_user_id = actor_id
            resolved_email_masked = None

        # Module callers pass `extra`; host callers pass `metadata`. Treat
        # them as the same slot — if both arrive, `extra` keys override.
        merged_metadata: dict[str, Any] = {}
        if metadata:
            merged_metadata.update(metadata)
        if extra:
            merged_metadata.update(extra)
        if target_id is not None and "target_id" not in merged_metadata:
            merged_metadata["target_id"] = str(target_id)

        # is_security_event: explicit override wins; otherwise derive from
        # the host-shape action_type bucket.
        if is_security_event is None:
            resolved_security = action_type in SECURITY_ACTIONS
        else:
            resolved_security = is_security_event

        entry = ActivityLog(
            id=uuid4(),
            user_id=resolved_user_id,
            user_email_masked=resolved_email_masked,
            action_type=action_type,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            is_mutation=action_type in MUTATION_ACTIONS,
            is_sensitive_read=action_type in SENSITIVE_ACTIONS,
            is_security_event=resolved_security,
            risk_level=risk_level,
            ip_address=(
                request.client.host if request and request.client else None
            ),
            user_agent=(
                request.headers.get("user-agent", "")[:500] if request else None
            ),
            request_id=request.headers.get("x-request-id") if request else None,
            endpoint=str(request.url.path) if request else None,
            http_method=request.method if request else None,
            http_status=http_status,
            extra_metadata=_scrub_pii(merged_metadata),
        )
        self.db.add(entry)
        # Caller commits.
