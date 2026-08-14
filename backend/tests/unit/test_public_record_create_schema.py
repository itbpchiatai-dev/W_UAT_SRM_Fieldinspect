"""PublicRecordCreate — extra="forbid" must reject client-supplied
plotId/supplierId (round 8's "reject, don't ignore" decision).
submittedByCode is retired (round 8-3G) — the schema no longer has the
field at all, so a client that still sends it now gets the same 422 as
any other stray/unrecognized field (extra="forbid")."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.record import PublicRecordCreate

_BASE = {
    "inspectionSessionToken": "x",
    "recordDate": "2026-07-01",
}


def test_accepts_valid_minimal_payload() -> None:
    payload = PublicRecordCreate.model_validate(_BASE)
    assert payload.inspection_session_token == "x"
    assert payload.submitted_by_name is None


def test_rejects_client_supplied_plot_id() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "plotId": str(uuid4())})


def test_rejects_client_supplied_supplier_id() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "supplierId": str(uuid4())})


def test_rejects_client_supplied_recorded_by_id() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "recordedById": str(uuid4())})


def test_has_no_submitted_by_code_field_at_all() -> None:
    assert "submitted_by_code" not in PublicRecordCreate.model_fields


def test_rejects_client_supplied_submitted_by_code() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "submittedByCode": "FIELD01"})


def test_requires_inspection_session_token() -> None:
    payload = dict(_BASE)
    del payload["inspectionSessionToken"]
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate(payload)


def test_has_no_plot_or_supplier_id_fields_at_all() -> None:
    """Not just rejected by extra="forbid" — the fields don't exist on the
    schema, so there's no code path that could ever read a client-supplied
    plot_id/supplier_id off this model."""
    fields = set(PublicRecordCreate.model_fields)
    assert "plot_id" not in fields
    assert "supplier_id" not in fields
    assert "recorded_by_id" not in fields


def test_has_no_crop_variety_or_planting_date_fields_at_all() -> None:
    """Round 20.2 — crop/variety/planting_date are plot MASTER data, set
    only via Plot Create/Edit by an admin, never by whoever fills in an
    inspection. Same "field doesn't exist" treatment as plot_id/
    supplier_id above — the endpoint snapshots them from the verified
    plot instead (see public_records.py's _finish_creating_record)."""
    fields = set(PublicRecordCreate.model_fields)
    assert "crop" not in fields
    assert "variety" not in fields
    assert "planting_date" not in fields


def test_rejects_client_supplied_crop_variety_or_planting_date() -> None:
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "crop": "พริก"})
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "variety": "พริกขี้หนู"})
    with pytest.raises(ValidationError):
        PublicRecordCreate.model_validate({**_BASE, "plantingDate": "2026-01-01"})
