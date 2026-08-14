"""POST /api/v1/public/records — business logic.

No DB fixture exists in this repo — mocks the repository calls and
exercises the real token decode + RecordCreate re-validation logic
directly. Calls `.__wrapped__` to bypass the @limiter.limit slowapi
decorator (see tests/unit/test_public_plot_verify_endpoint.py for why).
"""
from __future__ import annotations

import datetime
import inspect
import io
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from jose import jwt
from PIL import Image

from app.api.v1.public_records import create_record_public, create_record_with_photos_public
from app.auth.inspection_session import encode_inspection_session_token
from app.auth.jwt_service import encode_access_token
from app.core.config import get_settings
from app.schemas.record import PublicRecordCreate
from app.services.inspection_photos import LocalPhotoStorage

_create = create_record_public.__wrapped__
_create_with_photos = create_record_with_photos_public.__wrapped__
_MODULE = "app.api.v1.public_records"


def _synthetic(fmt: str) -> bytes:
    """Round 8-14A — a REAL encoded image, not a magic-byte stub.

    The public upload path runs the same decode/re-encode pipeline as the
    logged-in one, so the old `b"\\xff\\xd8\\xff" + zeros` placeholders are
    (correctly) rejected as malformed. Synthesized in-process; no user photo
    is ever read.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (48, 36), (30, 120, 60)).save(buffer, format=fmt)
    return buffer.getvalue()


_JPEG = _synthetic("JPEG")
_PNG = _synthetic("PNG")
_WEBP = _synthetic("WEBP")


@pytest.fixture(autouse=True)
def _stub_protocol_map():
    """_finish_creating_record loads the protocol config via get_protocol_map
    (round 5.5); with no DB fixture, stub it with the built-in default map."""
    from app.services.inspection_protocols import default_protocol_map
    with patch(f"{_MODULE}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


@pytest.fixture(autouse=True)
def _stub_access_phone():
    """Round 8-3G: every inspection_session_token is phone-bound now (no
    legacy fallback), so _finish_creating_record always resolves + locks an
    access-phone row. Default every test to one matching access row so the
    plain create/no-photos happy-path tests (which predate the phone-binding
    feature and don't care about it) still pass unmodified; tests that DO
    care about the phone binding itself override this target or use
    test_public_record_phone_binding.py instead."""
    access = SimpleNamespace(id=_DEFAULT_ACCESS_PHONE_ID, phone_normalized="0812345678", access_type="primary")
    with patch(f"{_MODULE}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)):
        yield


@pytest.fixture(autouse=True)
def _stub_active_cycle():
    """Round 7.1: _verify_and_resolve resolves the plot's active planting
    cycle (unlocked). Round 8.0.5: _finish_creating_record re-locks it
    immediately before the insert and rejects if the id changed. Round 8-0.6:
    _verify_and_resolve ALSO rejects if the resolved active cycle's id no
    longer matches the token's plot_cycle_id claim. Default every test to a
    plot with ONE active cycle whose id is _DEFAULT_ACTIVE_CYCLE_ID (so the
    default `_token(...)` helper, which binds to that same id, matches), both
    lookups returning the SAME object; the no-active-cycle / stale-cycle /
    cycle-snapshot tests override these targets themselves."""
    cycle = _cycle(id=_DEFAULT_ACTIVE_CYCLE_ID)
    with patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)):
        yield


# Shared id for the autouse fixture's default active cycle and the default
# _token() below, so a token minted without an explicit plot_cycle_id matches
# the cycle the endpoint resolves under the default fixture (round 8-0.6).
_DEFAULT_ACTIVE_CYCLE_ID = uuid4()

# Shared id for the autouse _stub_access_phone fixture's default access row
# and the default _token() below (round 8-3G: phone binding is required on
# every token now — no legacy no-binding shape accepted).
_DEFAULT_ACCESS_PHONE_ID = uuid4()


def _token(plot, supplier, *, plot_cycle_id=None, plot_access_phone_id=None, inspector_type="farmer") -> str:
    """Mint an inspection_session_token bound to plot/supplier/active-cycle
    (round 8-0.6) AND an access phone/inspector type (round 8-3B, required
    since round 8-3G). Defaults plot_cycle_id/plot_access_phone_id to the
    autouse fixtures' matching defaults so ordinary tests "just match";
    stale-cycle tests pass an explicit plot_cycle_id that deliberately
    won't."""
    token, _ = encode_inspection_session_token(
        plot_id=plot.id,
        supplier_id=supplier.id,
        plot_cycle_id=plot_cycle_id if plot_cycle_id is not None else _DEFAULT_ACTIVE_CYCLE_ID,
        plot_access_phone_id=(
            plot_access_phone_id if plot_access_phone_id is not None else _DEFAULT_ACCESS_PHONE_ID
        ),
        inspector_type=inspector_type,
    )
    return token


def _cycle(**overrides):
    """Minimal active PlotCycle — id (→ record.plot_cycle_id) and crop/variety/
    planting_date (snapshot source) are read in the create flow. Defaults
    mirror _plot()'s current_* so tests that don't care are unaffected.
    expected_yield_full/unit (round 8-8A) default to None — the yield-kg
    derivation's "no comparable target" branch, matching every test in this
    file that doesn't send yield_quantity_kg."""
    defaults = dict(
        id=uuid4(),
        crop="พริก", variety="พริกขี้หนู", planting_date=datetime.date(2026, 1, 1),
        expected_yield_full=None, expected_yield_unit=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _supplier(**overrides):
    defaults = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plot(supplier_id, **overrides):
    defaults = dict(
        id=uuid4(), plot_code="PLOT001", name="Plot One", is_active=True,
        supplier_id=supplier_id,
        current_crop="พริก", current_variety="พริกขี้หนู",
        current_planting_date=datetime.date(2026, 1, 1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _system_user(**overrides):
    defaults = dict(id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_record(**overrides):
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), record_date=datetime.date(2026, 7, 1),
        submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        # Round 8-4A — the receipt now echoes these; NULL for an online record.
        client_submission_id=None, captured_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _payload(token: str, **overrides) -> PublicRecordCreate:
    defaults = dict(
        inspection_session_token=token,
        record_date=datetime.date(2026, 7, 1),
    )
    defaults.update(overrides)
    return PublicRecordCreate(**defaults)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


def _raw_token(claims: dict) -> str:
    settings = get_settings()
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _upload(content: bytes, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _four_photos() -> list[UploadFile]:
    return [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(_JPEG, "d.jpg")]


async def test_success_uses_plot_and_supplier_id_from_token() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    system_user = _system_user()
    fake_record = _fake_record(plot_id=plot.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=system_user)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        result = await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert result.plot_id == plot.id
    assert result.plot_code == plot.plot_code
    assert result.supplier_id == supplier.id
    assert result.supplier_code == supplier.code

    mocked_create.assert_awaited_once()
    args, kwargs = mocked_create.call_args
    record_payload_arg = args[1]
    assert record_payload_arg.plot_id == plot.id
    assert record_payload_arg.supplier_id == supplier.id
    assert kwargs["recorded_by_id"] == system_user.id


async def test_crop_variety_planting_date_snapshot_from_active_cycle_not_from_client() -> None:
    """Round 7.1 — crop/variety/planting_date on the record are snapshot from
    the plot's ACTIVE CYCLE (round 20.2 already removed them from
    PublicRecordCreate, so there's no client value to even consider). The
    active cycle, not the plot mirror, is now the source of truth."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle(
        crop="เมล่อน", variety="เมล่อนญี่ปุ่น", planting_date=datetime.date(2026, 3, 15),
    )
    token = _token(plot, supplier, plot_cycle_id=cycle.id)
    system_user = _system_user()
    fake_record = _fake_record(plot_id=plot.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=system_user)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.crop == "เมล่อน"
    assert record_payload_arg.variety == "เมล่อนญี่ปุ่น"
    assert record_payload_arg.planting_date == datetime.date(2026, 3, 15)


async def test_public_create_assigns_active_plot_cycle_id() -> None:
    """The record is created with plot_cycle_id = the plot's active cycle."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    token = _token(plot, supplier, plot_cycle_id=cycle.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert mocked_create.call_args.kwargs["plot_cycle_id"] == cycle.id


async def test_public_create_no_active_cycle_rejected_generic_404() -> None:
    """A plot with no active planting cycle is a generic 404 (same message as
    a missing/closed plot — doesn't leak that it's "between cycles"); no
    record is created."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_create.assert_not_awaited()


async def test_recorded_by_id_is_always_the_system_user_never_client_controlled() -> None:
    """PublicRecordCreate has no recorded_by_id field at all (see schema
    tests) — this additionally proves the endpoint wires the *looked-up*
    system user's id through, not anything derived from the request."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    system_user = _system_user()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=system_user)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert mocked_create.call_args.kwargs["recorded_by_id"] == system_user.id
    assert mocked_create.call_args.kwargs["recorded_by_id"] != plot.id
    assert mocked_create.call_args.kwargs["recorded_by_id"] != supplier.id


async def test_missing_system_user_returns_500_not_a_crash() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 500


async def test_expired_token_rejected_401() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    expired = _raw_token({
        "type": "inspection_session",
        "plot_id": str(uuid4()),
        "supplier_id": str(uuid4()),
        "iat": int((now - datetime.timedelta(minutes=40)).timestamp()),
        "exp": int((now - datetime.timedelta(minutes=10)).timestamp()),
        "jti": "x",
    })
    with pytest.raises(HTTPException) as exc_info:
        await _create(payload=_payload(expired), request=AsyncMock(), db=_mock_db())
    assert exc_info.value.status_code == 401


async def test_garbage_token_rejected_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _create(payload=_payload("not-a-jwt"), request=AsyncMock(), db=_mock_db())
    assert exc_info.value.status_code == 401


async def test_access_token_rejected_as_inspection_session_token() -> None:
    """A real login access token — signed with the same secret — must not
    be usable here either. Proves the `type` claim, not the secret, is
    what separates the two token kinds."""
    token = encode_access_token(subject=str(uuid4()), auth_provider="local")
    with pytest.raises(HTTPException) as exc_info:
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())
    assert exc_info.value.status_code == 401


async def test_token_with_malformed_plot_id_claim_rejected_401() -> None:
    bad = _raw_token({
        "type": "inspection_session",
        "plot_id": "not-a-uuid",
        "supplier_id": str(uuid4()),
        "iat": 0,
        "exp": 9999999999,
        "jti": "x",
    })
    with pytest.raises(HTTPException) as exc_info:
        await _create(payload=_payload(bad), request=AsyncMock(), db=_mock_db())
    assert exc_info.value.status_code == 401


async def test_inactive_supplier_rejected_generic_404() -> None:
    supplier = _supplier(is_active=False)
    plot = _plot(supplier.id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_inactive_plot_rejected_generic_404() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id, is_active=False)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404


async def test_plot_belonging_to_a_different_supplier_rejected_404() -> None:
    """Defense-in-depth beyond RLS's supplier-level check: even if a plot
    row came back, it must actually belong to the token's supplier_id."""
    supplier = _supplier()
    other_supplier_id = uuid4()
    plot = _plot(other_supplier_id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404


async def test_blank_submitted_by_name_after_trim_becomes_none_not_422() -> None:
    """Round 8-3G: submitted_by_code (previously required, non-blank) is
    retired. submitted_by_name is the sole remaining attribution input and
    is optional — a blank/whitespace value trims to None rather than
    rejecting, and record creation still succeeds."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    system_user = _system_user()
    fake_record = _fake_record(plot_id=plot.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=system_user)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=fake_record)) as mk, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(
            payload=_payload(token, submitted_by_name="   "),
            request=AsyncMock(), db=_mock_db(),
        )

    record_payload = mk.call_args.args[1]
    assert record_payload.submitted_by_name is None


async def test_rls_context_is_set_to_supplier_scope_from_token_not_all() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))), \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()) as mocked_rls:
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    mocked_rls.assert_awaited_once()
    args, _ = mocked_rls.call_args
    assert args[1] == supplier.id


async def test_syncs_plot_snapshot_from_the_created_record() -> None:
    """round 12: plot.current_* must be synced from exactly the record this
    call just created, after create_record, not before or from something
    else."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    fake_record = _fake_record(plot_id=plot.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()) as mocked_sync, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    mocked_create.assert_awaited_once()
    mocked_sync.assert_awaited_once()
    sync_args, _ = mocked_sync.call_args
    assert sync_args[1] is fake_record


async def test_sync_failure_propagates_instead_of_being_swallowed() -> None:
    """Proves the public create flow has no try/except around the sync
    call — a failure here must reach get_db's dependency uncaught so it
    rolls back the whole transaction, including the record insert."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(
             f"{_MODULE}.plot_repo.sync_current_status_from_record",
             AsyncMock(side_effect=ValueError("plot not found")),
         ):
        with pytest.raises(ValueError):
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())


# --- round 13: POST /api/v1/public/records/with-photos -----------------------

async def test_with_photos_success_saves_four_photos_onto_the_record(tmp_path: Path) -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    system_user = _system_user()
    fake_record = _fake_record(plot_id=plot.id)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=system_user)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        await _create_with_photos(
            request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
        )

    mocked_create.assert_awaited_once()
    record_payload_arg = mocked_create.call_args[0][1]
    assert len(record_payload_arg.photo_urls) == 4
    assert len(list(tmp_path.iterdir())) == 4


async def test_with_photos_too_many_photos_rejected_422_before_touching_db() -> None:
    """Photos are optional (0..5) now — >5 is the remaining wrong-count case
    on this multipart path (a zero-photo submit uses the JSON endpoint)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json,
                photos=[_upload(_JPEG)] * 6, db=_mock_db(),
            )

    assert exc_info.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_with_photos_non_image_file_rejected_400() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()
    photos = [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(b"not-an-image", "d.txt")]

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=photos, db=_mock_db(),
            )

    assert exc_info.value.status_code == 400


async def test_with_photos_invalid_token_rejected_401_before_saving_any_photo(tmp_path: Path) -> None:
    """Token verification must run before photos ever touch disk — otherwise
    this endpoint would let an attacker with no valid token still burn disk
    space/IO on every request (see _verify_and_resolve's docstring)."""
    payload_json = _payload("not-a-jwt").model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert exc_info.value.status_code == 401
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_with_photos_malformed_json_payload_rejected_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await _create_with_photos(
            request=AsyncMock(), payload="not json", photos=_four_photos(), db=_mock_db(),
        )

    assert exc_info.value.status_code == 422


async def test_with_photos_no_active_cycle_404_before_saving_any_photo(tmp_path: Path) -> None:
    """The active-cycle guard runs inside _verify_and_resolve, BEFORE photos
    are saved — a plot with no active cycle must 404 without a disk write
    (same disk-oracle protection as the invalid-token case)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert exc_info.value.status_code == 404
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_with_photos_still_requires_inspection_session_token_scope() -> None:
    """The multipart endpoint must gate on the same token as the JSON one —
    an inactive supplier is rejected identically."""
    supplier = _supplier(is_active=False)
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert exc_info.value.status_code == 404


# --- round 13.1: orphan cleanup on DB failure --------------------------------

async def test_with_photos_cleans_up_saved_files_when_db_step_fails(tmp_path: Path) -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(side_effect=RuntimeError("db exploded"))), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        with pytest.raises(RuntimeError):
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert list(tmp_path.iterdir()) == []


async def test_with_photos_reraises_original_error_even_if_cleanup_itself_fails(
    tmp_path: Path,
) -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(side_effect=RuntimeError("db exploded"))), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.cleanup_photos", AsyncMock(side_effect=OSError("cleanup also failed"))):
        with pytest.raises(RuntimeError, match="db exploded"):
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )


# --- round 8.0.5: re-lock the active cycle immediately before insert -------

async def test_public_create_rejects_when_cycle_rolled_over_between_resolve_and_insert() -> None:
    """Simulates a rollover racing in between _verify_and_resolve (which
    resolved `cycle`, before any photo write) and _finish_creating_record's
    re-lock, immediately before the insert: the re-locked cycle has a
    DIFFERENT id, so the request must be rejected with the same generic 404
    the rest of this flow uses, and create_record must never be called."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    resolved_cycle = _cycle()
    rolled_over_cycle = _cycle()  # different id — simulates a rollover mid-flight
    # Bind the token to the cycle _verify_and_resolve will see, so the request
    # gets PAST the round-8-0.6 verify-time cycle-match guard and is rejected
    # specifically by the round-8.0.5 re-lock (the scenario under test).
    token = _token(plot, supplier, plot_cycle_id=resolved_cycle.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=resolved_cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=rolled_over_cycle)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_create.assert_not_awaited()


async def test_public_create_rejects_when_cycle_closed_between_resolve_and_insert() -> None:
    """Same window, but the cycle simply closed with no replacement."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    resolved_cycle = _cycle()
    token = _token(plot, supplier, plot_cycle_id=resolved_cycle.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=resolved_cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


async def test_with_photos_cleans_up_photos_when_cycle_changed_between_resolve_and_insert(
    tmp_path: Path,
) -> None:
    """The round-8.0.5 re-lock rejection happens AFTER photos are already
    saved to disk (validate_and_save_photos runs before _finish_creating_
    record) — the with-photos endpoint's existing cleanup wrapper must still
    delete them, same as any other post-upload failure."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    resolved_cycle = _cycle()
    rolled_over_cycle = _cycle()
    token = _token(plot, supplier, plot_cycle_id=resolved_cycle.id)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=resolved_cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=rolled_over_cycle)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert exc_info.value.status_code == 404
    assert list(tmp_path.iterdir()) == []


# --- round 8-0.6: token bound to the plot's active cycle at mint time -------

async def test_token_bound_to_cycle1_creates_record_when_cycle1_still_active() -> None:
    """The happy path: a token minted against cycle 1 submits while cycle 1 is
    still the plot's active cycle — the record is created and bound to it."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle1 = _cycle()
    token = _token(plot, supplier, plot_cycle_id=cycle1.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle1)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle1)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    mocked_create.assert_awaited_once()
    assert mocked_create.call_args.kwargs["plot_cycle_id"] == cycle1.id


async def test_token_bound_to_cycle1_rejected_404_after_rollover_to_cycle2() -> None:
    """The core round-8-0.6 guarantee: a token minted against cycle 1 must NOT
    submit after the plot rolled over to cycle 2. The active cycle's id no
    longer matches the token's plot_cycle_id → generic 404, no record, and
    NO fallback to cycle 2."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle1 = _cycle()
    cycle2 = _cycle()  # the plot's NEW active cycle after a rollover
    token = _token(plot, supplier, plot_cycle_id=cycle1.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle2)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_create.assert_not_awaited()


async def test_token_bound_to_cycle1_rejected_404_when_no_active_cycle() -> None:
    """A token bound to cycle 1 submitted after the plot closed cycle 1 with no
    replacement — generic 404, same as any other no-active-cycle case."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle1 = _cycle()
    token = _token(plot, supplier, plot_cycle_id=cycle1.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


async def test_token_cycle_id_for_a_cycle_of_a_different_plot_rejected_404() -> None:
    """A token whose plot_cycle_id names a cycle that isn't this plot's current
    active cycle (e.g. copied from another plot's token) is rejected — the
    active-cycle-id match is what gates it, not just plot/supplier scope."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    this_plots_active_cycle = _cycle()
    other_plots_cycle_id = uuid4()
    token = _token(plot, supplier, plot_cycle_id=other_plots_cycle_id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=this_plots_active_cycle)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


async def test_old_token_without_plot_cycle_id_claim_rejected_401_fail_closed() -> None:
    """A pre-8-0.6 token has no plot_cycle_id claim — it must fail closed with
    a generic 401, not be treated as "matches any cycle"."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    old_token = _raw_token({
        "type": "inspection_session",
        "plot_id": str(plot.id),
        "supplier_id": str(supplier.id),
        # deliberately no plot_cycle_id
        "iat": 0,
        "exp": 9999999999,
        "jti": "x",
    })

    with patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(old_token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired inspection session token"
    mocked_create.assert_not_awaited()


async def test_token_with_malformed_plot_cycle_id_claim_rejected_401() -> None:
    """A plot_cycle_id claim that isn't a UUID is a generic 401 (same as any
    other malformed claim)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    bad = _raw_token({
        "type": "inspection_session",
        "plot_id": str(plot.id),
        "supplier_id": str(supplier.id),
        "plot_cycle_id": "not-a-uuid",
        "iat": 0,
        "exp": 9999999999,
        "jti": "x",
    })
    with pytest.raises(HTTPException) as exc_info:
        await _create(payload=_payload(bad), request=AsyncMock(), db=_mock_db())
    assert exc_info.value.status_code == 401


async def test_same_cycle_but_plan_edited_uses_latest_locked_cycle_values() -> None:
    """The cycle didn't change (same id, token still valid) but its plan was
    edited between verify and submit — the record snapshots the LATEST values
    from the re-locked cycle, not any stale copy."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle_id = uuid4()
    # Same id in both lookups (cycle unchanged) but the re-locked snapshot
    # carries edited crop/variety/planting_date.
    resolved = _cycle(id=cycle_id, crop="พริก", variety="พริกเดิม",
                      planting_date=datetime.date(2026, 1, 1))
    relocked = _cycle(id=cycle_id, crop="เมล่อน", variety="เมล่อนใหม่",
                      planting_date=datetime.date(2026, 5, 20))
    token = _token(plot, supplier, plot_cycle_id=cycle_id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=resolved)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=relocked)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.crop == "เมล่อน"
    assert record_payload_arg.variety == "เมล่อนใหม่"
    assert record_payload_arg.planting_date == datetime.date(2026, 5, 20)
    assert mocked_create.call_args.kwargs["plot_cycle_id"] == cycle_id


async def test_with_photos_stale_cycle_at_verify_cleans_up_photos_404(tmp_path: Path) -> None:
    """The round-8-0.6 verify-time cycle-mismatch (token bound to cycle 1,
    plot now on cycle 2) is detected in _verify_and_resolve — which runs
    BEFORE photos are saved, so no disk write happens and the tmp dir stays
    empty (same disk-oracle protection as the invalid-token case)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle1 = _cycle()
    cycle2 = _cycle()
    token = _token(plot, supplier, plot_cycle_id=cycle1.id)
    payload_json = _payload(token).model_dump_json()

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle2)), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()), \
         patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )):
        with pytest.raises(HTTPException) as exc_info:
            await _create_with_photos(
                request=AsyncMock(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert exc_info.value.status_code == 404
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


# --- round 8.0.7: plot re-locked + revalidated before the cycle lock -------

async def test_public_create_rejects_when_plot_deactivated_between_resolve_and_insert() -> None:
    """The plot passed _verify_and_resolve's is_active check, but was
    deactivated by a concurrent transaction before _finish_creating_record's
    re-lock — generic 404, no record created, cycle never even re-locked."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    now_inactive = _plot(supplier.id, id=plot.id, is_active=False)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=now_inactive)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock()) as mocked_cycle_lock, \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_cycle_lock.assert_not_awaited()
    mocked_create.assert_not_awaited()


async def test_public_create_rejects_when_plot_reassigned_to_another_supplier_between_resolve_and_insert() -> None:
    """Defense-in-depth mirror of test_plot_belonging_to_a_different_supplier_
    rejected_404, but for the re-locked read: ownership must still match at
    insert time, not just at verify time."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    token = _token(plot, supplier)
    now_other_supplier = _plot(uuid4(), id=plot.id)

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=now_other_supplier)), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


