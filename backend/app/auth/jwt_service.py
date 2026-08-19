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
            auth_provider: str, auth_version: int = 0,
            extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "type": token_type,
        "auth_provider": auth_provider,
        # Round 8-23A — session generation at mint time. Snake_case to match
        # its sibling `auth_provider` (JWT claims here follow the payload's
        # own convention, not the camelCase API-JSON convention). Verified
        # fail-closed against users.auth_version by
        # auth/dependencies.get_current_user and /auth/refresh.
        "auth_version": int(auth_version),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def encode_access_token(*, subject: str, auth_provider: str,
                        auth_version: int = 0,
                        extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    return _encode(
        subject=subject,
        token_type="access",
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        auth_provider=auth_provider,
        auth_version=auth_version,
        extra=extra,
    )


def encode_refresh_token(*, subject: str, auth_provider: str,
                         auth_version: int = 0) -> str:
    """Mint a refresh token with a fresh `jti`.

    The caller is expected to keep / pass the jti where revocation
    needs to happen (logout, rotation, password change). The token
    itself is opaque to the client — they just shove it back in the
    httponly cookie.

    `auth_version` (round 8-23A) is the coarse, user-wide counterpart to
    `jti`: jti revokes ONE token, auth_version invalidates ALL of a
    user's outstanding tokens at once (admin password reset).
    """
    settings = get_settings()
    jti = uuid.uuid4().hex
    return _encode(
        subject=subject,
        token_type="refresh",
        expires_delta=timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        auth_provider=auth_provider,
        auth_version=auth_version,
        extra={"jti": jti},
    )


def token_auth_version(claims: dict[str, Any]) -> int | None:
    """Read the `auth_version` claim, fail-closed (round 8-23A).

    Returns:
      - the int value when the claim is a real integer,
      - 0 when the claim is ABSENT — a pre-8-23A token, which stays valid
        for a user still at auth_version 0 so the rollout needs no forced
        re-login,
      - None when the claim is present but MALFORMED (a string, float,
        bool, null, …). None means "reject": a caller must never coerce a
        junk claim into a number that might happen to match.

    `bool` is excluded explicitly — Python's bool subclasses int, so
    `True == 1` would otherwise let `{"auth_version": true}` satisfy a
    user sitting at version 1.
    """
    if "auth_version" not in claims:
        return 0
    value = claims["auth_version"]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def decode_token(token: str) -> dict[str, Any]:
    """Decode + validate. Raises JWTError on bad signature / expired token."""
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


__all__ = [
    "encode_access_token", "encode_refresh_token", "decode_token",
    "token_auth_version", "JWTError",
]
