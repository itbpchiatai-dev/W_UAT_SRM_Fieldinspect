"""Protocol snapshot is written server-side on record create — both the
logged-in and the public flow, with the gated contract enforced end-to-end
at the endpoint layer (apply_protocol_snapshot runs for real; only the repo
and plot lookups are mocked).
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.public_records import _finish_creating_record
from app.api.v1.records import create_record
from app.schemas.record import PublicRecordCreate, RecordCreate
from app.services.inspection_protocols import SNAPSHOT_KEY, default_protocol_map

_REC_MODULE = "app.api.v1.records"
_PUB_MODULE = "app.api.v1.public_records"


@pytest.fixture(autouse=True)
def _stub_protocol_map():
    """Both create paths now load the protocol from the DB config
    (get_protocol_map). With no DB fixture here, stub it with the built-in
    default map so apply_protocol_snapshot still runs for real against a
    realistic map."""
    with patch(f"{_REC_MODULE}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())), \
         patch(f"{_PUB_MODULE}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


@pytest.fixture(autouse=True)
def _stub_active_cycle():
    """Round 7.1/8.0.5: the logged-in create_record path row-locks the
    plot's active cycle. (The public tests call _finish_creating_record
    directly and pass a cycle explicitly, then patch its own re-lock call
    themselves, so only the logged-in module's lookup is stubbed here.)"""
    with patch(f"{_REC_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update",
               AsyncMock(return_value=_fake_cycle())):
        yield


def _fake_cycle(**overrides):
    defaults = dict(
        id=uuid4(), crop="พริก", variety="พริกขี้หนู",
        planting_date=datetime.date(2026, 1, 1),
        # Round 8-8A — no comparable kg target by default (no test in this
        # file sends yield_quantity_kg).
        expected_yield_full=None, expected_yield_unit=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_request(host: str = "203.0.113.7"):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers={})


def _current_user(**overrides):
    return SimpleNamespace(id=uuid4(), **overrides)