async def test_finish_creating_record_locks_plot_before_cycle_call_order() -> None:
    """Round 8.0.7 — _finish_creating_record must lock the plot BEFORE the
    cycle, proven by actual call order (not just source position)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    token = _token(plot, supplier, plot_cycle_id=cycle.id)
    order: list[str] = []

    async def _lock_plot(*a, **k):
        order.append("lock_plot")
        return plot

    async def _lock_cycle(*a, **k):
        order.append("lock_cycle")
        return cycle

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", side_effect=_lock_plot), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", side_effect=_lock_cycle), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))), \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_mock_db())

    assert order == ["lock_plot", "lock_cycle"]


def test_finish_creating_record_locks_plot_before_cycle_in_source() -> None:
    """Structural guard mirroring the lifecycle-endpoint version in
    test_plot_cycle_lifecycle.py — anchored on `await ...` so docstring
    prose can't make it pass vacuously."""
    import app.api.v1.public_records as public_records_module
    src = inspect.getsource(public_records_module._finish_creating_record)
    assert "await plot_repo.get_plot_for_update" in src
    assert "await plot_cycle_repo.get_active_cycle_for_plot_for_update" in src
    assert src.index("await plot_repo.get_plot_for_update") < src.index(
        "await plot_cycle_repo.get_active_cycle_for_plot_for_update"
    )


