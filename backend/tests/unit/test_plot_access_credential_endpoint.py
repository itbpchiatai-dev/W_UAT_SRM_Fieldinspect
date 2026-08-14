"""GET/PUT /plots/{plotId}/inspection-access-credential (round 8-9A).

DB-less: patches the repos/helpers and calls the route functions directly (same
style as test_plot_access_phone_endpoint.py). Verifies permission/RLS wiring,
the generic 404 for out-of-scope/unknown plots, the Plot-locked-first order, the
503-not-500 on a missing pepper, the 409-on-IntegrityError, the version
semantics, the security activity log — and, above all, that NO secret ever
reaches a response, an error, or a log.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.api.v1.plots as plots_module
from app.api.v1.plots import (
    get_plot_inspection_access_credential,
    set_plot_inspection_access_credential,
)
from app.auth.permissions import PermissionKey
from app.auth.plot_access_password import PlotAccessPepperMissingError
from app.schemas.plot import PlotInspectionCredentialSet, PlotInspectionCredentialStatus

_P = "app.api.v1.plots"
_NOW = datetime.datetime(2026, 7, 31, tzinfo=datetime.timezone.utc)
_PIN = "135790"


def _db():
    return AsyncMock()


def _user():
    return SimpleNamespace(id=uuid4(), email="admin@example.com")


def _row(version: int = 1, is_active: bool = True):
    return SimpleNamespace(
        id=uuid4(), plot_id=uuid4(), password_hash="$2b$12$secret-hash",
        password_lookup_digest="a" * 64, credential_version=version,
        is_active=is_active, updated_at=_NOW,
    )


def _patch_helpers():
    """Real policy, stubbed pepper-dependent digest (no secret needed here)."""
    return patch(f"{_P}.build_plot_access_password_lookup_digest", return_value="a" * 64)


_SENTINEL = object()


@contextlib.contextmanager
def _patch_plot(scoped=_SENTINEL, locked=_SENTINEL):
    """Patch BOTH plot reads the PUT does (round 8-9A.1): the unlocked scoped
    read that authorizes before any work, and the locked re-read that closes the
    TOCTOU window after hashing. Pass `locked=None` to simulate the plot
    vanishing / leaving scope while bcrypt ran."""
    plot = SimpleNamespace(id=uuid4())
    with patch(f"{_P}.repo.get_plot",
               AsyncMock(return_value=plot if scoped is _SENTINEL else scoped)) as mk_scoped, \
         patch(f"{_P}.repo.get_plot_for_update",
               AsyncMock(return_value=plot if locked is _SENTINEL else locked)) as mk_locked:
        yield SimpleNamespace(plot=plot, scoped=mk_scoped, locked=mk_locked)


# --- GET --------------------------------------------------------------------

async def test_get_reports_configured_with_version_and_timestamp() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.credential_repo.get_credential_status_by_plot_id",
               AsyncMock(return_value=_row(version=3))):
        result = await get_plot_inspection_access_credential(plot_id=uuid4(), db=_db())
    assert result.configured is True
    assert result.credential_version == 3
    assert result.updated_at == _NOW


async def test_get_reports_not_configured_when_never_set() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.credential_repo.get_credential_status_by_plot_id",
               AsyncMock(return_value=None)):
        result = await get_plot_inspection_access_credential(plot_id=uuid4(), db=_db())
    assert result.configured is False
    assert result.credential_version is None
    assert result.updated_at is None


async def test_get_reports_not_configured_for_an_inactive_credential() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.credential_repo.get_credential_status_by_plot_id",
               AsyncMock(return_value=_row(version=2, is_active=False))):
        result = await get_plot_inspection_access_credential(plot_id=uuid4(), db=_db())
    assert result.configured is False
    assert result.credential_version is None


async def test_get_out_of_scope_or_unknown_is_generic_404() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=None)), \
         patch(f"{_P}.credential_repo.get_credential_status_by_plot_id", AsyncMock()) as mk:
        with pytest.raises(HTTPException) as exc:
            await get_plot_inspection_access_credential(plot_id=uuid4(), db=_db())
    assert exc.value.status_code == 404
    mk.assert_not_awaited()  # never queried the credential for an out-of-scope plot


async def test_get_response_carries_no_secret_field() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.credential_repo.get_credential_status_by_plot_id",
               AsyncMock(return_value=_row())):
        result = await get_plot_inspection_access_credential(plot_id=uuid4(), db=_db())
    body = result.model_dump(by_alias=True)
    assert set(body) == {"configured", "credentialVersion", "updatedAt"}
    dumped = str(body)
    for leaked in ("$2b$12$", "a" * 64, "password", "Digest", "pepper"):
        assert leaked not in dumped


# --- PUT --------------------------------------------------------------------

async def test_put_first_set_returns_version_1_status() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot() as plots, \
         _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row(version=1))) as mk_set, \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        result = await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    plots.scoped.assert_awaited_once()   # authorization read
    plots.locked.assert_awaited_once()   # aggregate lock for the mutation
    mk_set.assert_awaited_once()
    assert result.configured is True
    assert result.credential_version == 1


async def test_put_reports_the_incremented_version_on_replace() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot(), \
         _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row(version=7))), \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        result = await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    assert result.credential_version == 7


async def test_put_hashes_in_a_worker_thread_never_on_the_event_loop() -> None:
    """Round 8-9A.1: bcrypt cost 12 is ~250ms of blocking CPU. It must go
    through asyncio.to_thread, or one admin request stalls every other request
    the loop is serving."""
    payload = PlotInspectionCredentialSet(password=_PIN)
    offloaded = []
    real_to_thread = asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        offloaded.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.asyncio.to_thread", spy), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())), \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    assert offloaded == [plots_module.hash_plot_access_password]


async def test_put_phase_order_hash_completes_before_the_plot_lock() -> None:
    """The whole point of 8-9A.1: authorize → hash (no lock held) → lock →
    write. If the lock were taken first, every other writer on this plot would
    block for the duration of the bcrypt round."""
    payload = PlotInspectionCredentialSet(password=_PIN)
    order: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy_thread(fn, *args, **kwargs):
        result = await real_to_thread(fn, *args, **kwargs)
        order.append("hash")          # appended only once hashing FINISHED
        return result

    async def spy_scoped(*_a, **_kw):
        order.append("scoped_read")
        return SimpleNamespace(id=uuid4())

    async def spy_locked(*_a, **_kw):
        order.append("lock")
        return SimpleNamespace(id=uuid4())

    async def spy_set(*_a, **_kw):
        order.append("credential_write")
        return _row()

    with patch(f"{_P}.repo.get_plot", spy_scoped), \
         patch(f"{_P}.repo.get_plot_for_update", spy_locked), \
         _patch_helpers(), \
         patch(f"{_P}.asyncio.to_thread", spy_thread), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential", spy_set), \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    assert order == ["scoped_read", "hash", "lock", "credential_write"]


async def test_put_rechecks_the_plot_under_the_lock_after_hashing() -> None:
    """TOCTOU: the authorization read is unlocked, so a plot that is deleted or
    moved out of scope while bcrypt ran must still be a generic 404 — never a
    write against a plot the caller no longer owns."""
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot(locked=None), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(HTTPException) as exc:
            await set_plot_inspection_access_credential(
                plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"
    mk_set.assert_not_awaited()


async def test_put_out_of_scope_or_unknown_is_generic_404_before_any_work() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot(scoped=None) as plots, \
         patch(f"{_P}.build_plot_access_password_lookup_digest") as mk_digest, \
         patch(f"{_P}.asyncio.to_thread", AsyncMock()) as mk_thread, \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(HTTPException) as exc:
            await set_plot_inspection_access_credential(
                plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Plot not found"     # generic, no existence hint
    mk_set.assert_not_awaited()
    mk_digest.assert_not_called()
    mk_thread.assert_not_awaited()   # never spends a bcrypt round for an unauthorized caller
    plots.locked.assert_not_awaited()  # and never takes the lock either


async def test_put_may_set_a_credential_on_an_inactive_plot() -> None:
    """Prepares reactivation: an inactive plot can be given a password now so it
    is usable the moment it comes back."""
    payload = PlotInspectionCredentialSet(password=_PIN)
    inactive_plot = SimpleNamespace(id=uuid4(), is_active=False)
    with _patch_plot(scoped=inactive_plot, locked=inactive_plot), \
         _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())) as mk_set, \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        result = await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    mk_set.assert_awaited_once()
    assert result.configured is True


@pytest.mark.parametrize(
    "bad",
    ["123", "1" * 21, "13579a", "12 34", "๑๓๕๗๙๐", "１２３４"],
)
async def test_put_malformed_code_is_422_with_a_generic_message(bad: str) -> None:
    """Round 8-9B.0: only length/character violations reach 422 now — repeated
    and sequential codes are legal (see test_put_accepts_repeated_and_sequential
    below)."""
    payload = PlotInspectionCredentialSet(password=bad)
    with _patch_plot() as plots, \
         patch(f"{_P}.asyncio.to_thread", AsyncMock()) as mk_thread, \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(HTTPException) as exc:
            await set_plot_inspection_access_credential(
                plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 422
    assert bad not in str(exc.value.detail)     # never echoes the submitted code
    mk_set.assert_not_awaited()
    mk_thread.assert_not_awaited()      # rejected before any bcrypt cost
    plots.locked.assert_not_awaited()   # and before any lock


@pytest.mark.parametrize(
    "easy", ["0000", "1111", "1234", "987654", "111111", "1" * 20],
)
async def test_put_accepts_repeated_and_sequential_codes(easy: str) -> None:
    """Round 8-9B.0 — the guessability rules are GONE by product decision. Each
    of these used to be a 422; all of them must now reach the repository."""
    payload = PlotInspectionCredentialSet(password=easy)
    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())) as mk_set, \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        result = await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    mk_set.assert_awaited_once()
    assert result.configured is True
    # still hashed, never stored in the clear
    assert mk_set.await_args.kwargs["password_hash"].startswith("$2b$")
    assert easy not in mk_set.await_args.kwargs["password_hash"]


@pytest.mark.parametrize("edge", ["1357", "1" * 20])
async def test_put_accepts_both_length_boundaries(edge: str) -> None:
    payload = PlotInspectionCredentialSet(password=edge)
    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())) as mk_set, \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    mk_set.assert_awaited_once()


def test_oversized_payload_is_rejected_without_echoing_the_code() -> None:
    """The schema's coarse max_length guard sits above the 20-digit policy —
    a huge body is refused before any work, and SecretStr keeps the value out
    of the 422."""
    from pydantic import ValidationError

    huge = "1" * 5000
    with pytest.raises(ValidationError) as exc:
        PlotInspectionCredentialSet(password=huge)
    assert huge not in str(exc.value)


def test_a_21_digit_code_reaches_the_shared_validator_not_pydantic() -> None:
    """21 digits is under the schema's coarse boundary on purpose: the shared
    backend policy — not Pydantic — is the authority, so the caller gets the
    one static Thai message instead of a schema-shaped error."""
    from app.auth.plot_access_password import (
        PlotAccessPasswordPolicyError,
        validate_plot_access_password,
    )

    payload = PlotInspectionCredentialSet(password="1" * 21)   # schema accepts
    with pytest.raises(PlotAccessPasswordPolicyError):         # policy rejects
        validate_plot_access_password(payload.password.get_secret_value())


async def test_put_missing_pepper_is_a_controlled_503_not_a_500() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot() as plots, \
         patch(f"{_P}.build_plot_access_password_lookup_digest",
               side_effect=PlotAccessPepperMissingError("no pepper")), \
         patch(f"{_P}.asyncio.to_thread", AsyncMock()) as mk_thread, \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential", AsyncMock()) as mk_set:
        with pytest.raises(HTTPException) as exc:
            await set_plot_inspection_access_credential(
                plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 503
    assert "pepper" not in str(exc.value.detail).lower()   # no config detail leaked
    mk_set.assert_not_awaited()
    mk_thread.assert_not_awaited()      # the cheap check fails before bcrypt
    plots.locked.assert_not_awaited()


async def test_put_integrity_error_maps_to_clean_409() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await set_plot_inspection_access_credential(
                plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
            )
    assert exc.value.status_code == 409


async def test_put_passes_a_hash_and_digest_never_the_plaintext() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    user = _user()
    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())) as mk_set, \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=user, db=_db()
        )
    kwargs = mk_set.await_args.kwargs
    assert kwargs["password_hash"] != _PIN
    assert _PIN not in kwargs["password_hash"]
    assert kwargs["password_hash"].startswith("$2b$")   # a real bcrypt hash
    assert kwargs["password_lookup_digest"] == "a" * 64
    assert kwargs["updated_by_id"] == user.id
    assert set(kwargs) == {
        "password_hash", "password_lookup_digest", "updated_by_id"
    }   # the repository still receives no plaintext, by construction


async def test_put_response_carries_no_secret_field() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    with _patch_plot(), _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row())), \
         patch(f"{_P}.ActivityLogger", return_value=AsyncMock()):
        result = await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    body = result.model_dump(by_alias=True)
    assert set(body) == {"configured", "credentialVersion", "updatedAt"}
    assert _PIN not in str(body)


async def test_put_writes_a_high_risk_security_activity_log_without_secrets() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    logger = AsyncMock()
    with _patch_plot() as plots, _patch_helpers(), \
         patch(f"{_P}.credential_repo.set_or_replace_plot_credential",
               AsyncMock(return_value=_row(version=2))), \
         patch(f"{_P}.ActivityLogger", return_value=logger):
        await set_plot_inspection_access_credential(
            plot_id=uuid4(), payload=payload, current_user=_user(), db=_db()
        )
    plot = plots.plot
    kwargs = logger.log.await_args.kwargs
    assert kwargs["action"] == "plot.inspection_access_credential_set"
    assert kwargs["is_security_event"] is True
    assert kwargs["risk_level"] == "high"
    assert kwargs["resource_type"] == "plot"
    assert kwargs["resource_id"] == str(plot.id)
    logged = str(kwargs)
    for leaked in (_PIN, "$2b$12$", "a" * 64, "phone"):
        assert leaked not in logged


# --- request schema ---------------------------------------------------------

def test_request_password_is_a_secret_that_never_reprs_its_value() -> None:
    payload = PlotInspectionCredentialSet(password=_PIN)
    assert _PIN not in repr(payload)
    assert _PIN not in str(payload)
    assert payload.password.get_secret_value() == _PIN


def test_request_rejects_an_oversized_payload_without_echoing_it() -> None:
    from pydantic import ValidationError

    huge = "1" * 5000
    with pytest.raises(ValidationError) as exc:
        PlotInspectionCredentialSet(password=huge)
    assert huge not in str(exc.value)


def test_request_forbids_unknown_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlotInspectionCredentialSet(password=_PIN, credentialVersion=99)


def test_status_response_has_no_secret_fields_at_all() -> None:
    fields = set(PlotInspectionCredentialStatus.model_fields)
    assert fields == {"configured", "credential_version", "updated_at"}
    for banned in ("password", "password_hash", "lookup_digest", "pepper",
                   "password_last_digits"):
        assert banned not in fields


# --- permission / route wiring ----------------------------------------------

def _route(path: str, method: str):
    for r in plots_module.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _permission_keys(route) -> set[str]:
    """The PermissionKey values a route's require_permission dependencies close
    over (PermissionKey members are plain strings, e.g. 'plots.read')."""
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
            elif isinstance(cell.cell_contents, tuple):
                keys.update(c for c in cell.cell_contents if isinstance(c, str))
    return keys


def _dependency_names(route) -> set[str]:
    return {getattr(d.dependency, "__name__", "") for d in route.dependencies}


def test_get_requires_plots_read_and_rls() -> None:
    route = _route("/{plot_id}/inspection-access-credential", "GET")
    assert PermissionKey.PLOTS_READ in _permission_keys(route)
    assert "get_rls_context" in _dependency_names(route)


def test_put_requires_plots_update_and_rls() -> None:
    route = _route("/{plot_id}/inspection-access-credential", "PUT")
    assert PermissionKey.PLOTS_UPDATE in _permission_keys(route)
    assert "get_rls_context" in _dependency_names(route)


def test_put_does_not_settle_for_the_weaker_read_permission() -> None:
    route = _route("/{plot_id}/inspection-access-credential", "PUT")
    assert PermissionKey.PLOTS_READ not in _permission_keys(route)


def test_there_is_no_endpoint_that_reveals_or_deletes_a_password() -> None:
    src = Path(inspect.getfile(plots_module)).read_text(encoding="utf-8")
    # no DELETE route on the credential path, and no "reveal" style read
    assert "@router.delete" not in src
    assert "inspection-access-credential/reveal" not in src
    # the endpoint layer unwraps the SecretStr exactly once, and never reads a
    # stored hash/digest back off a credential row
    assert src.count("get_secret_value") == 1
    assert "row.password_hash" not in src
    assert "row.password_lookup_digest" not in src


def test_bcrypt_is_never_called_inline_in_the_async_endpoint() -> None:
    """Round 8-9A.1 source guard — the ONLY call site of the hashing helper is
    inside asyncio.to_thread. A future edit that "simplifies" it back to a
    direct call would silently reintroduce a 250ms event-loop stall."""
    src = Path(inspect.getfile(plots_module)).read_text(encoding="utf-8")
    assert "await asyncio.to_thread(hash_plot_access_password, pin)" in src
    # exactly one occurrence outside the import block
    body = src[src.index("router = APIRouter"):]
    assert body.count("hash_plot_access_password") == 1


def test_no_public_route_exposes_the_credential() -> None:
    """Round 8-9C wired the credential INTO the public flow (verification), so
    the public modules legitimately reference the repository now. What must
    still hold — and is the thing that actually matters — is that no public
    route serves the ADMIN credential endpoint and no public response model
    carries a credential field."""
    import app.api.v1.public_inspection_access as public_access
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    import app.schemas.phone_access as phone_access_schemas

    for mod in (public_access, public_plots, public_records):
        src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
        assert "inspection-access-credential" not in src   # no admin route here
        # The stored DIGEST column is never read by the public flow at all —
        # it is only ever COMPUTED from the submitted password and passed as a
        # query filter. (`build_plot_access_password_lookup_digest` contains
        # the substring, hence the attribute-access form here.)
        assert ".password_lookup_digest" not in src
        # The stored hash may be READ — that is what bcrypt verification is —
        # but only ever as the argument to verify_plot_access_password. It must
        # never be assigned, returned, or formatted anywhere else.
        for line in src.splitlines():
            if "password_hash" not in line or line.strip().startswith("#"):
                continue
            assert "verify_plot_access_password" in line or line.strip().endswith(
                "credential.password_hash"
            ), f"{mod.__name__}: unexpected password_hash use: {line.strip()!r}"

    # No public RESPONSE model may carry credential/secret fields.
    for name in dir(phone_access_schemas):
        model = getattr(phone_access_schemas, name)
        fields = getattr(model, "model_fields", None)
        if not fields or not name.startswith("PublicPhoneAccess"):
            continue
        if name.endswith("Request"):
            continue
        for banned in (
            "password", "password_hash", "lookup_digest",
            "credential_id", "credential_version", "phone",
        ):
            assert banned not in fields, f"{name} must not expose {banned}"
