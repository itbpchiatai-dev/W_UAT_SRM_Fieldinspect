"""HIGH-3 regression — refresh tokens MUST carry a `jti` so the auth
router can revoke them server-side. Without jti the blocklist becomes
a no-op. Env bootstrap lives in conftest.py.
"""
from __future__ import annotations

from app.auth.jwt_service import (
    decode_token, encode_access_token, encode_refresh_token,
)


def test_refresh_token_has_jti() -> None:
    tok = encode_refresh_token(subject="11111111-1111-1111-1111-111111111111",
                               auth_provider="local")
    claims = decode_token(tok)
    assert claims["type"] == "refresh"
    jti = claims.get("jti")
    assert isinstance(jti, str) and len(jti) >= 16


def test_refresh_token_jti_is_unique_per_mint() -> None:
    a = decode_token(encode_refresh_token(
        subject="11111111-1111-1111-1111-111111111111",
        auth_provider="local",
    ))
    b = decode_token(encode_refresh_token(
        subject="11111111-1111-1111-1111-111111111111",
        auth_provider="local",
    ))
    assert a["jti"] != b["jti"]


def test_access_token_unchanged_no_jti_required() -> None:
    # Access tokens stay stateless — the short exp is the defence.
    claims = decode_token(encode_access_token(
        subject="11111111-1111-1111-1111-111111111111",
        auth_provider="local",
    ))
    assert claims["type"] == "access"
