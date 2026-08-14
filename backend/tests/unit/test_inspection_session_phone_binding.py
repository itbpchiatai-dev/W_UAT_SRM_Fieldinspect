"""Round 8-3B introduced phone binding on inspection_session tokens as
OPTIONAL (both-or-neither, runtime-checked). Round 8-3H made it REQUIRED at
the type-signature level instead — see test_inspection_session_token.py for
the TypeError-on-missing-kwarg coverage. This file keeps the
phone-binding-specific behavioral checks (claim shape, no raw phone,
inspector_type allowlist) that are still meaningful post-8-3H.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest
from jose import jwt

from app.auth.inspection_session import encode_inspection_session_token
from app.core.config import get_settings


def _decode(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.JWT_SECRET_KEY, algorithms=[s.JWT_ALGORITHM])


def test_phone_bound_token_carries_both_claims_no_raw_phone() -> None:
    pid = uuid4()
    token, _ = encode_inspection_session_token(
        plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        plot_access_phone_id=pid, inspector_type="supplier",
    )
    claims = _decode(token)
    assert claims["plot_access_phone_id"] == str(pid)
    assert claims["inspector_type"] == "supplier"
    # id only — never a phone number
    assert not re.search(r"0[689]\d{8}", str(claims))


def test_claim_set_is_exactly_the_expected_shape() -> None:
    """Round 8-3H: plot_access_phone_id/inspector_type are now always
    present (no more legacy no-binding shape to distinguish from)."""
    token, _ = encode_inspection_session_token(
        plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        plot_access_phone_id=uuid4(), inspector_type="farmer",
    )
    claims = _decode(token)
    assert set(claims) == {
        "type", "plot_id", "supplier_id", "plot_cycle_id",
        "plot_access_phone_id", "inspector_type", "iat", "exp", "jti",
    }


def test_invalid_inspector_type_rejected() -> None:
    with pytest.raises(ValueError):
        encode_inspection_session_token(
            plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
            plot_access_phone_id=uuid4(), inspector_type="agronomist",
        )