# --- round 8-8A: yield-in-kg derivation wiring (public flow) ---------------

def _kg_setup(*, expected_yield_full, expected_yield_unit, yield_pct=None, yield_quantity_kg):
    """Common fixture wiring for the yield-kg tests below: a plot/supplier/
    cycle (the cycle carries the kg target) and a token bound to that cycle,
    using the SAME cycle object for both the unlocked and locked lookups
    (matching the module's own re-lock-then-compare-id contract)."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle(
        id=_DEFAULT_ACTIVE_CYCLE_ID,
        expected_yield_full=expected_yield_full, expected_yield_unit=expected_yield_unit,
    )
    token = _token(plot, supplier, plot_cycle_id=cycle.id)
    payload = _payload(token, yield_pct=yield_pct, yield_quantity_kg=yield_quantity_kg) \
        if yield_pct is not None else _payload(token, yield_quantity_kg=yield_quantity_kg)
    return supplier, plot, cycle, payload


async def test_public_create_derives_yield_pct_from_kg_quantity() -> None:
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        yield_pct=Decimal("1"), yield_quantity_kg=Decimal("800"),
    )

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.yield_pct == Decimal("80.0")
    assert record_payload_arg.yield_quantity_kg == Decimal("800.00")
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] == Decimal("1000.00")


async def test_public_create_legacy_client_yield_pct_unaffected() -> None:
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        yield_pct=Decimal("42.5"), yield_quantity_kg=None,
    )
    assert payload.yield_quantity_kg is None

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.yield_pct == Decimal("42.5")
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] is None


# Round 8-8B.1 — rewritten (not deleted): 150% is a non-blocking frontend
# warning threshold only now (lib/yield-planning.ts's YIELD_WARNING_PCT) —
# the public create endpoint must accept and store it, same as logged-in.
async def test_public_create_kg_over_150_percent_no_longer_rejected() -> None:
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        yield_quantity_kg=Decimal("1600"),
    )

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.yield_pct == Decimal("160.0")
    assert record_payload_arg.yield_quantity_kg == Decimal("1600.00")
    mocked_create.assert_awaited_once()


async def test_public_create_kg_over_9999_point_9_percent_rejected_422_before_insert() -> None:
    """The real technical ceiling now: NUMERIC(5,1) storage capacity."""
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        yield_quantity_kg=Decimal("100000"),
    )

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_public_create_kg_with_non_weight_unit_keeps_quantity_nulls_pct() -> None:
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("500"), expected_yield_unit="ผล",
        yield_pct=Decimal("100"), yield_quantity_kg=Decimal("10"),
    )

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    record_payload_arg = mocked_create.call_args[0][1]
    assert record_payload_arg.yield_quantity_kg == Decimal("10.00")
    assert record_payload_arg.yield_pct is None
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] is None


async def test_public_create_kg_target_overflow_rejected_422_before_insert() -> None:
    """Round 8-8A.1 — same NUMERIC(12,2) overflow guard as the logged-in
    flow, exercised through the public path."""
    supplier, plot, cycle, payload = _kg_setup(
        expected_yield_full=Decimal("9999999999.99"), expected_yield_unit="ตัน",
        yield_quantity_kg=Decimal("100"),
    )

    with patch(f"{_MODULE}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await _create(payload=payload, request=AsyncMock(), db=_mock_db())

    assert exc_info.value.status_code == 422
    assert "kg" in str(exc_info.value.detail)
    mocked_create.assert_not_awaited()


async def test_public_create_logged_in_and_public_use_the_same_derive_yield_function() -> None:
    """Part C's "single shared helper" contract, proven directly: both
    endpoint modules import the exact same function object."""
    import app.api.v1.public_records as public_records_module
    import app.api.v1.records as records_module

    assert (
        public_records_module.yield_calculation.derive_yield
        is records_module.yield_calculation.derive_yield
    )
