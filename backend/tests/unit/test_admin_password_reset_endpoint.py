"""POST /users/{user_id}/reset-password — admin password reset (round 8-23A).

DB-less: the route function is called directly with an AsyncMock session,
same style as tests/unit/test_masterdata_endpoint_duplicate.py and
tests/unit/test_supplier_import_endpoints.py. No live HTTP server, no real
database, no real user is ever reset.

SECURITY INVARIANT for this whole file: no assertion, fixture, or failure
message may ever put a plaintext password into a test snapshot. The
constants below are obviously-fake local test values, and every assertion
about them is a NOT-in check (proving the value did NOT leak into a
response or an audit row), never an echo of a real credential.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.users import reset_user_password
from app.auth.password import verify_password
from app.schemas.auth import AdminPasswordResetRequest

_M = "app.api.v1.users"

# Fake, local-only test values. Never a real credential.
_GOOD_PASSWORD = "Correct-Horse-Battery-42"
_WEAK_PASSWORD = "short"


def _target(**overrides) -> SimpleNamespace:
    base = dict(
        id=uuid4(),
        email="target.user@example.invalid",
        auth_provider="local",
        password_hash="$2b$12$oldhasholdhasholdhasholdhasholdhasholdhasholdhasholdha",
        auth_version=0,
        is_active=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _caller(**overrides) -> SimpleNamespace:
    base = dict(id=uuid4(), email="admin@example.invalid", roles=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def _db_returning(target) -> AsyncMock:
    """AsyncMock session whose execute() resolves to `target`."""
    db = AsyncMock()
    result = SimpleNamespace(scalar_one_or_none=lambda: target)
    db.execute = AsyncMock(return_value=result)
    return db


async def _call(target, caller, password: str = _GOOD_PASSWORD, user_id=None):
    db = _db_returning(target)
    with patch(f"{_M}.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        result = await reset_user_password(
            user_id=user_id if user_id is not None else target.id,
            payload=AdminPasswordResetRequest(new_password=password),
            request=AsyncMock(),
            user=caller,
            db=db,
        )
    return result, db, logger_cls


# --- happy path ---------------------------------------------------------

async def test_local_user_reset_succeeds_and_hash_changes() -> None:
    target = _target()
    old_hash = target.password_hash
    caller = _caller()

    result, _db, _logger = await _call(target, caller)

    assert target.password_hash != old_hash
    assert result.status == "ok"
    assert result.user_id == target.id
    # The stored value is a real bcrypt hash of the new password, not the
    # password itself (asserted via verify, never by printing either one).
    assert target.password_hash.startswith("$2b$")
    assert verify_password(_GOOD_PASSWORD, target.password_hash)


async def test_reset_increments_auth_version_by_exactly_one() -> None:
    target = _target(auth_version=7)
    result, _db, _logger = await _call(target, _caller())
    assert target.auth_version == 8
    assert result.auth_version == 8
    assert result.sessions_invalidated is True


async def test_reset_takes_a_row_lock_before_writing() -> None:
    """The increment must be computed from a LOCKED row, otherwise two
    concurrent resets could both read the same auth_version and the second
    write would silently undo the first (lost update)."""
    import inspect
    src = inspect.getsource(reset_user_password)
    assert "with_for_update()" in src
    # ...and the lock must be taken BEFORE the mutation, in source order.
    assert src.index("with_for_update()") < src.index("target.auth_version =")


async def test_sequential_resets_increment_monotonically_no_lost_update() -> None:
    """Two resets serialised by the row lock must land on 1 then 2.

    LIMITATION (stated plainly): this repo has no DB-backed test fixture,
    so this cannot exercise a genuine concurrent race. What it does prove
    is the property a lost update would violate — that the increment is
    computed from the row's CURRENT value at mutation time, not from a
    value captured earlier in the request. Combined with
    test_reset_takes_a_row_lock_before_writing (which proves the read is
    done under FOR UPDATE), that is the full mechanism: serialise, then
    read-modify-write inside the lock.
    """
    target = _target(auth_version=0)
    caller = _caller()

    await _call(target, caller)
    assert target.auth_version == 1
    first_hash = target.password_hash

    await _call(target, caller, password="Another-Valid-Passphrase-9")
    assert target.auth_version == 2
    assert target.password_hash != first_hash


async def test_reset_does_not_touch_email_roles_supplier_or_active_state() -> None:
    target = _target()
    before = (target.email, target.auth_provider, target.is_active)

    await _call(target, _caller())

    assert (target.email, target.auth_provider, target.is_active) == before


# --- account rules ------------------------------------------------------

async def test_azure_ad_account_is_rejected_with_a_microsoft_message() -> None:
    target = _target(auth_provider="azure_ad", password_hash=None)
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller())
    assert exc.value.status_code == 400
    assert "Microsoft" in exc.value.detail
    assert target.password_hash is None
    assert target.auth_version == 0


async def test_non_local_non_azure_account_is_rejected_without_the_microsoft_message() -> None:
    """The seeded `system` FK-placeholder user (app/seed.py
    _seed_external_field_helper_user) is auth_provider='system' — it must
    be refused too, but telling its admin to "use Microsoft" would be
    nonsense."""
    target = _target(auth_provider="system", password_hash=None)
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller())
    assert exc.value.status_code == 400
    assert "Microsoft" not in exc.value.detail
    assert target.auth_version == 0


async def test_missing_target_returns_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call(None, _caller(), user_id=uuid4())
    assert exc.value.status_code == 404


async def test_self_reset_is_rejected_before_any_db_read() -> None:
    caller = _caller()
    db = _db_returning(_target(id=caller.id))
    with pytest.raises(HTTPException) as exc:
        await reset_user_password(
            user_id=caller.id,
            payload=AdminPasswordResetRequest(new_password=_GOOD_PASSWORD),
            request=AsyncMock(), user=caller, db=db,
        )
    assert exc.value.status_code == 403
    # Nothing was even looked up — the guard is the very first statement.
    db.execute.assert_not_awaited()


# --- password policy ----------------------------------------------------

async def test_weak_password_is_rejected_with_400_and_no_write() -> None:
    target = _target()
    old_hash = target.password_hash
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller(), password=_WEAK_PASSWORD)
    assert exc.value.status_code == 400
    assert target.password_hash == old_hash
    assert target.auth_version == 0


async def test_policy_rejection_never_echoes_the_password_or_its_length() -> None:
    target = _target()
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller(), password=_WEAK_PASSWORD)
    detail = str(exc.value.detail)
    assert _WEAK_PASSWORD not in detail
    assert str(len(_WEAK_PASSWORD)) not in detail


async def test_password_containing_the_email_local_part_is_rejected() -> None:
    """context_terms wiring: the target's own email local-part must be
    passed to hash_password, exactly like create_user does."""
    target = _target(email="somebody@example.invalid")
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller(), password="somebody-Passphrase-1")
    assert exc.value.status_code == 400


async def test_blank_password_is_rejected_generically() -> None:
    target = _target()
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller(), password="")
    assert exc.value.status_code == 400
    assert target.auth_version == 0


async def test_absurdly_long_password_is_rejected_by_length_alone() -> None:
    target = _target()
    with pytest.raises(HTTPException) as exc:
        await _call(target, _caller(), password="A1" + "x" * 5000)
    assert exc.value.status_code == 400
    # The rejection must not quote the value or reveal the limit.
    assert "x" * 20 not in str(exc.value.detail)


async def test_non_string_password_is_rejected_not_crashed() -> None:
    """SkipValidation means pydantic lets a non-str through — the endpoint's
    hand-written isinstance check is the only thing standing between that
    and a TypeError deep inside bcrypt."""
    target = _target()
    db = _db_returning(target)
    payload = AdminPasswordResetRequest(new_password="placeholder")
    object.__setattr__(payload, "new_password", 12345)
    with pytest.raises(HTTPException) as exc:
        await reset_user_password(
            user_id=target.id, payload=payload, request=AsyncMock(),
            user=_caller(), db=db,
        )
    assert exc.value.status_code == 400


# --- audit / secret safety ----------------------------------------------

async def test_audit_row_is_high_risk_security_event_with_the_right_action() -> None:
    target = _target()
    _result, _db, logger_cls = await _call(target, _caller())

    logger_cls.return_value.log.assert_awaited_once()
    kwargs = logger_cls.return_value.log.await_args.kwargs
    assert kwargs["action"] == "user.password_reset"
    assert kwargs["is_security_event"] is True
    assert kwargs["risk_level"] == "high"
    assert kwargs["resource_type"] == "user"
    assert kwargs["resource_id"] == str(target.id)


async def test_audit_row_carries_only_the_two_user_ids_and_no_secret() -> None:
    target = _target()
    caller = _caller()
    _result, _db, logger_cls = await _call(target, caller)

    kwargs = logger_cls.return_value.log.await_args.kwargs
    # Actor is passed as the User object; target as resource_id.
    assert kwargs["user"] is caller
    assert kwargs["resource_id"] == str(target.id)
    # No metadata at all -> nothing about the password can be in it.
    assert kwargs.get("metadata") is None
    assert kwargs.get("extra") is None
    serialized = repr(kwargs)
    assert _GOOD_PASSWORD not in serialized
    assert "password_hash" not in serialized


async def test_success_response_contains_no_password_or_hash() -> None:
    target = _target()
    result, _db, _logger = await _call(target, _caller())

    body = result.model_dump(by_alias=True)
    serialized = repr(body)
    assert _GOOD_PASSWORD not in serialized
    assert "$2b$" not in serialized
    assert "passwordHash" not in body
    assert "password_hash" not in body
    assert set(body) == {"status", "userId", "authVersion", "sessionsInvalidated"}
