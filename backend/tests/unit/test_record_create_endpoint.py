"""POST /api/v1/records (logged-in) — round 12 plot-snapshot-sync wiring.

No DB fixture exists in this repo — mocks the repository calls, matching
the established pattern in tests/unit/test_public_record_create_endpoint.py.
Unlike the public endpoint, create_record has no @limiter.limit decorator,
so it's callable directly with no .__wrapped__ unwrap needed.
"""
from __future__ import annotations

import datetime
import io
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.api.v1.records import create_record, create_record_with_photos
from app.schemas.record import RecordCreate
from app.services.inspection_photos import LocalPhotoStorage

_MODULE = "app.api.v1.records"


def _synthetic(fmt: str) -> bytes:
    """Round 8-14A — a REAL encoded image, not a magic-byte stub.

    The upload path now genuinely decodes and re-encodes every photo, so the
    old `b"\\xff\\xd8\\xff" + zeros` placeholders are (correctly) rejected as
    malformed. Synthesized in-process; no user photo is ever read.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (48, 36), (30, 120, 60)).save(buffer, format=fmt)
    return buffer.getvalue()


_JPEG = _synthetic("JPEG")
_PNG = _synthetic("PNG")
_WEBP = _synthetic("WEBP")


@pytest.fixture(autouse=True)
def _stub_protocol_map():
    """The create path loads the protocol config via get_protocol_map (round
    5.5); with no DB fixture, stub it with the built-in default map so these
    tests exercise creation without a real DB read."""
    from app.services.inspection_protocols import default_protocol_map
    with patch(f"{_MODULE}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


@pytest.fixture(autouse=True)
def _stub_active_cycle():
    """Round 7.1: _create_record resolves the plot's active planting cycle.
    Default every test to a plot that HAS one (returns a fake active cycle);
    the no-active-cycle / cycle-snapshot tests patch this target themselves to
    override."""
    with patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_fake_cycle())):
        yield


def _fake_request(host: str = "203.0.113.7"):
    """Request stand-in for get_client_ip: direct-hop client, no XFF —
    with no TRUSTED_PROXY_IPS configured the helper returns client.host."""
    return SimpleNamespace(client=SimpleNamespace(host=host), headers={})


def _current_user(**overrides):
    defaults = dict(id=uuid4())
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_record(**overrides):
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
        submitted_by_code="FIELD01", submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        plot=None, supplier=None, recorded_by=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_plot(**overrides):
    """Minimal plot for _create_record — supplier_id (derivation guard) and
    is_active (round 7.1 closed-plot guard) are read there. plot_code is
    read by create_record_with_photos (round 8-16B) to namespace the OBS
    storage key."""
    defaults = dict(id=uuid4(), supplier_id=uuid4(), is_active=True, plot_code="PLOT001")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_cycle(**overrides):
    """Minimal active PlotCycle for the round-7.1 record-create guard —
    id (→ record.plot_cycle_id) and crop/variety/planting_date (snapshot
    source) are read in _create_record. expected_yield_full/unit (round
    8-8A) default to None — the yield-kg derivation's "no comparable
    target" branch, matching every test in this file that doesn't send
    yield_quantity_kg."""
    defaults = dict(
        id=uuid4(), crop=None, variety=None, planting_date=None,
        expected_yield_full=None, expected_yield_unit=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _payload(**overrides) -> RecordCreate:
    defaults = dict(
        plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
        submitted_by_code="FIELD01",
    )
    defaults.update(overrides)
    return RecordCreate(**defaults)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


def _upload(content: bytes, filename: str = "photo.jpg") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _four_photos() -> list[UploadFile]:
    return [_upload(_JPEG), _upload(_PNG), _upload(_WEBP), _upload(_JPEG, "d.jpg")]


async def test_create_record_syncs_plot_snapshot_after_insert() -> None:
    plot_id = uuid4()
    payload = _payload(plot_id=plot_id)
    fake_record = _fake_record(plot_id=plot_id)
    current_user = _current_user()

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()) as mocked_sync, \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=current_user, db=_mock_db())

    mocked_create.assert_awaited_once()
    mocked_sync.assert_awaited_once()
    # Sync must run on the exact record create_record produced, not a
    # freshly re-fetched or re-derived one.
    sync_args, _ = mocked_sync.call_args
    assert sync_args[1] is fake_record


async def test_sync_runs_after_create_not_before() -> None:
    """Ordering matters: sync_current_status_from_record reads record.id/
    record.plot_id, which only exist once the record has actually been
    created."""
    call_order: list[str] = []

    async def _fake_create(*args, **kwargs):
        call_order.append("create")
        return _fake_record()

    async def _fake_sync(*args, **kwargs):
        call_order.append("sync")

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.repo.create_record", _fake_create), \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", _fake_sync), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=_payload(), current_user=_current_user(), db=_mock_db())

    assert call_order == ["create", "sync"]


async def test_sync_failure_propagates_instead_of_being_swallowed() -> None:
    """If the plot snapshot update fails, the exception must reach FastAPI's
    get_db dependency uncaught so it rolls back the whole transaction
    (including the record insert) — this proves create_record has no
    try/except around the sync call that would defeat that."""
    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock(side_effect=ValueError("plot not found"))):
        with pytest.raises(ValueError):
            await create_record(request=_fake_request(), payload=_payload(), current_user=_current_user(), db=_mock_db())


# --- round 13: POST /api/v1/records/with-photos -----------------------------

async def test_with_photos_saves_four_photos_and_creates_record(tmp_path: Path) -> None:
    plot_id = uuid4()
    fake_record = _fake_record(plot_id=plot_id)
    current_user = _current_user()
    payload_json = _payload(plot_id=plot_id).model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=fake_record.supplier_id))), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=fake_record.supplier_id))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=fake_record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record_with_photos(request=_fake_request(),
            current_user=current_user, payload=payload_json, photos=_four_photos(), db=_mock_db(),
        )

    mocked_create.assert_awaited_once()
    record_payload_arg = mocked_create.call_args[0][1]
    assert len(record_payload_arg.photo_urls) == 4
    assert all(u.startswith("/media/inspection-photos/") for u in record_payload_arg.photo_urls)
    assert len(list(tmp_path.iterdir())) == 4


async def test_with_photos_too_many_photos_rejected_before_create(tmp_path: Path) -> None:
    """Photos are optional (0..5) now — >5 is the remaining wrong-count case
    on this multipart path (a zero-photo submit uses the JSON endpoint).
    get_photo_storage is mocked to LocalPhotoStorage — real settings would
    otherwise decide which backend this exercises, which isn't this test's
    concern (it's asserting create_record never runs, not which storage
    backend get_photo_storage picks)."""
    payload_json = _payload().model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record_with_photos(request=_fake_request(),
                current_user=_current_user(), payload=payload_json,
                photos=[_upload(_JPEG)] * 6, db=_mock_db(),
            )

    assert exc_info.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_with_photos_malformed_json_payload_rejected_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await create_record_with_photos(request=_fake_request(), 
            current_user=_current_user(), payload="not json", photos=_four_photos(), db=_mock_db(),
        )

    assert exc_info.value.status_code == 422


async def test_with_photos_ignores_client_supplied_photo_urls(tmp_path: Path) -> None:
    """A client sending photoUrls inside the JSON payload must not have
    those values persisted — the 4 uploaded files always win."""
    plot_id = uuid4()
    fake_record = _fake_record(plot_id=plot_id)
    payload_json = _payload(plot_id=plot_id, photo_urls=["http://evil.example/x.jpg"]).model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=fake_record.supplier_id))), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=fake_record.supplier_id))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=fake_record)) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=fake_record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record_with_photos(request=_fake_request(),
            current_user=_current_user(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
        )

    record_payload_arg = mocked_create.call_args[0][1]
    assert "http://evil.example/x.jpg" not in record_payload_arg.photo_urls
    assert len(record_payload_arg.photo_urls) == 4


# --- round 13.1: orphan cleanup on DB failure --------------------------------

async def test_with_photos_cleans_up_saved_files_when_db_step_fails(tmp_path: Path) -> None:
    payload_json = _payload().model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(side_effect=RuntimeError("db exploded"))):
        with pytest.raises(RuntimeError):
            await create_record_with_photos(request=_fake_request(),
                current_user=_current_user(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )

    assert list(tmp_path.iterdir()) == []


async def test_with_photos_reraises_original_error_even_if_cleanup_itself_fails(
    tmp_path: Path,
) -> None:
    """Cleanup failing must never mask the real DB error with something
    about the cleanup instead."""
    payload_json = _payload().model_dump_json()

    with patch(f"{_MODULE}.get_photo_storage", MagicMock(
             return_value=LocalPhotoStorage(root=tmp_path, url_prefix="/media/inspection-photos")
         )), \
         patch(f"{_MODULE}.plot_repo.get_plot", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot())), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(side_effect=RuntimeError("db exploded"))), \
         patch(f"{_MODULE}.cleanup_photos", AsyncMock(side_effect=OSError("cleanup also failed"))):
        with pytest.raises(RuntimeError, match="db exploded"):
            await create_record_with_photos(request=_fake_request(),
                current_user=_current_user(), payload=payload_json, photos=_four_photos(), db=_mock_db(),
            )


# --- supplier is derived from the plot, never trusted from the client -------

async def test_create_record_overrides_client_supplier_with_the_plots_owner() -> None:
    """The admin form has a separate Supplier select + plot picker/QR scan,
    so a mismatched (supplier_id, plot_id) pair can be submitted. The record
    must be created with the PLOT's supplier, not the client-sent one —
    otherwise records leak across suppliers and pollute plot history."""
    plot_id = uuid4()
    true_supplier = uuid4()
    wrong_supplier = uuid4()
    payload = _payload(plot_id=plot_id, supplier_id=wrong_supplier)

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=true_supplier))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id, supplier_id=true_supplier))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created_payload = mocked_create.call_args[0][1]
    assert created_payload.supplier_id == true_supplier
    assert created_payload.supplier_id != wrong_supplier


async def test_create_record_404_when_plot_missing() -> None:
    """A plot_id that resolves to nothing (nonexistent, or outside the
    caller's RLS scope) is a generic 404 — never a created record."""
    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record(request=_fake_request(), payload=_payload(), current_user=_current_user(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


# --- submitted_ip audit capture ----------------------------------------------

async def test_create_record_stores_the_resolved_client_ip() -> None:
    """The endpoint passes get_client_ip(request) through to the repository
    as the submitted_ip kwarg — with no trusted proxies configured that is
    the direct peer address, never anything from the request body."""
    payload = _payload()
    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record())) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(
            request=_fake_request("198.51.100.42"), payload=payload,
            current_user=_current_user(), db=_mock_db(),
        )

    assert mocked_create.call_args.kwargs["submitted_ip"] == "198.51.100.42"


# --- round 7.1: record binds to the plot's active planting cycle -------------

async def test_create_record_assigns_active_plot_cycle_id() -> None:
    """The record is created with plot_cycle_id = the plot's active cycle,
    resolved server-side (never from the client)."""
    plot_id = uuid4()
    cycle = _fake_cycle()
    payload = _payload(plot_id=plot_id)

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert mocked_create.call_args.kwargs["plot_cycle_id"] == cycle.id


async def test_create_record_locks_the_active_cycle_row_not_the_unlocked_lookup() -> None:
    """Round 8.0.5 — logged-in create must take a row lock (SELECT ... FOR
    UPDATE) on the active cycle so a concurrent close/rollover can't race
    it, in the same transaction as the record insert + snapshot sync.
    Patches ONLY the locking variant; the module-level autouse fixture
    already proves every other test in this file goes through it too (they
    all pass without the unlocked get_active_cycle_for_plot being patched
    at all)."""
    plot_id = uuid4()
    cycle = _fake_cycle()
    payload = _payload(plot_id=plot_id)

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=cycle)) as mocked_locked_lookup, \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    mocked_locked_lookup.assert_awaited_once()
    assert mocked_locked_lookup.call_args.args[1] == plot_id
    mocked_create.assert_awaited_once()


