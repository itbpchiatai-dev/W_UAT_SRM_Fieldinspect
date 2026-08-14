"""RecordCreate/RecordUpdate — submitted_by_code retirement (round 8-3G) +
submitted_by_name (the sole remaining, optional field-attribution input).

submitted_by_code used to be required + trim + non-blank + max-length
validated; round 8-3G drops it from both schemas entirely — no create flow
collects it anymore, and RecordUpdate has no live caller regardless (round
8.0.5 append-only lock). recorded_by_id (the audit/login user) is set
separately by the API layer from current_user, not from either payload —
unaffected by this round."""
from __future__ import annotations

import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.record import RecordCreate, RecordUpdate

_BASE = dict(
    plot_id=uuid4(),
    supplier_id=uuid4(),
    record_date=datetime.date(2026, 7, 1),
)


def test_record_create_has_no_submitted_by_code_field() -> None:
    assert "submitted_by_code" not in RecordCreate.model_fields


def test_record_update_has_no_submitted_by_code_field() -> None:
    assert "submitted_by_code" not in RecordUpdate.model_fields


def test_record_create_no_longer_requires_anything_beyond_plot_supplier_date() -> None:
    """A stray submittedByCode in the request is silently ignored (RecordCreate
    has no extra="forbid" — pre-existing lenient behavior, unchanged by this
    round) but the record itself never carries it."""
    record = RecordCreate.model_validate({**_BASE, "submittedByCode": "ST001"})
    assert not hasattr(record, "submitted_by_code")


def test_record_create_submitted_by_name_optional() -> None:
    record = RecordCreate(**_BASE)
    assert record.submitted_by_name is None


def test_record_create_trims_submitted_by_name_and_blank_becomes_none() -> None:
    record = RecordCreate(**_BASE, submitted_by_name="  Somchai  ")
    assert record.submitted_by_name == "Somchai"

    record2 = RecordCreate(**_BASE, submitted_by_name="   ")
    assert record2.submitted_by_name is None


def test_record_create_rejects_submitted_by_name_over_max_length() -> None:
    with pytest.raises(ValidationError):
        RecordCreate(**_BASE, submitted_by_name="x" * 256)


def test_record_create_does_not_expose_recorded_by_id() -> None:
    """recorded_by_id must come from current_user server-side, never from the
    client payload — RecordCreate has no such field to smuggle it through."""
    assert "recorded_by_id" not in RecordCreate.model_fields


def test_record_update_submitted_by_name_optional_when_omitted() -> None:
    update = RecordUpdate()
    assert update.submitted_by_name is None
    assert "submitted_by_name" not in update.model_fields_set


def test_record_update_accepts_explicit_null_submitted_by_name() -> None:
    update = RecordUpdate(submitted_by_name=None)
    assert update.submitted_by_name is None


def test_record_update_trims_submitted_by_name() -> None:
    update = RecordUpdate(submitted_by_name="  Somchai  ")
    assert update.submitted_by_name == "Somchai"


def test_record_create_accepts_camel_case_payload() -> None:
    """CamelBaseModel — API clients send camelCase JSON keys."""
    record = RecordCreate.model_validate({
        "plotId": str(_BASE["plot_id"]),
        "supplierId": str(_BASE["supplier_id"]),
        "recordDate": "2026-07-01",
        "submittedByName": "Somchai",
    })
    assert record.submitted_by_name == "Somchai"

    dumped = record.model_dump(by_alias=True)
    assert "submittedByName" in dumped
    assert "submittedByCode" not in dumped
