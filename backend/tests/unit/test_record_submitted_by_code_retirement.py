"""submitted_by_code retirement (round 8-3G) — read-schema nullability +
create-path null-storage, across both the logged-in and public flows.

submitted_by_code is dropped from every *write* schema (RecordCreate,
RecordUpdate, PublicRecordCreate) — see test_record_submitted_by_schema.py /
test_public_record_create_schema.py. This file covers the other half of the
contract: the *read* schemas (RecordRead/RecordSummary) must still accept
BOTH a historical non-null code (existing rows) and a null one (every new
row, since no create flow collects it anymore), and both create paths must
actually persist NULL rather than any synthesized value.
"""
from __future__ import annotations

import datetime
from uuid import uuid4

from app.schemas.record import RecordCreate, RecordRead, RecordSummary

_READ_BASE = dict(
    id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(), recorded_by_id=uuid4(),
    submitted_by_name=None,
    record_date=datetime.date(2026, 7, 1),
    crop=None, variety=None, growth_stage=None, planting_date=None, yield_pct=None,
    weather_condition=None, field_prep_score=None, weather_score=None,
    care_score=None, variety_resistance_score=None,
    recommendation=None, notes=None, latitude=None, longitude=None,
    photo_urls=[], custom_fields={}, is_active=True,
    created_at=datetime.datetime.now(datetime.timezone.utc),
    updated_at=datetime.datetime.now(datetime.timezone.utc),
)

_SUMMARY_BASE = dict(
    id=uuid4(), plot_id=uuid4(), supplier_id=uuid4(), recorded_by_id=uuid4(),
    submitted_by_name=None,
    record_date=datetime.date(2026, 7, 1),
    crop=None, variety=None, growth_stage=None, yield_pct=None,
    field_prep_score=None, weather_score=None, care_score=None,
    variety_resistance_score=None, is_active=True,
    created_at=datetime.datetime.now(datetime.timezone.utc),
)


def test_record_read_accepts_a_historical_non_null_code() -> None:
    record = RecordRead(**{**_READ_BASE, "submitted_by_code": "FIELD01"})
    assert record.submitted_by_code == "FIELD01"


def test_record_read_accepts_null_for_a_new_record() -> None:
    record = RecordRead(**{**_READ_BASE, "submitted_by_code": None})
    assert record.submitted_by_code is None


def test_record_summary_accepts_a_historical_non_null_code() -> None:
    record = RecordSummary(**{**_SUMMARY_BASE, "submitted_by_code": "FIELD01"})
    assert record.submitted_by_code == "FIELD01"


def test_record_summary_accepts_null_for_a_new_record() -> None:
    record = RecordSummary(**{**_SUMMARY_BASE, "submitted_by_code": None})
    assert record.submitted_by_code is None


def test_logged_in_create_payload_never_carries_a_submitted_by_code_to_persist() -> None:
    """record_repository.create_record does `Record(**payload.model_dump())`
    — proving RecordCreate's dump has no submitted_by_code key is sufficient
    to prove the DB insert leaves the (nullable) column unset -> NULL,
    without needing a live DB."""
    payload = RecordCreate(
        plot_id=uuid4(), supplier_id=uuid4(), record_date=datetime.date(2026, 7, 1),
    )
    dumped = payload.model_dump()
    assert "submitted_by_code" not in dumped


async def test_logged_in_create_record_constructs_record_with_no_submitted_by_code_kwarg() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.repositories.record_repository import create_record

    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    payload = RecordCreate(
        plot_id=uuid4(), supplier_id=uuid4(), record_date=datetime.date(2026, 7, 1),
    )
    record = await create_record(db, payload, recorded_by_id=uuid4())
    assert record.submitted_by_code is None


def test_public_record_create_payload_never_carries_a_submitted_by_code_to_persist() -> None:
    """Mirrors the logged-in check above for the public flow: PublicRecordCreate
    has no submitted_by_code field at all (see test_public_record_create_schema.py),
    so the RecordCreate _finish_creating_record rebuilds from its model_dump()
    can never carry one either."""
    from app.schemas.record import PublicRecordCreate

    payload = PublicRecordCreate(
        inspection_session_token="tok", record_date=datetime.date(2026, 7, 1),
    )
    dumped = payload.model_dump(exclude={"inspection_session_token"})
    assert "submitted_by_code" not in dumped
