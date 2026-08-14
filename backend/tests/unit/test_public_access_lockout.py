"""Round 8-9C — brute-force lockout, password session token, record-create
recheck, and readiness.

All storage here is an isolated in-memory `limits` backend; no test touches a
shared/redis one and none needs PUBLIC_PLOT_PASSWORD_ENFORCEMENT enabled in
the real environment.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

import app.api.v1.public_inspection_access as pia
import app.api.v1.public_records as public_records
from app.core import public_access_lockout as lockout

PHONE = "0812345678"           # test-only
PEPPER = SecretStr("p" * 40)   # test-only
FP_A = "a" * 64                # a fingerprint-shaped value, not a real one
FP_B = "b" * 64
IP_A = "10.0.0.1"
IP_B = "10.0.0.2"


@pytest.fixture(autouse=True)
def _isolated_memory_storage(monkeypatch: pytest.MonkeyPatch):
    """Every test gets a FRESH memory:// backend, so counters can never leak
    between tests (or into a developer's real redis)."""
    monkeypatch.setattr(
        "app.core.public_access_lockout.get_settings",
        lambda: SimpleNamespace(RATE_LIMIT_STORAGE_URI="memory://"),
    )
    lockout.reset_for_tests()
    yield
    lockout.reset_for_tests()


# --- counters ---------------------------------------------------------------

def test_a_fresh_phone_is_not_locked_out() -> None:
    assert lockout.is_locked_out(IP_A, FP_A) is False


def test_per_ip_and_phone_locks_out_after_the_configured_failures() -> None:
    limit = lockout.PER_IP_PHONE_LIMIT.amount
    for _ in range(limit):
        assert lockout.is_locked_out(IP_A, FP_A) is False
        lockout.register_failure(IP_A, FP_A)
    assert lockout.is_locked_out(IP_A, FP_A) is True


def test_one_ip_being_locked_out_does_not_lock_a_different_phone() -> None:
    for _ in range(lockout.PER_IP_PHONE_LIMIT.amount):
        lockout.register_failure(IP_A, FP_A)
    assert lockout.is_locked_out(IP_A, FP_A) is True
    assert lockout.is_locked_out(IP_A, FP_B) is False


def test_the_global_phone_counter_catches_a_distributed_grind() -> None:
    """Tier 1 buckets by IP, so a botnet slips under it — tier 2 is what sees
    the same phone being attacked from everywhere."""
    per_ip = lockout.PER_IP_PHONE_LIMIT.amount
    total = lockout.PER_PHONE_LIMIT.amount
    # Spread failures across enough distinct IPs that no single IP trips.
    for i in range(total):
        ip = f"10.1.{i // per_ip}.{i % per_ip}"
        lockout.register_failure(ip, FP_A)
    assert lockout.is_locked_out("10.9.9.9", FP_A) is True    # a brand-new IP
    assert lockout.is_locked_out("10.9.9.9", FP_B) is False   # other phones fine


def test_checking_does_not_consume_budget() -> None:
    """is_locked_out peeks (`test`), never `hit` — otherwise merely asking
    would lock an honest user out."""
    for _ in range(lockout.PER_IP_PHONE_LIMIT.amount * 3):
        assert lockout.is_locked_out(IP_A, FP_A) is False


def test_success_clears_both_tiers() -> None:
    for _ in range(lockout.PER_IP_PHONE_LIMIT.amount):
        lockout.register_failure(IP_A, FP_A)
    assert lockout.is_locked_out(IP_A, FP_A) is True
    lockout.clear_failures(IP_A, FP_A)
    assert lockout.is_locked_out(IP_A, FP_A) is False
    assert lockout.is_locked_out(IP_B, FP_A) is False   # tier 2 cleared too


def test_keys_never_contain_a_phone_password_or_plot_code() -> None:
    ip_key, phone_key = lockout._keys(IP_A, FP_A)
    for key in (ip_key, phone_key):
        assert PHONE not in key
        assert "135790" not in key
        assert "SUP001" not in key
        assert FP_A in key          # the fingerprint IS the bucket


def test_storage_failure_fails_open_without_raising() -> None:
    """A redis blip must not take the whole public inspection flow down: the
    credential is still bcrypt-verified and the route-level limit still
    applies, so losing this extra counter degrades rather than denies."""
    class _Broken:
        def test(self, *_a, **_kw):
            raise RuntimeError("storage down")

        def hit(self, *_a, **_kw):
            raise RuntimeError("storage down")

        def clear(self, *_a, **_kw):
            raise RuntimeError("storage down")

    with patch.object(lockout, "_rate_limiter", lambda: _Broken()):
        assert lockout.is_locked_out(IP_A, FP_A) is False
        lockout.register_failure(IP_A, FP_A)     # must not raise
        lockout.clear_failures(IP_A, FP_A)       # must not raise


def test_it_uses_the_apps_configured_shared_storage_uri() -> None:
    """Production is required to point RATE_LIMIT_STORAGE_URI at shared
    storage (Settings validator); this module must honour the SAME setting
    rather than inventing its own backend."""
    src = inspect.getsource(lockout)
    assert "RATE_LIMIT_STORAGE_URI" in src
    assert "storage_from_string" in src


def test_it_adds_no_dependency_and_uses_no_private_slowapi_attribute() -> None:
    src = inspect.getsource(lockout)
    # No private attribute of slowapi's Limiter is reached into...
    assert "limiter._" not in src
    assert "from slowapi" not in src
    assert "import slowapi" not in src
    # ...and the engine used is `limits`, which slowapi already depends on, so
    # nothing new is installed.
    assert "from limits" in src


def test_the_fingerprint_is_keyed_and_domain_separated() -> None:
    import hashlib

    from app.auth.plot_access_password import build_phone_lockout_fingerprint
    from app.auth.plot_access_password import build_plot_access_password_lookup_digest

    with patch("app.auth.plot_access_password.get_settings",
               return_value=SimpleNamespace(PLOT_ACCESS_PASSWORD_PEPPER=PEPPER)):
        fp = build_phone_lockout_fingerprint(PHONE)
        # not a bare SHA-256 of the phone (that would be a ~10^8 rainbow table)
        assert fp != hashlib.sha256(PHONE.encode()).hexdigest()
        # domain-separated from the credential digest even though the key is shared
        assert fp != build_plot_access_password_lookup_digest(PHONE)
    assert len(fp) == 64
    assert PHONE not in fp


def test_the_route_level_ip_limit_still_guards_the_lookup() -> None:
    """The cheaper outer gate is unchanged — slowapi's decorator runs before
    the endpoint body, and therefore before any bcrypt."""
    assert hasattr(pia.phone_access_lookup, "__wrapped__")
    src = Path(inspect.getfile(pia)).read_text(encoding="utf-8")
    assert '@limiter.limit("5/minute")' in src


# --- password session token -------------------------------------------------

from app.auth.phone_password_session import (  # noqa: E402
    MAX_GRANTS,
    CredentialGrant,
    PhonePasswordTokenError,
    decode_phone_password_session_token,
    encode_phone_password_session_token,
)


def _grant(version: int = 1) -> CredentialGrant:
    return CredentialGrant(
        access_phone_id=uuid4(), credential_id=uuid4(), credential_version=version
    )


def test_token_round_trip_preserves_every_grant() -> None:
    grants = [_grant(1), _grant(7)]
    token, expires_in = encode_phone_password_session_token(grants=grants)
    assert expires_in > 0
    assert decode_phone_password_session_token(token) == grants


def test_token_rejects_empty_or_oversized_or_duplicate_grants() -> None:
    with pytest.raises(ValueError):
        encode_phone_password_session_token(grants=[])
    with pytest.raises(ValueError):
        encode_phone_password_session_token(grants=[_grant() for _ in range(MAX_GRANTS + 1)])
    dup = _grant()
    with pytest.raises(ValueError):
        encode_phone_password_session_token(grants=[dup, dup])


@pytest.mark.parametrize("bad", ["", "not.a.token", "a.b.c"])
def test_malformed_tokens_raise_the_one_generic_error(bad: str) -> None:
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(bad)


def test_a_legacy_phone_only_token_is_rejected_by_type() -> None:
    from app.auth.phone_access_session import encode_phone_access_session_token

    legacy, _ = encode_phone_access_session_token(access_phone_ids=[uuid4()])
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(legacy)


def test_a_password_token_is_rejected_by_the_legacy_decoder() -> None:
    """Neither type may stand in for the other — that symmetry is what stops a
    password-verified session being downgraded or a phone-only one upgraded."""
    from app.auth.phone_access_session import (
        PhoneAccessTokenError,
        decode_phone_access_session_token,
    )

    token, _ = encode_phone_password_session_token(grants=[_grant()])
    with pytest.raises(PhoneAccessTokenError):
        decode_phone_access_session_token(token)


@pytest.mark.parametrize(
    "mutate",
    [
        {"type": "phone_access_session"},
        {"ver": 99},
        {"grants": []},
        {"grants": "not-a-list"},
        {"grants": [{"a": "not-a-uuid", "c": str(uuid4()), "v": 1}]},
        {"grants": [{"a": str(uuid4()), "c": str(uuid4()), "v": "1"}]},
        {"grants": [{"a": str(uuid4()), "c": str(uuid4()), "v": True}]},
        {"grants": [{"a": str(uuid4()), "c": str(uuid4()), "v": -1}]},
        {"grants": [{"a": str(uuid4())}]},
    ],
)
def test_every_malformed_claim_shape_is_rejected(mutate: dict) -> None:
    from jose import jwt

    from app.core.config import get_settings

    token, _ = encode_phone_password_session_token(grants=[_grant()])
    settings = get_settings()
    claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    claims.update(mutate)
    forged = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(forged)


def test_duplicate_grants_in_a_forged_token_are_rejected() -> None:
    from jose import jwt

    from app.core.config import get_settings

    grant = _grant()
    token, _ = encode_phone_password_session_token(grants=[grant])
    settings = get_settings()
    claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    claims["grants"] = claims["grants"] * 2
    forged = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(forged)


def test_an_oversized_grant_list_is_rejected_on_decode() -> None:
    from jose import jwt

    from app.core.config import get_settings

    token, _ = encode_phone_password_session_token(grants=[_grant()])
    settings = get_settings()
    claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    claims["grants"] = [
        {"a": str(uuid4()), "c": str(uuid4()), "v": 1} for _ in range(MAX_GRANTS + 1)
    ]
    forged = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(forged)


# --- inspection token binding + record create recheck -----------------------

def _claims(**over) -> dict:
    base = {
        "plot_access_credential_id": str(uuid4()),
        "plot_access_credential_version": 1,
    }
    base.update(over)
    return base


def _enforce(on: bool):
    return patch(
        "app.api.v1.public_records.get_settings",
        return_value=SimpleNamespace(PUBLIC_PLOT_PASSWORD_ENFORCEMENT=on),
    )


def test_inspection_token_omits_the_binding_when_enforcement_is_off() -> None:
    from jose import jwt

    from app.auth.inspection_session import encode_inspection_session_token
    from app.core.config import get_settings

    token, _ = encode_inspection_session_token(
        plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        plot_access_phone_id=uuid4(), inspector_type="farmer",
    )
    settings = get_settings()
    claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert "plot_access_credential_id" not in claims
    assert "plot_access_credential_version" not in claims


def test_inspection_token_carries_the_binding_but_no_secret_when_bound() -> None:
    import json

    from jose import jwt

    from app.auth.inspection_session import encode_inspection_session_token
    from app.core.config import get_settings

    credential_id = uuid4()
    token, _ = encode_inspection_session_token(
        plot_id=uuid4(), supplier_id=uuid4(), plot_cycle_id=uuid4(),
        plot_access_phone_id=uuid4(), inspector_type="farmer",
        plot_access_credential_id=credential_id, plot_access_credential_version=4,
    )
    settings = get_settings()
    claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert claims["plot_access_credential_id"] == str(credential_id)
    assert claims["plot_access_credential_version"] == 4
    blob = json.dumps(claims)
    for leaked in (PHONE, "135790", "$2b$", "password", "digest"):
        assert leaked not in blob


async def test_record_recheck_is_a_no_op_when_enforcement_is_off() -> None:
    plot = SimpleNamespace(id=uuid4())
    with _enforce(False), \
         patch("app.api.v1.public_records.credential_repo.get_active_credential_by_plot_id",
               AsyncMock()) as mk:
        await public_records._recheck_plot_credential(AsyncMock(), plot, "any-token")
    mk.assert_not_awaited()


async def test_record_recheck_accepts_a_matching_credential() -> None:
    plot = SimpleNamespace(id=uuid4())
    credential_id = uuid4()
    live = SimpleNamespace(id=credential_id, credential_version=3, is_active=True)
    with _enforce(True), \
         patch("app.api.v1.public_records.decode_inspection_session_token",
               return_value=_claims(plot_access_credential_id=str(credential_id),
                                    plot_access_credential_version=3)), \
         patch("app.api.v1.public_records.credential_repo.get_active_credential_by_plot_id",
               AsyncMock(return_value=live)):
        await public_records._recheck_plot_credential(AsyncMock(), plot, "tok")


@pytest.mark.parametrize(
    "label,claims,live",
    [
        ("no_binding_on_token", {}, SimpleNamespace(id=uuid4(), credential_version=1)),
        ("malformed_id", {"plot_access_credential_id": "nope",
                          "plot_access_credential_version": 1},
         SimpleNamespace(id=uuid4(), credential_version=1)),
        ("malformed_version", {"plot_access_credential_id": str(uuid4()),
                               "plot_access_credential_version": "1"},
         SimpleNamespace(id=uuid4(), credential_version=1)),
        ("credential_removed", None, None),
    ],
)
async def test_record_recheck_fails_closed_and_generically(label, claims, live) -> None:
    plot = SimpleNamespace(id=uuid4())
    claims = _claims() if claims is None else claims
    with _enforce(True), \
         patch("app.api.v1.public_records.decode_inspection_session_token",
               return_value=claims), \
         patch("app.api.v1.public_records.credential_repo.get_active_credential_by_plot_id",
               AsyncMock(return_value=live)):
        with pytest.raises(HTTPException) as exc:
            await public_records._recheck_plot_credential(AsyncMock(), plot, "tok")
    assert exc.value.status_code == 404, label
    assert exc.value.detail == "Plot not found", label
    assert "password" not in str(exc.value.detail).lower(), label


async def test_a_password_changed_after_select_plot_blocks_the_record() -> None:
    """The version moved between select-plot and submit — the exact scenario
    "the farmer's password was changed while they were filling the form"."""
    plot = SimpleNamespace(id=uuid4())
    credential_id = uuid4()
    with _enforce(True), \
         patch("app.api.v1.public_records.decode_inspection_session_token",
               return_value=_claims(plot_access_credential_id=str(credential_id),
                                    plot_access_credential_version=1)), \
         patch("app.api.v1.public_records.credential_repo.get_active_credential_by_plot_id",
               AsyncMock(return_value=SimpleNamespace(
                   id=credential_id, credential_version=2, is_active=True))):
        with pytest.raises(HTTPException) as exc:
            await public_records._recheck_plot_credential(AsyncMock(), plot, "tok")
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"


def test_the_record_recheck_runs_under_the_existing_plot_lock() -> None:
    """Lock order is unchanged: the recheck is the LAST thing before the
    insert, inside the Plot → PlotCycle → PlotAccessPhone sequence already
    established in round 8.0.7."""
    src = inspect.getsource(public_records._finish_creating_record)
    lock_at = src.index("get_plot_for_update")
    phone_at = src.index("get_access_row_for_plot_from_ids")
    recheck_at = src.index("_recheck_plot_credential")
    assert lock_at < phone_at < recheck_at