async def test_create_record_rejects_inactive_plot_404() -> None:
    """A permanently-closed plot (is_active=false) takes no new records —
    generic 404, no record created, cycle never even looked up."""
    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(is_active=False))), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record(request=_fake_request(), payload=_payload(), current_user=_current_user(), db=_mock_db())

    assert exc_info.value.status_code == 404
    mocked_create.assert_not_awaited()


async def test_create_record_rejects_plot_with_no_active_cycle_409() -> None:
    """An active plot with no active planting cycle is 409 (distinct from the
    404 for a missing/closed plot) — no record created."""
    payload = _payload()
    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert exc_info.value.status_code == 409
    mocked_create.assert_not_awaited()


async def test_create_record_snapshots_crop_variety_from_cycle_not_client() -> None:
    """crop/variety/plantingDate come from the active cycle, not the client
    body — a client can't record a crop that disagrees with the cycle."""
    plot_id = uuid4()
    cycle = _fake_cycle(crop="เมล่อน", variety="ออร์เร้นจ์", planting_date=datetime.date(2026, 5, 1))
    payload = _payload(plot_id=plot_id, crop="EVIL", variety="SPOOF",
                       planting_date=datetime.date(2020, 1, 1))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created = mocked_create.call_args[0][1]
    assert created.crop == "เมล่อน"
    assert created.variety == "ออร์เร้นจ์"
    assert created.planting_date == datetime.date(2026, 5, 1)


