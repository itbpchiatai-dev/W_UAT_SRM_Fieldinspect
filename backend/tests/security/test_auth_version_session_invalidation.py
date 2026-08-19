"""auth_version session invalidation (round 8-23A).

An admin password reset must make the target's EXISTING access AND refresh
tokens unusable. The pre-existing `revoked_tokens` blocklist cannot do
this: it is keyed by a refresh token's `jti`, and jtis are never recorded
at mint time, so there is no way to enumerate "every outstanding token for
user X". `users.auth_version` is the user-wide generation counter that
closes that gap.

Contract under test:
  - both token kinds carry the generation live at mint time
  - get_current_user and /auth/refresh compare the claim to the LIVE row
    with EXACT equality, fail-closed
  - a token with NO claim reads as generation 0, so pre-8-23A sessions
    survive the rollout for users still at 0 (and only for those)

No real DB: the session is an AsyncMock and the token helpers are the real
ones.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

from app.auth.dependencies import get_current_user
from app.auth.jwt_service import (
    decode_token,
    encode_access_token,
    encode_refresh_token,
    token_auth_version,
)
from app.core.config import get_settings

_SUBJECT = str(uuid4())


def _user(**overrides) -> SimpleNamespace:
    base = dict(
        id=None, is_active=True, auth_version=0,
        roles=[], permission_overrides=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db_returning(user) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: user)
    )
    return db


async def _authenticate(token: str, user) -> object:
    return await get_current_user(
        authorization=f"Bearer {token}", db=_db_returning(user)
    )


def _legacy_access_token(subject: str) -> str:
    """A pre-8-23A access token: identical shape MINUS the auth_version
    claim. Minted here by hand because the current encoder always emits
    the claim."""
    settings = get_settings()
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=30)).timestamp()),
        "type": "access",
        "auth_provider": "local",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# --- claim is minted --------------------------------------------------

def test_access_token_carries_the_auth_version_claim() -> None:
    claims = decode_token(encode_access_token(
        subject=_SUBJECT, auth_provider="local", auth_version=3))
    assert claims["auth_version"] == 3


def test_refresh_token_carries_the_auth_version_claim() -> None:
    claims = decode_token(encode_refresh_token(
        subject=_SUBJECT, auth_provider="local", auth_version=5))
    assert claims["auth_version"] == 5
    # jti (per-token revocation) still present — the two mechanisms coexist.
    assert claims["jti"]


def test_default_auth_version_is_zero() -> None:
    claims = decode_token(encode_access_token(subject=_SUBJECT, auth_provider="local"))
    assert claims["auth_version"] == 0


# --- token_auth_version helper (fail-closed parsing) --------------------

def test_absent_claim_reads_as_generation_zero() -> None:
    assert token_auth_version({"sub": "x"}) == 0


def test_valid_int_claim_is_returned() -> None:
    assert token_auth_version({"auth_version": 4}) == 4


@pytest.mark.parametrize("bad", ["1", 1.0, None, [], {}, True, False])
def test_malformed_claim_is_rejected_not_coerced(bad: object) -> None:
    """None means "reject". A string "1", a float 1.0, or a bool True would
    all compare equal-ish to 1 somewhere downstream if coerced — bool
    especially, since bool subclasses int in Python."""
    assert token_auth_version({"auth_version": bad}) is None


# --- access-token enforcement -----------------------------------------

async def test_current_generation_token_is_accepted() -> None:
    user = _user(auth_version=2)
    token = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=2)
    assert await _authenticate(token, user) is user


async def test_token_from_before_a_reset_is_rejected() -> None:
    """The core requirement: the token was minted at generation 2, the
    admin then reset the password (row is now at 3)."""
    user = _user(auth_version=3)
    stale = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=2)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(stale, user)
    assert exc.value.status_code == 401


async def test_token_ahead_of_the_row_is_also_rejected() -> None:
    """Exact equality, never `<=` — a token claiming a generation the user
    has not reached (rollback, tampering) must fail closed too."""
    user = _user(auth_version=1)
    ahead = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=9)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(ahead, user)
    assert exc.value.status_code == 401


async def test_newly_minted_token_after_a_reset_works() -> None:
    user = _user(auth_version=3)
    fresh = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=3)
    assert await _authenticate(fresh, user) is user


async def test_legacy_token_without_the_claim_works_for_a_version_zero_user() -> None:
    """Rollout: sessions minted before this round keep working — but only
    while the user has never had a reset."""
    user = _user(auth_version=0)
    assert await _authenticate(_legacy_access_token(_SUBJECT), user) is user


async def test_legacy_token_stops_working_once_the_user_is_reset() -> None:
    user = _user(auth_version=1)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(_legacy_access_token(_SUBJECT), user)
    assert exc.value.status_code == 401


async def test_null_auth_version_column_is_treated_as_zero() -> None:
    """Defensive: a row read before the migration's default applied (or a
    mock) must not blow up on None."""
    user = _user(auth_version=None)
    token = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=0)
    assert await _authenticate(token, user) is user


async def test_rejection_message_does_not_leak_the_generation_numbers() -> None:
    user = _user(auth_version=42)
    stale = encode_access_token(subject=_SUBJECT, auth_provider="local", auth_version=41)
    with pytest.raises(HTTPException) as exc:
        await _authenticate(stale, user)
    detail = str(exc.value.detail)
    assert "42" not in detail
    assert "41" not in detail


# --- wiring proof: every mint site + the refresh gate -------------------

def test_all_three_mint_sites_pass_auth_version() -> None:
    """login, /refresh rotation, and /sso/callback must ALL stamp the
    generation — a site that forgets would mint a token permanently stuck
    at 0 and lock the user out the moment they are reset once."""
    import inspect
    from app.api.v1 import auth as auth_module

    for fn in (auth_module.login, auth_module.refresh, auth_module.sso_callback):
        src = inspect.getsource(fn)
        assert src.count("auth_version=") >= 2, (
            f"{fn.__name__} must pass auth_version to BOTH the access and "
            "refresh token mint"
        )


def test_refresh_endpoint_enforces_the_generation() -> None:
    """Without this, a reset would kill access tokens but leave the 7-day
    refresh cookie able to mint brand-new ones."""
    import inspect
    from app.api.v1 import auth as auth_module

    src = inspect.getsource(auth_module.refresh)
    assert "token_auth_version(claims)" in src
    assert "refreshed_user.auth_version" in src


def test_get_current_user_enforces_the_generation() -> None:
    import inspect
    from app.auth import dependencies

    src = inspect.getsource(dependencies.get_current_user)
    assert "token_auth_version(claims)" in src
    assert "claim_version is None" in src, "malformed claim must fail closed"
    assert "!=" in src, "must be exact equality, never a <= comparison"
