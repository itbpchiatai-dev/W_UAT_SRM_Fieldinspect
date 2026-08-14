"""Public record creation — round 8-3B phone binding (round 8-3G: made
REQUIRED, no legacy fallback).

A phone-bound inspection token makes the server snapshot the access phone
(id/number/type) + inspectorType onto the record — all server-derived, never
from the client. Every token must carry the binding now — a token minted
without one (the old "legacy inspection-code" shape) is rejected fail-closed
with a generic 401, never silently accepted with null phone fields. Lock
order is Plot → PlotCycle → PlotAccessPhone → insert. Same DB-less style +
`.__wrapped__` bypass as test_public_record_create_endpoint.py.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

from app.api.v1.public_records import create_record_public
from app.auth.inspection_session import encode_inspection_session_token
from app.core.config import get_settings
from app.schemas.record import PublicRecordCreate

_create = create_record_public.__wrapped__
_M = "app.api.v1.public_records"


@pytest.fixture(autouse=True)
def _stub_protocol_map():
    from app.services.inspection_protocols import default_protocol_map
    with patch(f"{_M}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


def _cycle(**o):
    # expected_yield_full/unit (round 8-8A) default None — no test in this
    # file sends yield_quantity_kg.
    d = dict(id=uuid4(), crop="พริก", variety="พริกขี้หนู",
             planting_date=datetime.date(2026, 1, 1),
             expected_yield_full=None, expected_yield_unit=None)
    d.update(o)
    return SimpleNamespace(**d)


def _supplier(**o):
    d = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    d.update(o)
    return SimpleNamespace(**d)


def _plot(supplier_id, **o):
    d = dict(id=uuid4(), plot_code="P001", name="Plot One", is_active=True,
             supplier_id=supplier_id)
    d.update(o)
    return SimpleNamespace(**d)


def _access(**o):
    d = dict(id=uuid4(), phone_normalized="0845552162", access_type="primary")
    d.update(o)
    return SimpleNamespace(**d)


def _system_user():
    return SimpleNamespace(id=uuid4())


def _record():
    return SimpleNamespace(
        id=uuid4(), plot_id=uuid4(), record_date=datetime.date(2026, 7, 1),
        submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        # Round 8-4A — the receipt now echoes these; a realistic online record
        # leaves them NULL.
        client_submission_id=None, captured_at=None,
    )


def _payload(token, **o):
    d = dict(inspection_session_token=token, record_date=datetime.date(2026, 7, 1))
    d.update(o)
    return PublicRecordCreate(**d)


def _phone_token(plot, supplier, cycle, access, inspector="farmer"):
    tok, _ = encode_inspection_session_token(
        plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
        plot_access_phone_id=access.id, inspector_type=inspector,
    )
    return tok


def _legacy_token(plot, supplier, cycle):
    """A token shaped like the pre-8-3B / pre-8-3H legacy inspection-code
    token — no plot_access_phone_id/inspector_type claims at all. Built by
    hand-encoding the JWT directly: round 8-3H made
    encode_inspection_session_token's phone-binding kwargs REQUIRED, so the
    real encoder can no longer produce this shape — but a forged/very-old
    token could still arrive at the endpoint, which is exactly the
    fail-closed case _extract_phone_binding must reject."""
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "type": "inspection_session",
        "plot_id": str(plot.id),
        "supplier_id": str(supplier.id),
        "plot_cycle_id": str(cycle.id),
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=30)).timestamp()),
        "jti": uuid4().hex,
    }
    return jose_jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _db():
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


def _common_patches(supplier, plot, cycle, access, record, sysuser):
    return [
        patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)),
        patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)),
        patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)),
        patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)),
        patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=sysuser)),
        patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()),
        patch(f"{_M}.set_public_record_rls_context", AsyncMock()),
    ]


async def test_phone_bound_record_snapshots_server_values() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access(access_type="primary", phone_normalized="0891112222")
    token = _phone_token(plot, supplier, cycle, access, inspector="farmer")
    with patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record())) as mk, \
         _apply(_common_patches(supplier, plot, cycle, access, None, _system_user())):
        await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    kw = mk.call_args.kwargs
    assert kw["plot_access_phone_id"] == access.id
    assert kw["submitted_phone_snapshot"] == "0891112222"
    assert kw["submitted_phone_type"] == "primary"
    assert kw["inspector_type"] == "farmer"


async def test_additional_type_snapshot() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access(access_type="additional")
    token = _phone_token(plot, supplier, cycle, access, inspector="chiatai")
    with patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record())) as mk, \
         _apply(_common_patches(supplier, plot, cycle, access, None, _system_user())):
        await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    kw = mk.call_args.kwargs
    assert kw["submitted_phone_type"] == "additional"
    assert kw["inspector_type"] == "chiatai"


async def test_phone_not_copied_into_submitted_by_name() -> None:
    """submitted_by_name (the sole remaining, optional field-attribution
    input since round 8-3G retired submitted_by_code) must stay exactly
    what the client sent — never overwritten by the phone snapshot."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access(phone_normalized="0845559999")
    token = _phone_token(plot, supplier, cycle, access)
    with patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record())) as mk, \
         _apply(_common_patches(supplier, plot, cycle, access, None, _system_user())):
        await _create(payload=_payload(token, submitted_by_name="สมชาย"),
                      request=AsyncMock(), db=_db())
    record_payload = mk.call_args.args[1]
    assert record_payload.submitted_by_name == "สมชาย"
    assert "0845559999" not in (record_payload.submitted_by_name or "")


