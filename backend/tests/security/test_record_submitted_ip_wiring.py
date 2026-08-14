"""records.submitted_ip audit capture — wiring + not-client-writable checks.

Confirms:
- All four record-create endpoints (logged-in JSON/multipart + public
  JSON/multipart) resolve the client IP via rate_limit.get_client_ip —
  the one trusted-proxy-aware algorithm the rate limiter already uses —
  and pass it to the repository.
- submitted_ip is NOT a field on any create/update request schema, so a
  client can never supply or overwrite it (PublicRecordCreate would 422
  via extra="forbid"; RecordCreate/RecordUpdate would silently ignore it,
  but the field simply doesn't exist to be read).
- RecordRead exposes it (read-only, for the admin-facing preview).

No DB fixture exists in this repo — source/schema inspection per the
established pattern (tests/security/test_record_recorded_by_not_spoofable.py).
"""
from __future__ import annotations

import inspect

from app.api.v1 import public_records as public_records_module
from app.api.v1 import records as records_module
from app.schemas.record import PublicRecordCreate, RecordCreate, RecordRead, RecordUpdate


def test_no_create_or_update_schema_accepts_submitted_ip() -> None:
    for schema in (RecordCreate, RecordUpdate, PublicRecordCreate):
        assert "submitted_ip" not in schema.model_fields, schema.__name__


def test_record_read_exposes_submitted_ip() -> None:
    assert "submitted_ip" in RecordRead.model_fields


def test_logged_in_create_endpoints_resolve_ip_via_get_client_ip() -> None:
    src = inspect.getsource(records_module)
    assert "from app.core.rate_limit import get_client_ip" in src
    for fn in (records_module.create_record, records_module.create_record_with_photos):
        assert "get_client_ip(request)" in inspect.getsource(fn), fn.__name__


def test_public_create_endpoints_resolve_ip_via_get_client_ip() -> None:
    src = inspect.getsource(public_records_module)
    assert "get_client_ip" in src
    for fn in (
        public_records_module.create_record_public.__wrapped__,
        public_records_module.create_record_with_photos_public.__wrapped__,
    ):
        assert "get_client_ip(request)" in inspect.getsource(fn), fn.__name__


def test_repository_takes_ip_as_kwarg_not_from_payload() -> None:
    from app.repositories import record_repository

    sig = inspect.signature(record_repository.create_record)
    assert "submitted_ip" in sig.parameters
    # And the schema-level guarantee above means payload.model_dump() can
    # never smuggle a submitted_ip key into Record(**data).


def test_get_client_ip_is_the_same_algorithm_as_the_rate_limiter() -> None:
    """One IP-resolution truth: the audit field and the rate-limit key must
    never disagree about who the client is."""
    from app.core import rate_limit

    assert rate_limit.get_client_ip is rate_limit._client_ip
    assert rate_limit.limiter._key_func is rate_limit._client_ip
