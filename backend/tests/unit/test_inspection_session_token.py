"""app/auth/inspection_session.py — token minted after picking a plot via
the phone-access flow (round 8-3G retired the legacy inspection-code verify
path that used to mint this token type; round 8-3H made the phone binding a
required part of the type signature, not just a runtime check). Must be
decodable, carry the right claims/TTL, always carry the phone binding, and
must never be accepted by get_current_user as a login token (that's
enforced by the existing type != "access" check in
app/auth/dependencies.py — this file locks down that the token this module
mints would actually trip it).
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from app.auth import dependencies as auth_dependencies
from app.auth.inspection_session import (
    EXPIRE_MINUTES,
    TOKEN_TYPE,
    decode_inspection_session_token,
    encode_inspection_session_token,
)
from app.auth.jwt_service import decode_token

_ACCESS_PHONE_ID = uuid4()


def _encode(**overrides):
    defaults = dict(
        plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        plot_access_phone_id=_ACCESS_PHONE_ID, inspector_type="farmer",
    )
    defaults.update(overrides)
    return encode_inspection_session_token(**defaults)


def test_token_type_and_ttl_constants() -> None:
    assert TOKEN_TYPE == "inspection_session"
    assert EXPIRE_MINUTES == 30


def test_encode_returns_token_and_expires_in_seconds() -> None:
    token, expires_in = _encode()
    assert isinstance(token, str) and token
    assert expires_in == 1800  # 30 minutes


def test_decoded_claims_have_expected_shape() -> None:
    plot_id = uuid4()
    supplier_id = uuid4()
    plot_cycle_id = uuid4()
    token, _ = _encode(plot_id=plot_id, supplier_id=supplier_id, plot_cycle_id=plot_cycle_id)
    claims = decode_inspection_session_token(token)

    assert claims["type"] == "inspection_session"
    assert claims["plot_id"] == str(plot_id)
    assert claims["supplier_id"] == str(supplier_id)
    # Round 8-0.6: the token is now bound to the plot's active cycle.
    assert claims["plot_cycle_id"] == str(plot_cycle_id)
    # Round 8-3B/8-3H: phone binding is always present now.
    assert claims["plot_access_phone_id"] == str(_ACCESS_PHONE_ID)
    assert claims["inspector_type"] == "farmer"
    assert "iat" in claims
    assert "exp" in claims
    assert "jti" in claims
    assert claims["exp"] - claims["iat"] == 1800


def test_each_token_gets_a_unique_jti() -> None:
    plot_id, supplier_id, plot_cycle_id = uuid4(), uuid4(), uuid4()
    token1, _ = _encode(plot_id=plot_id, supplier_id=supplier_id, plot_cycle_id=plot_cycle_id)
    token2, _ = _encode(plot_id=plot_id, supplier_id=supplier_id, plot_cycle_id=plot_cycle_id)
    assert decode_inspection_session_token(token1)["jti"] != decode_inspection_session_token(token2)["jti"]


def test_token_decodes_via_the_shared_jwt_service_too() -> None:
    """Same JWT_SECRET_KEY/algorithm as access/refresh tokens — decodable
    by the generic decoder, which is exactly why the `type` claim (not the
    secret) is what keeps it from being usable as a login token."""
    token, _ = _encode()
    claims = decode_token(token)
    assert claims["type"] == "inspection_session"


def test_get_current_user_would_reject_this_token_type() -> None:
    """Regression guard: get_current_user must still gate on type == "access".
    Locks down that round 7 didn't loosen the existing logged-in flow to
    also accept inspection_session tokens."""
    src = inspect.getsource(auth_dependencies.get_current_user)
    assert 'claims.get("type") != "access"' in src

    token, _ = _encode()
    claims = decode_token(token)
    assert claims.get("type") != "access"


def test_raw_phone_number_is_never_a_claim() -> None:
    """Only the access-row id is a claim — never the phone number itself."""
    token, _ = _encode()
    claims = decode_inspection_session_token(token)
    assert "phone" not in claims
    assert "phone_normalized" not in claims


# --- Round 8-3H: phone binding is required, not optional --------------------

def test_encode_rejects_missing_plot_access_phone_id() -> None:
    with pytest.raises(TypeError):
        encode_inspection_session_token(
            plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
            inspector_type="farmer",
        )


def test_encode_rejects_missing_inspector_type() -> None:
    with pytest.raises(TypeError):
        encode_inspection_session_token(
            plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
            plot_access_phone_id=uuid4(),
        )


def test_encode_rejects_both_missing() -> None:
    with pytest.raises(TypeError):
        encode_inspection_session_token(
            plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        )


def test_encode_rejects_an_inspector_type_outside_the_allowlist() -> None:
    with pytest.raises(ValueError):
        _encode(inspector_type="not-a-real-role")