# --- round 8-8A: yield-in-kg derivation wiring -------------------------------

async def test_create_record_derives_yield_pct_from_kg_quantity() -> None:
    """The active cycle's own expected_yield_full/unit (already locked/loaded
    above) is what the kg quantity is compared against — no extra query."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("1000"), expected_yield_unit="kg")
    payload = _payload(plot_id=plot_id, yield_pct=Decimal("1"), yield_quantity_kg=Decimal("800"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created_payload = mocked_create.call_args[0][1]
    # Server overwrote the client's fake yieldPct (1) with the derived 80.0.
    assert created_payload.yield_pct == Decimal("80.0")
    assert created_payload.yield_quantity_kg == Decimal("800.00")
    # yield_target_kg_snapshot is NOT a RecordCreate field — passed as its
    # own keyword-only arg to repo.create_record.
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] == Decimal("1000.00")


async def test_create_record_legacy_client_yield_pct_unaffected() -> None:
    """A client that never sends yieldQuantityKg keeps its yieldPct exactly
    as submitted — the round-8-8A wiring must be a no-op for it."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("1000"), expected_yield_unit="kg")
    payload = _payload(plot_id=plot_id, yield_pct=Decimal("42.5"))
    assert payload.yield_quantity_kg is None

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created_payload = mocked_create.call_args[0][1]
    assert created_payload.yield_pct == Decimal("42.5")
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] is None


