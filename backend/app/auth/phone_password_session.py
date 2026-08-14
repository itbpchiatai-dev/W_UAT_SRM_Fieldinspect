"""Phone+password access session tokens (round 8-9C).

The password-verified sibling of app.auth.phone_access_session. Minted ONLY by
POST /public/inspection-access/lookup after every returned row's stored bcrypt
hash was verified against the submitted password — never from a digest match
alone.

Why a SEPARATE token type rather than an extra claim on the legacy one: a
legacy phone_access_session proves only "this caller knows a phone number".
If enforcement started reading a flag off that same type, any token minted
before the flag flipped — or by a code path that skipped verification — would
silently count as password-verified. A distinct `type` makes that
unrepresentable: decode_phone_access_session_token rejects this one and
decode_phone_password_session_token rejects that one, both with the same
generic error.

Claims are the MINIMUM needed to re-authorize on a later call:
    grants = [{a: access_phone_id, c: credential_id, v: credential_version}]
There is deliberately NO phone, no phone fingerprint, no password, no bcrypt
hash, no blind-index digest, and no plot/supplier display data. Everything
human-meaningful is re-derived from those ids under RLS on each call, so a
deactivated row or a bumped credential version simply stops working.

credential_version is what makes a password CHANGE invalidate old sessions:
/plots, /select-plot and record-create all compare the grant's version against
the live row and drop any grant that no longer matches.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from app.auth.phone_access_session import MAX_ACCESS_PHONE_IDS
from app.core.config import get_settings

TOKEN_TYPE = "phone_password_access_session"
TOKEN_VERSION = 1
EXPIRE_HOURS = 8

# Same cap as the phone-only token: a phone+password pair realistically maps to
# a handful of plots, and an unbounded list would be both a crafted-token
# amplification vector and an impractically large body field.
MAX_GRANTS = MAX_ACCESS_PHONE_IDS


class PhonePasswordTokenError(Exception):
    """Any structural problem decoding a password-verified token (bad
    signature, expiry, wrong type/version, missing/malformed/empty/oversized/
    duplicated grants). Every public endpoint maps ALL of these to the SAME
    generic failure — the caller never learns which check failed."""


@dataclass(frozen=True)
class CredentialGrant:
    """One plot the caller proved a password for. Carries no secret."""

    access_phone_id: UUID
    credential_id: UUID
    credential_version: int


def encode_phone_password_session_token(
    *, grants: list[CredentialGrant]
) -> tuple[str, int]:
    """Mint a password-verified session token. Returns (token, expires_in).

    Raises ValueError for an empty list, more than MAX_GRANTS entries, or a
    duplicate access_phone_id — the caller (lookup) must never mint a token
    with no verified rows (that is a generic 404 there) nor an ambiguous one.
    """
    if not grants:
        raise ValueError("grants must not be empty")
    if len(grants) > MAX_GRANTS:
        raise ValueError(f"grants exceeds {MAX_GRANTS}")
    if len({g.access_phone_id for g in grants}) != len(grants):
        raise ValueError("duplicate access_phone_id in grants")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(hours=EXPIRE_HOURS)
    payload: dict[str, Any] = {
        "type": TOKEN_TYPE,
        "ver": TOKEN_VERSION,
        "grants": [
            {
                "a": str(g.access_phone_id),
                "c": str(g.credential_id),
                "v": int(g.credential_version),
            }
            for g in grants
        ],
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_phone_password_session_token(token: str) -> list[CredentialGrant]:
    """Decode + fully validate, returning the grants. Raises
    PhonePasswordTokenError on ANY problem, so the endpoint can map every
    failure to one generic response.

    Validated: signature, expiry, `type`, `ver`, grants present/non-empty/
    within MAX_GRANTS, every entry a dict with parseable UUIDs, a version that
    is a real non-negative int (never a bool, never a float, never a string),
    and no duplicate access_phone_id.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise PhonePasswordTokenError("bad token") from exc

    if claims.get("type") != TOKEN_TYPE:
        raise PhonePasswordTokenError("wrong token type")
    if claims.get("ver") != TOKEN_VERSION:
        raise PhonePasswordTokenError("wrong token version")

    raw = claims.get("grants")
    if not isinstance(raw, list) or not raw:
        raise PhonePasswordTokenError("missing grants")
    if len(raw) > MAX_GRANTS:
        raise PhonePasswordTokenError("too many grants")

    grants: list[CredentialGrant] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PhonePasswordTokenError("malformed grant")
        version = item.get("v")
        # bool is an int subclass — reject it explicitly.
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            raise PhonePasswordTokenError("malformed grant version")
        try:
            grants.append(
                CredentialGrant(
                    access_phone_id=UUID(str(item.get("a"))),
                    credential_id=UUID(str(item.get("c"))),
                    credential_version=version,
                )
            )
        except (ValueError, TypeError) as exc:
            raise PhonePasswordTokenError("malformed grant id") from exc

    if len({g.access_phone_id for g in grants}) != len(grants):
        raise PhonePasswordTokenError("duplicate grant")
    return grants


__all__ = [
    "TOKEN_TYPE",
    "TOKEN_VERSION",
    "EXPIRE_HOURS",
    "MAX_GRANTS",
    "CredentialGrant",
    "PhonePasswordTokenError",
    "encode_phone_password_session_token",
    "decode_phone_password_session_token",
]
