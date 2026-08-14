"""Round 8-9C — public phone + plot-password enforcement.

Every enforcement=true case here is driven by monkeypatching the settings flag
inside the endpoint module. The REAL environment flag is never touched and
stays false: no test in this file requires it to be enabled.

DB-less: the repos are patched and the route functions called directly (same
style as test_phone_access_endpoints.py). Every PIN/phone/pepper is a test-only
fixture and is never printed.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

import app.api.v1.public_inspection_access as pia
from app.auth.phone_access_session import encode_phone_access_session_token
from app.auth.phone_password_session import (
    CredentialGrant,
    PhonePasswordTokenError,
    decode_phone_password_session_token,
    encode_phone_password_session_token,
)
from app.auth.plot_access_password import hash_plot_access_password
from app.schemas.phone_access import (
    PublicPhoneAccessListRequest,
    PublicPhoneAccessLookupRequest,
    PublicPhoneAccessSelectPlotRequest,
)

_M = "app.api.v1.public_inspection_access"

# Call the route functions THROUGH `.__wrapped__`, bypassing slowapi's
# @limiter.limit decorator (which insists on a real starlette Request) — the
# same approach test_phone_access_endpoints.py already uses. The route-level
# IP limit itself is asserted separately, in test_public_access_lockout.py.
_lookup_fn = pia.phone_access_lookup.__wrapped__
_plots_fn = pia.phone_access_plots.__wrapped__
_select_fn = pia.phone_access_select_plot.__wrapped__

PHONE = "0812345678"          # test-only
PIN = "135790"                # test-only
WRONG_PIN = "482913"          # test-only
PEPPER = SecretStr("p" * 40)  # test-only


# --- fixtures --------------------------------------------------------------

def _request(ip: str = "10.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


def _access(**kw):
    base = dict(id=uuid4(), access_type="primary", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _plot(**kw):
    base = dict(
        id=uuid4(), plot_code="P001", name="แปลงหนึ่ง", is_active=True,
        active_cycle=SimpleNamespace(
            id=uuid4(), cycle_no=1, cycle_label="jun2026", crop=None, variety=None,
            lot_no=None, planting_date=None, plant_count=None,
            expected_yield_full=None, expected_yield_unit=None,
        ),
        current_yield_pct=None, current_stage=None, last_inspected_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _supplier(**kw):
    base = dict(id=uuid4(), code="SUP001", name="ซัพ", is_active=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _credential(pin: str = PIN, version: int = 1, **kw):
    base = dict(
        id=uuid4(), credential_version=version, is_active=True,
        password_hash=hash_plot_access_password(pin),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _candidate(pin: str = PIN, version: int = 1):
    return (_access(), _plot(), _supplier(), _credential(pin, version))


def _enforcement(on: bool):
    return patch(f"{_M}.get_settings", return_value=SimpleNamespace(
        PUBLIC_PLOT_PASSWORD_ENFORCEMENT=on,
    ))


def _pepper():
    return patch("app.auth.plot_access_password.get_settings", return_value=SimpleNamespace(
        PLOT_ACCESS_PASSWORD_PEPPER=PEPPER,
    ))


def _lockout(locked: bool = False):
    """Patch the lockout module so no test shares real counter state."""
    return (
        patch(f"{_M}.public_access_lockout.is_locked_out", return_value=locked),
        patch(f"{_M}.public_access_lockout.register_failure"),
        patch(f"{_M}.public_access_lockout.clear_failures"),
    )


async def _lookup(payload, *, enforcement=True, candidates=None, locked=False, ip="10.0.0.1"):
    mk_locked, mk_fail, mk_clear = _lockout(locked)
    with _enforcement(enforcement), _pepper(), mk_locked, mk_fail as f, mk_clear as c, \
         patch(f"{_M}.credential_repo.lookup_active_access_rows_by_phone_and_digest",
               AsyncMock(return_value=candidates if candidates is not None else [])) as mk_q, \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={})):
        result = await _lookup_fn(
            payload=payload, request=_request(ip), db=AsyncMock()
        )
    return result, SimpleNamespace(query=mk_q, failed=f, cleared=c)


# --- enforcement OFF: legacy behaviour --------------------------------------

async def test_enforcement_off_uses_the_phone_only_path_even_with_a_password() -> None:
    """A client that starts sending a password must not change behaviour while
    the flag is false — no password token, no verification, no new errors."""
    rows = [(_access(), _plot(), _supplier())]
    with _enforcement(False), \
         patch(f"{_M}.phone_repo.lookup_active_access_rows_by_phone",
               AsyncMock(return_value=rows)) as mk_legacy, \
         patch(f"{_M}.credential_repo.lookup_active_access_rows_by_phone_and_digest",
               AsyncMock()) as mk_cred, \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={})):
        result = await _lookup_fn(
            payload=PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
            request=_request(), db=AsyncMock(),
        )
    mk_legacy.assert_awaited_once()
    mk_cred.assert_not_awaited()          # no credential query at all
    assert len(result.plots) == 1
    # the legacy token type — NOT a password-verified one
    with pytest.raises(PhonePasswordTokenError):
        decode_phone_password_session_token(result.phone_access_session_token)


async def test_enforcement_off_keeps_the_legacy_422_for_a_malformed_phone() -> None:
    with _enforcement(False):
        with pytest.raises(HTTPException) as exc:
            await _lookup_fn(
                payload=PublicPhoneAccessLookupRequest(phone="not-a-phone"),
                request=_request(), db=AsyncMock(),
            )
    assert exc.value.status_code == 422       # legacy error preserved


# --- enforcement ON: the generic failure ------------------------------------

@pytest.mark.parametrize(
    "label,payload_kwargs,candidates",
    [
        ("missing_password", {"phone": PHONE}, []),
        ("malformed_phone", {"phone": "nope", "password": PIN}, []),
        ("bad_format_password", {"phone": PHONE, "password": "123"}, []),
        ("unknown_phone_or_no_credential", {"phone": PHONE, "password": PIN}, []),
    ],
)
async def test_every_authentication_failure_is_the_same_generic_404(
    label, payload_kwargs, candidates
) -> None:
    with pytest.raises(HTTPException) as exc:
        await _lookup(
            PublicPhoneAccessLookupRequest(**payload_kwargs), candidates=candidates
        )
    assert exc.value.status_code == 404, label
    assert exc.value.detail == "Not found", label


async def test_wrong_password_with_a_digest_hit_is_still_rejected() -> None:
    """The blind index is NOT a proof: a row can only be returned when its own
    bcrypt hash verifies. (Constructed here as a candidate whose stored hash is
    for a DIFFERENT PIN — what a digest collision or a stale index would look
    like.)"""
    candidates = [_candidate(pin=WRONG_PIN)]
    with pytest.raises(HTTPException) as exc:
        await _lookup(
            PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
            candidates=candidates,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_a_digest_match_alone_never_authorizes() -> None:
    """Same point, stated as the rule: candidates existed, none verified, so
    NOTHING was returned and no token was minted."""
    with pytest.raises(HTTPException):
        await _lookup(
            PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
            candidates=[_candidate(pin=WRONG_PIN), _candidate(pin=WRONG_PIN)],
        )


async def test_failures_increment_the_lockout_counters() -> None:
    with pytest.raises(HTTPException):
        _, mocks = await _lookup(
            PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=[]
        )


async def test_lockout_is_checked_before_any_digest_query_or_bcrypt() -> None:
    """The counter exists to STOP work, not to record it after the fact."""
    mk_locked, mk_fail, mk_clear = _lockout(locked=True)
    with _enforcement(True), _pepper(), mk_locked, mk_fail, mk_clear, \
         patch(f"{_M}.build_plot_access_password_lookup_digest") as mk_digest, \
         patch(f"{_M}.credential_repo.lookup_active_access_rows_by_phone_and_digest",
               AsyncMock()) as mk_query, \
         patch(f"{_M}.verify_plot_access_password") as mk_verify:
        with pytest.raises(HTTPException) as exc:
            await _lookup_fn(
                payload=PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
                request=_request(), db=AsyncMock(),
            )
    assert exc.value.status_code == 429
    assert "attempt" in str(exc.value.detail).lower()
    mk_digest.assert_not_called()
    mk_query.assert_not_awaited()
    mk_verify.assert_not_called()


async def test_the_429_never_reveals_remaining_attempts() -> None:
    mk_locked, mk_fail, mk_clear = _lockout(locked=True)
    with _enforcement(True), _pepper(), mk_locked, mk_fail, mk_clear:
        with pytest.raises(HTTPException) as exc:
            await _lookup_fn(
                payload=PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
                request=_request(), db=AsyncMock(),
            )
    detail = str(exc.value.detail)
    for leaked in ("10", "50", "remaining", "attempts left", PHONE, PIN):
        assert leaked not in detail


# --- enforcement ON: success -------------------------------------------------

async def test_valid_phone_and_password_returns_the_plot_and_a_password_token() -> None:
    candidates = [_candidate()]
    result, mocks = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=candidates
    )
    assert len(result.plots) == 1
    grants = decode_phone_password_session_token(result.phone_access_session_token)
    assert len(grants) == 1
    assert grants[0].access_phone_id == candidates[0][0].id
    assert grants[0].credential_id == candidates[0][3].id
    assert grants[0].credential_version == 1
    mocks.cleared.assert_called_once()     # success resets the counters
    mocks.failed.assert_not_called()


async def test_one_phone_and_password_can_unlock_several_plots() -> None:
    """Locked business rule since 8-9A: plots may deliberately share a
    password, and all of them must come back."""
    candidates = [_candidate(), _candidate(), _candidate()]
    result, _ = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=candidates
    )
    assert len(result.plots) == 3
    assert len(decode_phone_password_session_token(result.phone_access_session_token)) == 3


async def test_only_the_verifying_rows_are_returned() -> None:
    good = _candidate(pin=PIN)
    bad = _candidate(pin=WRONG_PIN)
    result, _ = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=[good, bad]
    )
    assert len(result.plots) == 1
    assert result.plots[0].plot_id == good[1].id


async def test_bcrypt_runs_off_the_event_loop_for_every_candidate() -> None:
    offloaded = []
    real = asyncio.to_thread

    async def spy(fn, *a, **kw):
        offloaded.append(fn.__name__)
        return await real(fn, *a, **kw)

    with patch(f"{_M}.asyncio.to_thread", spy):
        await _lookup(
            PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN),
            candidates=[_candidate(), _candidate()],
        )
    assert offloaded == ["verify_plot_access_password"] * 2


def test_verification_concurrency_is_bounded() -> None:
    src = inspect.getsource(pia._verify_candidates)
    assert "Semaphore" in src
    assert "_MAX_VERIFY_CONCURRENCY" in src
    assert pia._MAX_VERIFY_CONCURRENCY <= 8


async def test_candidates_are_capped_before_verification() -> None:
    from app.auth.phone_access_session import MAX_ACCESS_PHONE_IDS

    many = [_candidate() for _ in range(MAX_ACCESS_PHONE_IDS + 5)]
    result, _ = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=many
    )
    assert len(result.plots) == MAX_ACCESS_PHONE_IDS


# --- QR ----------------------------------------------------------------------

async def test_qr_cannot_bypass_a_wrong_password() -> None:
    """A valid QR with the wrong password must fail exactly like any other bad
    attempt — the QR is only matched AFTER authorization."""
    with patch(f"{_M}.plot_repo.get_plot_by_qr_key", AsyncMock()) as mk_qr:
        with pytest.raises(HTTPException) as exc:
            await _lookup(
                PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN, qrKey="qr-1"),
                candidates=[_candidate(pin=WRONG_PIN)],
            )
    assert exc.value.status_code == 404
    mk_qr.assert_not_awaited()          # never even looked the QR up


async def test_qr_pointing_outside_the_verified_set_is_a_generic_404() -> None:
    candidates = [_candidate()]
    other_plot = _plot()
    mk_locked, mk_fail, mk_clear = _lockout()
    with _enforcement(True), _pepper(), mk_locked, mk_fail, mk_clear, \
         patch(f"{_M}.credential_repo.lookup_active_access_rows_by_phone_and_digest",
               AsyncMock(return_value=candidates)), \
         patch(f"{_M}.plot_repo.get_plot_by_qr_key", AsyncMock(return_value=other_plot)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={})):
        with pytest.raises(HTTPException) as exc:
            await _lookup_fn(
                payload=PublicPhoneAccessLookupRequest(
                    phone=PHONE, password=PIN, qrKey="qr-1"
                ),
                request=_request(), db=AsyncMock(),
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_qr_inside_the_verified_set_matches() -> None:
    candidates = [_candidate()]
    mk_locked, mk_fail, mk_clear = _lockout()
    with _enforcement(True), _pepper(), mk_locked, mk_fail, mk_clear, \
         patch(f"{_M}.credential_repo.lookup_active_access_rows_by_phone_and_digest",
               AsyncMock(return_value=candidates)), \
         patch(f"{_M}.plot_repo.get_plot_by_qr_key",
               AsyncMock(return_value=candidates[0][1])), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={})):
        result = await _lookup_fn(
            payload=PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN, qrKey="qr-1"),
            request=_request(), db=AsyncMock(),
        )
    assert result.qr_matched_plot_id == candidates[0][1].id


# --- secrets never leave -----------------------------------------------------

async def test_no_response_carries_the_password_phone_or_a_qr_key() -> None:
    candidates = [_candidate()]
    result, _ = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=candidates
    )
    body = result.model_dump_json(by_alias=True)
    for leaked in (PIN, PHONE, "$2b$", "qrKey", "password"):
        assert leaked not in body


async def test_the_password_token_carries_no_secret_and_no_phone() -> None:
    from jose import jwt

    from app.core.config import get_settings

    candidates = [_candidate()]
    result, _ = await _lookup(
        PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN), candidates=candidates
    )
    settings = get_settings()
    claims = jwt.decode(
        result.phone_access_session_token, settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    blob = json.dumps(claims)
    # NB: the token TYPE is "phone_password_access_session", so the word
    # "password" legitimately appears — assert on the actual secrets.
    for leaked in (PIN, PHONE, "$2b$", "digest", "fingerprint", "password_hash"):
        assert leaked not in blob
    assert set(claims["grants"][0]) == {"a", "c", "v"}


def test_the_request_masks_an_oversized_password() -> None:
    from pydantic import ValidationError

    huge = "1" * 5000
    with pytest.raises(ValidationError) as exc:
        PublicPhoneAccessLookupRequest(phone=PHONE, password=huge)
    assert huge not in str(exc.value)


def test_the_password_field_is_a_secret_that_never_reprs_its_value() -> None:
    payload = PublicPhoneAccessLookupRequest(phone=PHONE, password=PIN)
    assert PIN not in repr(payload)
    assert PIN not in str(payload)
    assert payload.password.get_secret_value() == PIN


# --- /plots and /select-plot rechecks ----------------------------------------

def _token_for(candidates) -> str:
    token, _ = encode_phone_password_session_token(grants=[
        CredentialGrant(
            access_phone_id=a.id, credential_id=c.id, credential_version=c.credential_version,
        )
        for (a, _p, _s, c) in candidates
    ])
    return token


async def _plots(token, live):
    with _enforcement(True), \
         patch(f"{_M}.credential_repo.list_active_access_rows_by_grants",
               AsyncMock(return_value=live)), \
         patch(f"{_M}.plot_cycle_repo.get_latest_active_record_dates_for_cycles",
               AsyncMock(return_value={})):
        return await _plots_fn(
            payload=PublicPhoneAccessListRequest(phoneAccessSessionToken=token),
            request=_request(), db=AsyncMock(),
        )


async def test_plots_returns_grants_that_are_still_valid() -> None:
    candidates = [_candidate()]
    result = await _plots(_token_for(candidates), candidates)
    assert len(result.plots) == 1


async def test_plots_drops_a_grant_whose_credential_version_moved() -> None:
    candidates = [_candidate(version=1)]
    token = _token_for(candidates)
    a, p, s, c = candidates[0]
    bumped = [(a, p, s, SimpleNamespace(
        id=c.id, credential_version=2, is_active=True, password_hash=c.password_hash,
    ))]
    with pytest.raises(HTTPException) as exc:
        await _plots(token, bumped)
    assert exc.value.status_code == 401


async def test_plots_drops_a_grant_whose_credential_row_was_replaced() -> None:
    candidates = [_candidate()]
    token = _token_for(candidates)
    a, p, s, c = candidates[0]
    different = [(a, p, s, SimpleNamespace(
        id=uuid4(), credential_version=1, is_active=True, password_hash=c.password_hash,
    ))]
    with pytest.raises(HTTPException) as exc:
        await _plots(token, different)
    assert exc.value.status_code == 401


async def test_plots_rejects_a_deactivated_credential_or_phone() -> None:
    """A deactivated credential/phone/plot/supplier simply doesn't come back
    from the set-based re-query."""
    candidates = [_candidate()]
    with pytest.raises(HTTPException) as exc:
        await _plots(_token_for(candidates), [])
    assert exc.value.status_code == 401


async def test_plots_rejects_a_legacy_phone_only_token_under_enforcement() -> None:
    legacy, _ = encode_phone_access_session_token(access_phone_ids=[uuid4()])
    with pytest.raises(HTTPException) as exc:
        await _plots(legacy, [])
    assert exc.value.status_code == 401


async def _select(token, live, plot_id):
    # select-plot still re-fetches the plot for the legacy active-plot /
    # active-cycle checks (unchanged by 8-9C) — stub that read too.
    fetched = next((row[1] for row in live if row[1].id == plot_id), None)
    if fetched is not None:
        supplier = next(row[2] for row in live if row[1].id == plot_id)
        fetched = SimpleNamespace(**{**vars(fetched), "supplier": supplier})
    with _enforcement(True), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=fetched)), \
         patch(f"{_M}.credential_repo.list_active_access_rows_by_grants",
               AsyncMock(return_value=live)):
        return await _select_fn(
            payload=PublicPhoneAccessSelectPlotRequest(
                phoneAccessSessionToken=token, plotId=plot_id, inspectorType="farmer",
            ),
            request=_request(), db=AsyncMock(),
        )


async def test_select_plot_mints_a_token_bound_to_the_credential() -> None:
    from jose import jwt

    from app.core.config import get_settings

    candidates = [_candidate()]
    result = await _select(_token_for(candidates), candidates, candidates[0][1].id)
    settings = get_settings()
    claims = jwt.decode(
        result.inspection_session_token, settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert claims["plot_access_credential_id"] == str(candidates[0][3].id)
    assert claims["plot_access_credential_version"] == 1
    blob = json.dumps(claims)
    for leaked in (PIN, PHONE, "$2b$"):
        assert leaked not in blob


async def test_select_plot_rejects_a_plot_outside_the_verified_set() -> None:
    candidates = [_candidate()]
    with pytest.raises(HTTPException) as exc:
        await _select(_token_for(candidates), candidates, uuid4())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_select_plot_rejects_a_stale_credential_version() -> None:
    candidates = [_candidate(version=1)]
    token = _token_for(candidates)
    a, p, s, c = candidates[0]
    bumped = [(a, p, s, SimpleNamespace(
        id=c.id, credential_version=9, is_active=True, password_hash=c.password_hash,
    ))]
    with pytest.raises(HTTPException) as exc:
        await _select(token, bumped, p.id)
    assert exc.value.status_code == 401


async def test_select_plot_response_carries_no_credential_fields() -> None:
    candidates = [_candidate()]
    result = await _select(_token_for(candidates), candidates, candidates[0][1].id)
    body = result.model_dump_json(by_alias=True)
    for leaked in (PIN, PHONE, "$2b$", "credentialId", "credentialVersion", "qrKey"):
        assert leaked not in body


# --- source guards -----------------------------------------------------------

def test_the_enforcement_path_never_falls_back_to_phone_only() -> None:
    src = inspect.getsource(pia._authorize_phone_password)
    assert "lookup_active_access_rows_by_phone(" not in src


def test_nothing_in_the_public_module_logs_a_phone_password_or_digest() -> None:
    src = Path(inspect.getfile(pia)).read_text(encoding="utf-8")
    # "print(" alone is a false positive — build_phone_lockout_fingerPRINT(...)
    # contains it. Match a real statement instead.
    for banned in ("logger.", "logging."):
        assert banned not in src
    for line in src.splitlines():
        assert not line.strip().startswith("print("), line
