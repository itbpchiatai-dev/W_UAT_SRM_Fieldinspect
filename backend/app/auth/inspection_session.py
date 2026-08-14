"""Inspection-session tokens — short-lived JWTs issued to an unauthenticated
field assistant after they pick a plot via the phone-access flow's
POST /api/v1/public/inspection-access/select-plot (round 8-3G: this is now
the ONLY minter — the legacy inspection-code verify endpoint that used to
mint this same token type without a phone binding was retired that round).

NOT a login token. `type` is "inspection_session", never "access" — the
existing get_current_user (app/auth/dependencies.py) only accepts
type == "access" and will reject one of these with 401 "Wrong token type"
even though it's signed with the same JWT_SECRET_KEY. Consumed by
POST /api/v1/public/records (and its with-photos variant) to create exactly
one record for the plot/supplier/active-cycle it names — see
app/api/v1/public_records.py's _verify_and_resolve / _finish_creating_record,
whose _extract_phone_binding requires every token to carry
plot_access_phone_id + inspector_type (no legacy fallback).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from app.core.config import get_settings
from app.db.models.record import INSPECTOR_TYPES

TOKEN_TYPE = "inspection_session"
EXPIRE_MINUTES = 30


def encode_inspection_session_token(
    *,
    plot_id: UUID,
    supplier_id: UUID,
    plot_cycle_id: UUID,
    plot_access_phone_id: UUID,
    inspector_type: str,
    plot_access_credential_id: UUID | None = None,
    plot_access_credential_version: int | None = None,
) -> tuple[str, int]:
    """Mint a token scoped to one plot/supplier/active-cycle. Returns
    (token, expires_in_seconds).

    Round 8-0.6: the token is now bound to the plot's ACTIVE CYCLE at mint
    time (plot_cycle_id), not just the plot/supplier. Record creation
    re-resolves the plot's current active cycle and rejects a token whose
    plot_cycle_id no longer matches — so a token verified during cycle 1
    can't submit into cycle 2 after a rollover (see
    public_records._verify_and_resolve). Tokens minted before this round
    carry no plot_cycle_id claim and are rejected fail-closed there.

    Round 8-3B: phone-bound claims. The token additionally carries
    plot_access_phone_id + inspector_type so record creation can snapshot
    which access phone entered and which inspector type was chosen —
    server-derived, never from the record client body. inspector_type must
    be in the allowlist. The RAW PHONE is NEVER a claim — only the
    access-row id is.

    Round 8-3H: both are now REQUIRED keyword arguments (previously
    optional-but-both-or-neither) — the phone-access select-plot endpoint
    (app/api/v1/public_inspection_access.py) has been this function's ONLY
    production caller since round 8-3G's retirement of the legacy
    inspection-code verify endpoint that used to call this with neither.
    There is no longer any valid reason to mint a token without a phone
    binding, so the type signature now makes that unrepresentable rather
    than relying on a runtime check.
    """
    if inspector_type not in INSPECTOR_TYPES:
        raise ValueError(f"invalid inspector_type: {inspector_type!r}")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "type": TOKEN_TYPE,
        "plot_id": str(plot_id),
        "supplier_id": str(supplier_id),
        "plot_cycle_id": str(plot_cycle_id),
        "plot_access_phone_id": str(plot_access_phone_id),
        "inspector_type": inspector_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    # Round 8-9C — plot-password binding. ADDITIVE and omitted entirely when
    # the caller passes neither, so a token minted while
    # PUBLIC_PLOT_PASSWORD_ENFORCEMENT is false has exactly the round-8-3B
    # shape and every existing consumer/test is unaffected.
    #
    # When present, these say WHICH credential (and which VERSION of it) the
    # holder proved. public_records re-checks both under the Plot lock, so a
    # password changed between select-plot and submit invalidates the token.
    # The password, its hash and its blind-index digest are NEVER claims —
    # only the row id and an integer version.
    if plot_access_credential_id is not None and plot_access_credential_version is not None:
        payload["plot_access_credential_id"] = str(plot_access_credential_id)
        payload["plot_access_credential_version"] = int(plot_access_credential_version)
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_inspection_session_token(token: str) -> dict[str, Any]:
    """Decode + validate signature/expiry. Raises JWTError on failure.

    Does NOT check the `type` claim — same split as jwt_service.decode_token,
    which callers (get_current_user, public_records._verify_and_resolve)
    check themselves.
    """
    settings = get_settings()
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


__all__ = [
    "TOKEN_TYPE",
    "EXPIRE_MINUTES",
    "encode_inspection_session_token",
    "decode_inspection_session_token",
    "JWTError",
]
