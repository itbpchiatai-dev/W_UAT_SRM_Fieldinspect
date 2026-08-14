"""JWT encode/decode — HS256 access + refresh tokens.

Single secret (`JWT_SECRET_KEY`) signs both. Access tokens carry
`auth_provider` so middleware can short-circuit provider-specific
checks without hitting the DB.

Refresh tokens carry a `jti` (UUID4) so the auth router can revoke
specific tokens server-side via the `revoked_tokens` table — closes
Deep-Audit HIGH-3 (stolen refresh token could otherwise replay until
its 7-day natural expiry).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.core.config import get_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(*, subject: str, token_type: str, expires_delta: timedelta,
            auth_provider: str, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "type": token_type,
        "auth_provider": auth_provider,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def encode_access_token(*, subject: str, auth_provider: str,
                        extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    return _encode(
        subject=subject,
        token_type="access",
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        auth_provider=auth_provider,
        extra=extra,
    )


def encode_refresh_token(*, subject: str, auth_provider: str) -> str:
    """Mint a refresh token with a fresh `jti`.

    The caller is expected to keep / pass the jti where revocation
    needs to happen (logout, rotation, password change). The token
    itself is opaque to the client — they just shove it back in the
    httponly cookie.
    """
    settings = get_settings()
    jti = uuid.uuid4().hex
    return _encode(
        subject=subject,
        token_type="refresh",
        expires_delta=timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        auth_provider=auth_provider,
        extra={"jti": jti},
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode + validate. Raises JWTError on bad signature / expired token."""
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


__all__ = ["encode_access_token", "encode_refresh_token", "decode_token", "JWTError"]
