"""recorded_by_id (audit/login user) must stay server-derived from
current_user.id — RecordCreate/RecordUpdate must not expose it as a
client-settable field. The new submitted_by_code/submitted_by_name
(field-attribution, added this round) are explicitly not a substitute and
must never gate authorization."""
from __future__ import annotations

import inspect

from app.api.v1 import records as records_module
from app.schemas.record import RecordCreate, RecordUpdate


def test_record_create_schema_has_no_recorded_by_id_field() -> None:
    assert "recorded_by_id" not in RecordCreate.model_fields


def test_record_update_schema_has_no_recorded_by_id_field() -> None:
    assert "recorded_by_id" not in RecordUpdate.model_fields


def test_create_record_endpoint_derives_recorded_by_id_from_current_user() -> None:
    """round 13: create_record delegates to the shared _create_record helper
    (reused by create_record_with_photos) — the invariant now spans both:
    create_record must pass current_user.id through unchanged, and
    _create_record must be what actually threads it into recorded_by_id."""
    route_src = inspect.getsource(records_module.create_record)
    assert "current_user_id=current_user.id" in route_src

    helper_src = inspect.getsource(records_module._create_record)
    assert "recorded_by_id=current_user_id" in helper_src


def test_create_record_with_photos_endpoint_also_derives_recorded_by_id_from_current_user() -> None:
    """round 13: the new multipart endpoint must go through the same
    _create_record helper as the JSON one — not a separate path that could
    drift and accept a client-controlled recorded_by_id."""
    src = inspect.getsource(records_module.create_record_with_photos)
    assert "current_user_id=current_user.id" in src


def test_submitted_by_fields_are_not_referenced_by_any_permission_check() -> None:
    src = inspect.getsource(records_module)
    assert "submitted_by" not in src, (
        "records.py route layer must not read submitted_by_code/name at all — "
        "they flow through payload.model_dump() only, never used for auth"
    )
