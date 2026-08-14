"""Phone-access session token (round 8-3B) — encode/decode, TTL, wrong type,
malformed/empty/oversized id lists, no-PII claims, and the guarantee it can
never authenticate a user (get_current_user rejects it)."""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

from app.auth.dependencies import get_current_user
from app.auth.phone_access_session import (
    EXPIRE_HOURS,
    MAX_ACCESS_PHONE_IDS,
    TOKEN_TYPE,
    PhoneAccessTokenError,
    decode_phone_access_session_token,
    encode_phone_access_session_token,
)
from app.core.config import get_settings


def _raw(claims: dict) -> str:
    s = get_settings()
    return jwt.encode(claims, s.JWT_SECRET_KEY, algorithm=s.JWT_ALGORITHM)


def test_encode_decode_round_trips_ids() -> None:
    ids = [uuid4(), uuid4(), uuid4()]
    token, expires_in = encode_phone_access_session_token(access_phone_ids=ids)
    assert expires_in == EXPIRE_HOURS * 3600
    assert decode_phone_access_session_token(token) == ids


def test_claims_carry_no_pii() -> None:
    """Only access-row ids — no phone number/last4, no plot/supplier display,
    no inspectorType. (The claim KEY 'access_phone_ids' contains the word
    'phone', but its VALUES are UUIDs, so we check values/keys precisely rather
    than a naive substring.)"""
    import re

    ids = [uuid4()]
    token, _ = encode_phone_access_session_token(access_phone_ids=ids)
    s = get_settings()
    claims = jwt.decode(token, s.JWT_SECRET_KEY, algorithms=[s.JWT_ALGORITHM])
    assert set(claims) == {"type", "access_phone_ids", "iat", "exp", "jti"}
    assert claims["type"] == TOKEN_TYPE
    # no PII-bearing claim keys
    for banned_key in ("phone_normalized", "phone_last4", "inspector_type",
                       "plot_id", "supplier_id", "plot_code"):
        assert banned_key not in claims
    # no Thai mobile number anywhere in the values
    assert not re.search(r"0[689]\d{8}", str(claims))


def test_encode_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        encode_phone_access_session_token(access_phone_ids=[])


def test_encode_rejects_over_max_ids() -> None:
    with pytest.raises(ValueError):
        encode_phone_access_session_token(
            access_phone_ids=[uuid4() for _ in range(MAX_ACCESS_PHONE_IDS + 1)]
        )


def test_decode_expired_rejected() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    expired = _raw({
        "type": TOKEN_TYPE,
        "access_phone_ids": [str(uuid4())],
        "iat": int((now - datetime.timedelta(hours=9)).timestamp()),
        "exp": int((now - datetime.timedelta(hours=1)).timestamp()),
        "jti": "x",
    })
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(expired)


def test_decode_wrong_type_rejected() -> None:
    bad = _raw({
        "type": "inspection_session",  # not phone_access_session
        "access_phone_ids": [str(uuid4())],
        "iat": 0, "exp": 9999999999, "jti": "x",
    })
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(bad)


def test_decode_missing_ids_rejected() -> None:
    bad = _raw({"type": TOKEN_TYPE, "iat": 0, "exp": 9999999999, "jti": "x"})
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(bad)


def test_decode_empty_ids_rejected() -> None:
    bad = _raw({
        "type": TOKEN_TYPE, "access_phone_ids": [],
        "iat": 0, "exp": 9999999999, "jti": "x",
    })
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(bad)


def test_decode_malformed_uuid_rejected() -> None:
    bad = _raw({
        "type": TOKEN_TYPE, "access_phone_ids": ["not-a-uuid"],
        "iat": 0, "exp": 9999999999, "jti": "x",
    })
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(bad)


def test_decode_too_many_ids_rejected() -> None:
    bad = _raw({
        "type": TOKEN_TYPE,
        "access_phone_ids": [str(uuid4()) for _ in range(MAX_ACCESS_PHONE_IDS + 1)],
        "iat": 0, "exp": 9999999999, "jti": "x",
    })
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(bad)


def test_garbage_token_rejected() -> None:
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token("not-a-jwt")


async def test_phone_access_token_not_accepted_as_login_token() -> None:
    """get_current_user only accepts type == 'access' — a phone-access token is
    rejected 401 before any DB access (the type check precedes the query)."""
    token, _ = encode_phone_access_session_token(access_phone_ids=[uuid4()])
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization=f"Bearer {token}", db=AsyncMock())
    assert exc.value.status_code == 401