# Round 8-8B.1 — rewritten (not deleted): real growers reported genuine
# harvests over 150% of plan, so this is no longer a 422 case. 150% is only
# a non-blocking frontend warning threshold now (lib/yield-planning.ts's
# YIELD_WARNING_PCT) — the logged-in create endpoint must accept and store
# it like any other value.
async def test_create_record_kg_over_150_percent_no_longer_rejected() -> None:
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("1000"), expected_yield_unit="kg")
    payload = _payload(plot_id=plot_id, yield_quantity_kg=Decimal("1600"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created_payload = mocked_create.call_args[0][1]
    assert created_payload.yield_pct == Decimal("160.0")
    assert created_payload.yield_quantity_kg == Decimal("1600.00")
    mocked_create.assert_awaited_once()


async def test_create_record_kg_over_9999_point_9_percent_rejected_422_before_insert() -> None:
    """The real technical ceiling now: NUMERIC(5,1) storage capacity."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("1000"), expected_yield_unit="kg")
    payload = _payload(plot_id=plot_id, yield_quantity_kg=Decimal("100000"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert exc_info.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_create_record_kg_with_non_weight_unit_keeps_quantity_nulls_pct() -> None:
    """Target unit is ผล (pieces) — not comparable; quantity still stored,
    pct/snapshot null, never a faked 100%."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("500"), expected_yield_unit="ผล")
    payload = _payload(plot_id=plot_id, yield_pct=Decimal("150"), yield_quantity_kg=Decimal("10"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record(plot_id=plot_id))), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created_payload = mocked_create.call_args[0][1]
    assert created_payload.yield_quantity_kg == Decimal("10.00")
    assert created_payload.yield_pct is None
    assert mocked_create.call_args.kwargs["yield_target_kg_snapshot"] is None


async def test_create_record_kg_target_overflow_rejected_422_before_insert() -> None:
    """Round 8-8A.1 — a converted target too large for NUMERIC(12,2) (e.g. a
    ตัน plan near the ceiling) must 422 BEFORE any insert, never surface as a
    DB overflow error."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("9999999999.99"), expected_yield_unit="ตัน")
    payload = _payload(plot_id=plot_id, yield_quantity_kg=Decimal("100"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert exc_info.value.status_code == 422
    assert "kg" in str(exc_info.value.detail)
    mocked_create.assert_not_awaited()


async def test_create_record_kg_pct_feeds_plot_snapshot_sync_via_record_yield_pct() -> None:
    """No new wiring needed in plot_repository — sync_current_status_from_
    record already reads record.yield_pct verbatim, which by the time it
    runs already holds the server-derived value (repo.create_record was
    called with it above). This proves sync still receives the record
    create_record actually produced, exercising the exact instance."""
    plot_id = uuid4()
    cycle = _fake_cycle(expected_yield_full=Decimal("1000"), expected_yield_unit="kg")
    payload = _payload(plot_id=plot_id, yield_quantity_kg=Decimal("800"))
    fake_record = _fake_record(plot_id=plot_id, yield_pct=Decimal("80.0"))

    with patch(f"{_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_MODULE}.repo.create_record", AsyncMock(return_value=fake_record)), \
         patch(f"{_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()) as mocked_sync, \
         patch(f"{_MODULE}.repo.get_record_full", AsyncMock(return_value=fake_record)), \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    mocked_sync.assert_awaited_once()
    synced_record = mocked_sync.call_args[0][1]
    assert synced_record is fake_record
    assert synced_record.yield_pct == Decimal("80.0")
