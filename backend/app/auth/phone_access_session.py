"""Phone-access session tokens — short-lived JWTs issued to an
unauthenticated field visitor after they prove they know a phone number that
is an ACCESS KEY of one or more plots (round 8-3B).

This is the FIRST step of the phone-only public inspection flow:
  1. POST /public/inspection-access/lookup  → mint THIS token (phone_access_session)
  2. POST /public/inspection-access/plots    → list the plots the phone can inspect
  3. POST /public/inspection-access/select-plot → exchange for an
     inspection_session_token bound to one plot/cycle/access-phone/inspectorType

NOT a login token, and NOT an inspection_session token. `type` is
"phone_access_session"; get_current_user only accepts type == "access", so
this can never authenticate a user. It carries ONLY the plot_access_phones row
IDs the phone resolved to — never the phone itself, never phoneLast4, never any
plot/supplier display data or inspectorType. Everything human-meaningful is
re-derived from those row IDs on each subsequent call (so a deactivated access
row simply disappears from the session).

PII: the token deliberately carries no phone in any form (docs/security.md §9).
Same JWT secret/algorithm as every other token (app.core.config), separated by
the `type` claim, not the key.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from app.core.config import get_settings

TOKEN_TYPE = "phone_access_session"
EXPIRE_HOURS = 8
# A phone realistically maps to a handful of plots; cap the claim so a token
# can never carry an unbounded list (defence against a crafted/huge token).
MAX_ACCESS_PHONE_IDS = 200


class PhoneAccessTokenError(Exception):
    """Any structural problem decoding a phone-access token (bad signature,
    expiry, wrong type, missing/malformed/empty/oversized claim). The public
    endpoints map every one of these to the SAME generic 401 — the caller
    never learns which check failed."""


def encode_phone_access_session_token(
    *, access_phone_ids: list[UUID]
) -> tuple[str, int]:
    """Mint a phone-access session token from the plot_access_phones row IDs a
    phone resolved to. Returns (token, expires_in_seconds).

    Raises ValueError if the list is empty or exceeds MAX_ACCESS_PHONE_IDS —
    the caller (lookup endpoint) must never mint a token with no usable access
    rows (that would be a 404 there) or an unbounded list.
    """
    if not access_phone_ids:
        raise ValueError("access_phone_ids must not be empty")
    if len(access_phone_ids) > MAX_ACCESS_PHONE_IDS:
        raise ValueError(f"access_phone_ids exceeds {MAX_ACCESS_PHONE_IDS}")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(hours=EXPIRE_HOURS)
    payload: dict[str, Any] = {
        "type": TOKEN_TYPE,
        "access_phone_ids": [str(i) for i in access_phone_ids],
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_phone_access_session_token(token: str) -> list[UUID]:
    """Decode + fully validate a phone-access token, returning its access-phone
    row IDs as UUIDs. Raises PhoneAccessTokenError on ANY problem (signature,
    expiry, wrong type, missing/empty/oversized/malformed access_phone_ids) —
    one generic failure so the endpoint can map it to a single 401.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise PhoneAccessTokenError("bad token") from exc

    if claims.get("type") != TOKEN_TYPE:
        raise PhoneAccessTokenError("wrong token type")

    raw_ids = claims.get("access_phone_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise PhoneAccessTokenError("missing access_phone_ids")
    if len(raw_ids) > MAX_ACCESS_PHONE_IDS:
        raise PhoneAccessTokenError("too many access_phone_ids")

    ids: list[UUID] = []
    for raw in raw_ids:
        try:
            ids.append(UUID(str(raw)))
        except (ValueError, TypeError) as exc:
            raise PhoneAccessTokenError("malformed access_phone_id") from exc
    return ids


__all__ = [
    "TOKEN_TYPE",
    "EXPIRE_HOURS",
    "MAX_ACCESS_PHONE_IDS",
    "PhoneAccessTokenError",
    "encode_phone_access_session_token",
    "decode_phone_access_session_token",
]