def _fake_plot(**overrides):
    defaults = dict(
        id=uuid4(), supplier_id=uuid4(),
        plot_code="PLOT001", name="Plot One", is_active=True,
        current_crop="พริก", current_variety="พริกขี้หนู",
        current_planting_date=datetime.date(2026, 1, 1),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_supplier(**overrides):
    defaults = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_record(**overrides):
    defaults = dict(
        id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
        submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        # Round 8-4A — the public receipt now echoes these (NULL online).
        client_submission_id=None, captured_at=None,
        plot=None, supplier=None, recorded_by=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_access(**overrides):
    defaults = dict(id=uuid4(), phone_normalized="0812345678", access_type="primary")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


def _rec_payload(**overrides) -> RecordCreate:
    defaults = dict(
        plot_id=uuid4(), supplier_id=uuid4(),
        record_date=datetime.date(2026, 7, 1),
    )
    defaults.update(overrides)
    return RecordCreate(**defaults)


def _protocol_scores() -> dict:
    return dict(field_prep_score=8, weather_score=7, care_score=9, variety_resistance_score=6)


# --- logged-in POST /api/v1/records ------------------------------------------

async def test_logged_in_create_writes_protocol_snapshot_server_side() -> None:
    plot_id = uuid4()
    payload = _rec_payload(plot_id=plot_id, growth_stage="ระยะงอก", **_protocol_scores())

    with patch(f"{_REC_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_REC_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_REC_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_REC_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_REC_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    created = mocked_create.call_args[0][1]
    snap = created.custom_fields[SNAPSHOT_KEY]
    assert snap["version"] == 1
    assert snap["growthStage"] == "ระยะงอก"
    assert [c["score"] for c in snap["criteria"]] == [8, 7, 9, 6]
    assert snap["criteria"][0]["label"] == "การเตรียมแปลง"


async def test_logged_in_create_rejects_a_protocol_stage_missing_a_score_422() -> None:
    payload = _rec_payload(growth_stage="ระยะงอก", field_prep_score=8, weather_score=7, care_score=9)

    with patch(f"{_REC_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(supplier_id=payload.supplier_id))), \
         patch(f"{_REC_MODULE}.repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc:
            await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert exc.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_logged_in_create_no_snapshot_for_non_protocol_stage() -> None:
    # gated pass-through — a supplement stage with no scores still creates.
    payload = _rec_payload(growth_stage="ตั้งตัว")

    with patch(f"{_REC_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(supplier_id=payload.supplier_id))), \
         patch(f"{_REC_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record())) as mocked_create, \
         patch(f"{_REC_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_REC_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_REC_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    assert SNAPSHOT_KEY not in mocked_create.call_args[0][1].custom_fields


async def test_logged_in_create_cannot_be_spoofed_by_client_custom_fields() -> None:
    plot_id = uuid4()
    forged = {"version": 99, "growthStage": "เก็บเกี่ยว", "criteria": [{"slot": "x", "label": "hack", "score": 1}]}
    payload = _rec_payload(
        plot_id=plot_id, growth_stage="ระยะงอก",
        custom_fields={SNAPSHOT_KEY: forged}, **_protocol_scores(),
    )

    with patch(f"{_REC_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=_fake_plot(id=plot_id, supplier_id=payload.supplier_id))), \
         patch(f"{_REC_MODULE}.repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot_id))) as mocked_create, \
         patch(f"{_REC_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_REC_MODULE}.repo.get_record_full", AsyncMock(return_value=_fake_record())), \
         patch(f"{_REC_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await create_record(request=_fake_request(), payload=payload, current_user=_current_user(), db=_mock_db())

    snap = mocked_create.call_args[0][1].custom_fields[SNAPSHOT_KEY]
    assert snap["version"] == 1
    assert snap["growthStage"] == "ระยะงอก"
    assert snap["criteria"][0]["label"] == "การเตรียมแปลง"


# --- public POST /api/v1/public/records --------------------------------------

def _pub_payload(**overrides) -> PublicRecordCreate:
    defaults = dict(
        inspection_session_token="tok",
        record_date=datetime.date(2026, 7, 1),
    )
    defaults.update(overrides)
    return PublicRecordCreate(**defaults)


# Round 8-3G: _finish_creating_record's phone_binding is now a required
# keyword-only arg (every real token is phone-bound) — these tests call it
# directly (bypassing _verify_and_resolve), so they supply a fixed dummy
# binding + mock the access-row lookup it triggers. Not the focus of this
# file (protocol-snapshot behavior), so kept minimal and identical everywhere.
_PHONE_BINDING = (uuid4(), "farmer")


async def test_public_create_writes_protocol_snapshot_server_side() -> None:
    supplier = _fake_supplier()
    plot = _fake_plot(supplier_id=supplier.id)
    cycle = _fake_cycle()
    payload = _pub_payload(growth_stage="ออกดอก", **_protocol_scores())

    with patch(f"{_PUB_MODULE}.get_external_submission_user", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_PUB_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_PUB_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_PUB_MODULE}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=_fake_access())), \
         patch(f"{_PUB_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_PUB_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()):
        await _finish_creating_record(_mock_db(), payload, plot, supplier, cycle, phone_binding=_PHONE_BINDING)

    snap = mocked_create.call_args[0][1].custom_fields[SNAPSHOT_KEY]
    assert snap["version"] == 1
    assert snap["growthStage"] == "ออกดอก"
    assert snap["criteria"][0]["label"] == "ความสมบูรณ์ของดอก"
    assert [c["score"] for c in snap["criteria"]] == [8, 7, 9, 6]


async def test_public_create_rejects_a_protocol_stage_missing_a_score_422() -> None:
    supplier = _fake_supplier()
    plot = _fake_plot(supplier_id=supplier.id)
    cycle = _fake_cycle()
    payload = _pub_payload(growth_stage="ออกดอก", field_prep_score=8)

    with patch(f"{_PUB_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_PUB_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_PUB_MODULE}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=_fake_access())), \
         patch(f"{_PUB_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc:
            await _finish_creating_record(_mock_db(), payload, plot, supplier, cycle, phone_binding=_PHONE_BINDING)

    assert exc.value.status_code == 422
    mocked_create.assert_not_awaited()


async def test_public_create_cannot_be_spoofed_by_client_custom_fields() -> None:
    supplier = _fake_supplier()
    plot = _fake_plot(supplier_id=supplier.id)
    cycle = _fake_cycle()
    forged = {"version": 99, "growthStage": "เก็บเกี่ยว"}
    payload = _pub_payload(
        growth_stage="ออกดอก", custom_fields={SNAPSHOT_KEY: forged}, **_protocol_scores(),
    )

    with patch(f"{_PUB_MODULE}.get_external_submission_user", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_PUB_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_PUB_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_PUB_MODULE}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=_fake_access())), \
         patch(f"{_PUB_MODULE}.record_repo.create_record", AsyncMock(return_value=_fake_record(plot_id=plot.id))) as mocked_create, \
         patch(f"{_PUB_MODULE}.plot_repo.sync_current_status_from_record", AsyncMock()):
        await _finish_creating_record(_mock_db(), payload, plot, supplier, cycle, phone_binding=_PHONE_BINDING)

    snap = mocked_create.call_args[0][1].custom_fields[SNAPSHOT_KEY]
    assert snap["version"] == 1
    assert snap["growthStage"] == "ออกดอก"


async def test_public_create_score_out_of_range_rejected_422() -> None:
    """PublicRecordCreate has no score range constraint of its own; the 1-10
    bound is enforced when the endpoint rebuilds RecordCreate."""
    supplier = _fake_supplier()
    plot = _fake_plot(supplier_id=supplier.id)
    cycle = _fake_cycle()
    payload = _pub_payload(growth_stage="ออกดอก", field_prep_score=11,
                           weather_score=7, care_score=9, variety_resistance_score=6)

    with patch(f"{_PUB_MODULE}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_PUB_MODULE}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_PUB_MODULE}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=_fake_access())), \
         patch(f"{_PUB_MODULE}.record_repo.create_record", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc:
            await _finish_creating_record(_mock_db(), payload, plot, supplier, cycle, phone_binding=_PHONE_BINDING)

    assert exc.value.status_code == 422
    mocked_create.assert_not_awaited()