async def test_token_without_phone_binding_rejected_401_and_skips_access_lookup() -> None:
    """Round 8-3G: the legacy "inspection-code" token shape (no
    plot_access_phone_id/inspector_type claims) is no longer accepted at
    all — fail closed with the same generic 401 as any other bad token,
    never silently treated as a valid legacy-flow submission."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    token = _legacy_token(plot, supplier, cycle)
    with patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk, \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock()) as mk_access, \
         patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 401
    mk.assert_not_awaited()
    mk_access.assert_not_awaited()


async def test_token_with_only_plot_access_phone_id_claim_rejected_401() -> None:
    """A half-set binding (only one of the pair) is malformed — same
    generic 401, never trusted."""
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    token = _legacy_token(plot, supplier, cycle)
    settings = get_settings()
    claims = jose_jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    claims["plot_access_phone_id"] = str(uuid4())
    half_token = jose_jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=_payload(half_token), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 401


async def test_revoked_access_phone_rejected_generic_404() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _phone_token(plot, supplier, cycle, access)
    with patch(f"{_M}.record_repo.create_record", AsyncMock()) as mk, \
         patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=None)), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 404
    mk.assert_not_awaited()


async def test_access_lookup_locks_for_update() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _phone_token(plot, supplier, cycle, access)
    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", AsyncMock(return_value=access)) as mk_access, \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record())), \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert mk_access.call_args.kwargs.get("for_update") is True


async def test_lock_order_plot_then_cycle_then_access_phone() -> None:
    supplier = _supplier()
    plot = _plot(supplier.id)
    cycle = _cycle()
    access = _access()
    token = _phone_token(plot, supplier, cycle, access)
    order: list[str] = []

    async def _lock_plot(*a, **k):
        order.append("plot")
        return plot

    async def _lock_cycle(*a, **k):
        order.append("cycle")
        return cycle

    async def _lock_access(*a, **k):
        order.append("access")
        return access

    with patch(f"{_M}.supplier_repo.get_supplier", AsyncMock(return_value=supplier)), \
         patch(f"{_M}.plot_repo.get_plot", AsyncMock(return_value=plot)), \
         patch(f"{_M}.plot_repo.get_plot_for_update", side_effect=_lock_plot), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=cycle)), \
         patch(f"{_M}.plot_cycle_repo.get_active_cycle_for_plot_for_update", side_effect=_lock_cycle), \
         patch(f"{_M}.phone_repo.get_access_row_for_plot_from_ids", side_effect=_lock_access), \
         patch(f"{_M}.get_external_submission_user", AsyncMock(return_value=_system_user())), \
         patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record())), \
         patch(f"{_M}.plot_repo.sync_current_status_from_record", AsyncMock()), \
         patch(f"{_M}.set_public_record_rls_context", AsyncMock()):
        await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert order == ["plot", "cycle", "access"]


def test_lock_order_in_source() -> None:
    import app.api.v1.public_records as pr
    src = inspect.getsource(pr._finish_creating_record)
    p = src.index("await plot_repo.get_plot_for_update")
    c = src.index("await plot_cycle_repo.get_active_cycle_for_plot_for_update")
    a = src.index("await phone_repo.get_access_row_for_plot_from_ids")
    assert p < c < a


def test_server_derived_phone_fields_absent_from_public_create_schema() -> None:
    banned = {"plot_access_phone_id", "submitted_phone_snapshot",
              "submitted_phone_type", "inspector_type"}
    assert banned.isdisjoint(set(PublicRecordCreate.model_fields))


# --- helper: apply a list of context managers -------------------------------

class _apply:
    """Enter a list of context managers as one `with` block."""

    def __init__(self, cms):
        self._cms = cms

    def __enter__(self):
        self._entered = [cm.__enter__() for cm in self._cms]
        return self._entered

    def __exit__(self, *exc):
        for cm in reversed(self._cms):
            cm.__exit__(*exc)
        return False
