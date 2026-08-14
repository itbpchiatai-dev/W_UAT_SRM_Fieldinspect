"""Enforcement-OFF compatibility guard (written round 8-9A, rewritten 8-9C).

Rounds 8-9A/8-9B.1 asserted the public flow had not been touched at all. Round
8-9C wired verification in — so the invariant this file protects has MOVED, not
disappeared, and it is now the more important one:

    with PUBLIC_PLOT_PASSWORD_ENFORCEMENT false, the public inspection flow
    must behave EXACTLY as it did before 8-9C existed.

That is what makes the rollout safe: the flag can be flipped back at any time
and every existing field user keeps working, with no data change and no client
change. These tests fail if enforcement ever becomes the implicit default, if
the flag stops defaulting to false, or if the legacy phone-only branch is
removed.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import app.api.v1.public_inspection_access as public_access
import app.api.v1.public_records as public_records
import app.auth.inspection_session as inspection_session
import app.auth.phone_access_session as phone_access_session
from app.schemas.phone_access import (
    PublicPhoneAccessListRequest,
    PublicPhoneAccessLookupRequest,
    PublicPhoneAccessSelectPlotRequest,
)


def _src(mod) -> str:
    return Path(inspect.getfile(mod)).read_text(encoding="utf-8")


def test_enforcement_defaults_to_off() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["PUBLIC_PLOT_PASSWORD_ENFORCEMENT"].default is False


def test_lookup_password_is_optional_on_the_wire() -> None:
    """A pre-8-9C client sends no password at all. The SCHEMA must accept that
    — whether it is required is the endpoint's decision, gated on the flag."""
    field = PublicPhoneAccessLookupRequest.model_fields["password"]
    assert field.default is None
    assert PublicPhoneAccessLookupRequest(phone="0812345678").password is None


def test_lookup_request_keeps_its_original_fields() -> None:
    assert set(PublicPhoneAccessLookupRequest.model_fields) == {
        "phone", "password", "qr_key",
    }


def test_list_and_select_plot_requests_are_unchanged() -> None:
    """Neither gained a password field — the session token carries the proof."""
    assert set(PublicPhoneAccessListRequest.model_fields) == {
        "phone_access_session_token"
    }
    assert set(PublicPhoneAccessSelectPlotRequest.model_fields) == {
        "phone_access_session_token", "plot_id", "inspector_type",
    }


def test_the_legacy_phone_only_branch_still_exists() -> None:
    """Every enforcement branch must have an `else` that runs the round-8-3B
    code — deleting it would make the flag one-way."""
    src = _src(public_access)
    assert "_enforcement_on()" in src
    assert "decode_phone_access_session_token" in src        # legacy token path
    assert "lookup_active_access_rows_by_phone(" in src      # legacy lookup
    assert "encode_phone_access_session_token(" in src       # legacy mint


def test_record_create_credential_recheck_is_flag_gated() -> None:
    """The record path must be a no-op while the flag is false, or turning it
    back off would leave existing tokens unusable."""
    src = inspect.getsource(public_records._recheck_plot_credential)
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT" in src
    # The guard is the FIRST statement and returns immediately when off.
    body = src[src.index('"""', src.index('"""') + 3) + 3:]
    first_statements = "\n".join(
        line for line in body.splitlines() if line.strip()
    )[:200]
    assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT" in first_statements
    assert "return" in first_statements


def test_no_token_type_carries_a_password_or_phone() -> None:
    """Token claims may name a credential ROW and a VERSION — never a secret,
    never the phone."""
    import app.auth.phone_password_session as phone_password_session

    assert phone_access_session.TOKEN_TYPE == "phone_access_session"
    assert inspection_session.TOKEN_TYPE == "inspection_session"
    assert phone_password_session.TOKEN_TYPE == "phone_password_access_session"
    for mod in (phone_access_session, inspection_session, phone_password_session):
        src = _src(mod)
        for banned in ('"password"', "password_hash", "lookup_digest", '"phone"'):
            assert banned not in src, f"{mod.__name__} must not claim {banned}"


def test_the_flag_is_never_written_by_application_code() -> None:
    """Nothing may enable enforcement on its own — not on a readiness result,
    not on a coverage threshold. Only an operator changing the environment."""
    for mod in (public_access, public_records):
        src = _src(mod)
        assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT =" not in src
        assert "PUBLIC_PLOT_PASSWORD_ENFORCEMENT=True" not in src
