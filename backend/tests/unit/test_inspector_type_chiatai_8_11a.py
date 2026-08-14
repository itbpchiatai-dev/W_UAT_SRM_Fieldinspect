"""Round 8-11A — the inspector_type contract after 'extension' → 'chiatai'.

The three canonical API/DB values are farmer / supplier / chiatai. This file
pins the contract end to end: the model allowlist and the Pydantic Literal
agree, select-plot mints a token carrying the chosen value, record creation
snapshots exactly that value, the RETIRED 'extension' is rejected at both the
request boundary (422) and the token boundary (401 fail-closed), and migration
0047's SQL renames the value in both directions without touching row counts.

DB-less: repos are patched with AsyncMocks, same style as
test_public_record_phone_binding.py / test_phone_access_endpoints.py. The
migration is checked by source inspection (backend/alembic shadows the
installed alembic package, so it can't be imported standalone — same approach
as test_plot_access_phones_migration.py).
"""
from __future__ import annotations

import datetime
import re
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt
from pydantic import ValidationError

from app.api.v1.public_records import create_record_public
from app.auth.inspection_session import encode_inspection_session_token
from app.core.config import get_settings
from app.db.models.record import (
    INSPECTOR_TYPE_CHIATAI,
    INSPECTOR_TYPE_FARMER,
    INSPECTOR_TYPE_SUPPLIER,
    INSPECTOR_TYPES,
)
from app.schemas.phone_access import PublicPhoneAccessSelectPlotRequest
from app.schemas.record import PublicRecordCreate

_create = create_record_public.__wrapped__
_M = "app.api.v1.public_records"

RETIRED_VALUE = "extension"


@pytest.fixture(autouse=True)
def _stub_protocol_map():
    from app.services.inspection_protocols import default_protocol_map
    with patch(f"{_M}.protocol_service.get_protocol_map",
               AsyncMock(return_value=default_protocol_map())):
        yield


# --- fixtures (same shapes as test_public_record_phone_binding.py) ----------

def _cycle(**o):
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


def _record():
    return SimpleNamespace(
        id=uuid4(), plot_id=uuid4(), record_date=datetime.date(2026, 7, 1),
        submitted_by_name=None,
        created_at=datetime.datetime.now(datetime.timezone.utc),
        client_submission_id=None, captured_at=None,
    )


def _payload(token, **o):
    d = dict(inspection_session_token=token, record_date=datetime.date(2026, 7, 1))
    d.update(o)
    return PublicRecordCreate(**d)


def _db():
    db = MagicMock()
    db.refresh = AsyncMock()
    return db


def _forged_token(plot, supplier, cycle, access, inspector_type):
    """Hand-encode a token with an ARBITRARY inspector_type claim — the real
    encoder validates against the allowlist and so cannot produce a retired or
    unknown value, but a forged/pre-migration token could still arrive."""
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "type": "inspection_session",
        "plot_id": str(plot.id),
        "supplier_id": str(supplier.id),
        "plot_cycle_id": str(cycle.id),
        "plot_access_phone_id": str(access.id),
        "inspector_type": inspector_type,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(minutes=30)).timestamp()),
        "jti": uuid4().hex,
    }
    return jose_jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _common_patches(supplier, plot, cycle, access, sysuser):
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


# --- items 1/2: the canonical allowlist -------------------------------------

def test_canonical_allowlist_is_farmer_supplier_chiatai():
    assert INSPECTOR_TYPES == ("farmer", "supplier", "chiatai")
    assert INSPECTOR_TYPE_FARMER == "farmer"
    assert INSPECTOR_TYPE_SUPPLIER == "supplier"
    assert INSPECTOR_TYPE_CHIATAI == "chiatai"


def test_the_retired_value_is_gone_from_the_allowlist():
    assert RETIRED_VALUE not in INSPECTOR_TYPES


def test_supplier_is_unchanged_not_renamed_to_manufacturer():
    """The Thai LABEL became "บริษัทผู้ผลิต", but the stored value must stay
    'supplier' — renaming it would orphan every existing supplier record."""
    assert INSPECTOR_TYPE_SUPPLIER == "supplier"
    assert "manufacturer" not in INSPECTOR_TYPES


def test_model_check_constraint_matches_the_allowlist():
    """NAMING_CONVENTION resolves the model's short `inspector_type_allowed`
    to ck_records_inspector_type_allowed at table-definition time — the exact
    name migrations 0039/0047 use, so model metadata and DB agree."""
    from app.db.models.record import Record

    checks = [
        str(c.sqltext) for c in Record.__table__.constraints
        if getattr(c, "name", None) == "ck_records_inspector_type_allowed"
    ]
    assert len(checks) == 1
    sql = checks[0]
    assert "'chiatai'" in sql
    assert "'farmer'" in sql and "'supplier'" in sql
    assert RETIRED_VALUE not in sql


def test_schema_literal_matches_the_model_allowlist():
    """The Pydantic Literal and the model tuple must never drift: a value
    FastAPI accepts has to be one the DB CHECK also allows."""
    from typing import get_args

    from app.schemas.phone_access import InspectorType

    assert set(get_args(InspectorType)) == set(INSPECTOR_TYPES)


def test_no_thai_label_is_ever_a_canonical_value():
    """Labels live in the frontend; the DB stores only ASCII keys."""
    for value in INSPECTOR_TYPES:
        assert value.isascii()
        assert value.islower()


# --- items 3/4/5/6: the request boundary + token minting --------------------

@pytest.mark.parametrize("itype", ["farmer", "supplier", "chiatai"])
def test_select_plot_request_accepts_each_canonical_value(itype):
    req = PublicPhoneAccessSelectPlotRequest(
        phone_access_session_token="tok", plot_id=uuid4(), inspector_type=itype,
    )
    assert req.inspector_type == itype


@pytest.mark.parametrize("bad", [RETIRED_VALUE, "Chiatai", "CHIATAI", "manufacturer", "", "farmer "])
def test_select_plot_request_rejects_retired_and_unknown_values(bad):
    """422 at the schema boundary — including the retired 'extension' (item 6)
    and any case variant, since the canonical value is lowercase."""
    with pytest.raises(ValidationError):
        PublicPhoneAccessSelectPlotRequest(
            phone_access_session_token="tok", plot_id=uuid4(), inspector_type=bad,
        )


@pytest.mark.parametrize("itype", ["farmer", "supplier", "chiatai"])
def test_token_carries_the_chosen_inspector_type(itype):
    supplier, cycle, access = _supplier(), _cycle(), _access()
    plot = _plot(supplier.id)
    token, _ = encode_inspection_session_token(
        plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
        plot_access_phone_id=access.id, inspector_type=itype,
    )
    settings = get_settings()
    claims = jose_jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert claims["inspector_type"] == itype


@pytest.mark.parametrize("bad", [RETIRED_VALUE, "unknown", "Chiatai"])
def test_the_encoder_refuses_to_mint_a_non_canonical_token(bad):
    supplier, cycle, access = _supplier(), _cycle(), _access()
    plot = _plot(supplier.id)
    with pytest.raises(ValueError):
        encode_inspection_session_token(
            plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
            plot_access_phone_id=access.id, inspector_type=bad,
        )


# --- items 7/8/9/10: record creation ----------------------------------------

@pytest.mark.parametrize("itype", ["farmer", "supplier", "chiatai"])
async def test_record_snapshots_the_token_inspector_type(itype):
    supplier, cycle, access = _supplier(), _cycle(), _access()
    plot = _plot(supplier.id)
    token, _ = encode_inspection_session_token(
        plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
        plot_access_phone_id=access.id, inspector_type=itype,
    )
    with ExitStack() as stack:
        for p in _common_patches(supplier, plot, cycle, access, SimpleNamespace(id=uuid4())):
            stack.enter_context(p)
        mk = stack.enter_context(
            patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record()))
        )
        await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert mk.call_args.kwargs["inspector_type"] == itype


@pytest.mark.parametrize("claim", [RETIRED_VALUE, "unknown", "Chiatai", ""])
async def test_a_token_claiming_a_non_canonical_type_is_rejected_fail_closed(claim):
    """Item 10 — a forged or pre-migration token carrying a value off the
    allowlist gets the same generic 401 as any other bad token, and NO record
    is ever written."""
    supplier, cycle, access = _supplier(), _cycle(), _access()
    plot = _plot(supplier.id)
    token = _forged_token(plot, supplier, cycle, access, claim)
    with ExitStack() as stack:
        for p in _common_patches(supplier, plot, cycle, access, SimpleNamespace(id=uuid4())):
            stack.enter_context(p)
        mk = stack.enter_context(
            patch(f"{_M}.record_repo.create_record", AsyncMock(return_value=_record()))
        )
        with pytest.raises(HTTPException) as exc:
            await _create(payload=_payload(token), request=AsyncMock(), db=_db())
    assert exc.value.status_code == 401
    mk.assert_not_awaited()
    # the rejected value is never echoed back to the caller
    assert claim not in str(exc.value.detail) or claim == ""


# --- item 11: idempotency / replay identity ---------------------------------

def test_replay_identity_compares_the_new_value():
    """_matches_replay_identity compares inspector_type by equality, so the
    renamed value flows through untouched — a replay of a 'chiatai' record
    matches only another 'chiatai' one."""
    from app.api.v1.public_records import _matches_replay_identity

    plot_id, phone_id = uuid4(), uuid4()
    plot = SimpleNamespace(id=plot_id)
    existing = SimpleNamespace(
        plot_id=plot_id, plot_access_phone_id=phone_id, inspector_type="chiatai",
    )
    assert _matches_replay_identity(existing, plot, phone_id, "chiatai") is True
    # a different inspector type is NOT the same submission identity
    assert _matches_replay_identity(existing, plot, phone_id, "farmer") is False
    assert _matches_replay_identity(existing, plot, phone_id, RETIRED_VALUE) is False


# --- item 12: unrelated security surfaces are untouched ---------------------

def test_the_token_still_carries_no_phone_number():
    """This round renames one enum value — it must not widen the token's
    claims. The raw phone is never a claim; only the access-row id is."""
    supplier, cycle = _supplier(), _cycle()
    access = _access(phone_normalized="0891112222")
    plot = _plot(supplier.id)
    token, _ = encode_inspection_session_token(
        plot_id=plot.id, supplier_id=supplier.id, plot_cycle_id=cycle.id,
        plot_access_phone_id=access.id, inspector_type="chiatai",
    )
    settings = get_settings()
    claims = jose_jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert "0891112222" not in str(claims)
    assert "password" not in str(claims).lower()
    assert "qr" not in {k.lower() for k in claims}


# --- items 13/14: migration 0047 --------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_08_04_0000-0047_inspector_type_chiatai.py"
)
_SRC = _MIGRATION.read_text(encoding="utf-8")


def _upgrade_sql() -> str:
    return _SRC[_SRC.index("def upgrade"):_SRC.index("def downgrade")]


def _downgrade_sql() -> str:
    return _SRC[_SRC.index("def downgrade"):]


def test_migration_revision_chain():
    revision = re.search(r'^revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    down = re.search(r'^down_revision = "([^"]+)"', _SRC, re.MULTILINE).group(1)
    assert revision == "0047_inspector_type_chiatai"
    assert down == "0046_plot_access_credentials"
    assert len(revision) <= 32  # alembic_version.version_num limit


def test_upgrade_drops_updates_then_recreates_in_that_order():
    up = _upgrade_sql()
    drop = up.index("DROP CONSTRAINT")
    update = up.index("UPDATE records")
    add = up.index("ADD CONSTRAINT")
    # The UPDATE must sit BETWEEN the drop and the re-add: the old CHECK has no
    # 'chiatai', so updating first would violate it.
    assert drop < update < add


def test_upgrade_uses_the_real_constraint_name():
    """Migration 0039 created it as ck_records_inspector_type_allowed (the ORM
    naming convention's ck_%(table_name)s_%(constraint_name)s form) — dropping
    the model-level short name would silently no-op."""
    up = _upgrade_sql()
    assert "ck_records_inspector_type_allowed" in up
    assert "DROP CONSTRAINT IF EXISTS ck_records_inspector_type_allowed" in up


def test_upgrade_rewrites_only_the_retired_value():
    up = _upgrade_sql()
    assert "SET inspector_type = 'chiatai'" in up
    assert "WHERE inspector_type = 'extension'" in up
    # a WHERE clause is what keeps farmer/supplier/NULL untouched
    assert up.count("UPDATE records") == 1


def test_upgrade_installs_the_new_allowlist():
    up = _upgrade_sql()
    assert "inspector_type IN ('farmer', 'supplier', 'chiatai')" in up
    assert "inspector_type IS NULL" in up   # NULL stays legal


def test_downgrade_is_the_exact_inverse():
    down = _downgrade_sql()
    assert "SET inspector_type = 'extension'" in down
    assert "WHERE inspector_type = 'chiatai'" in down
    assert "inspector_type IN ('farmer', 'supplier', 'extension')" in down
    drop = down.index("DROP CONSTRAINT")
    update = down.index("UPDATE records")
    add = down.index("ADD CONSTRAINT")
    assert drop < update < add


def test_migration_never_inserts_or_deletes_rows():
    """Item 14 — the records count must be identical before and after, in both
    directions. The only DML in the whole file is the two UPDATEs."""
    for sql in (_upgrade_sql(), _downgrade_sql()):
        assert "DELETE" not in sql.upper()
        assert "INSERT" not in sql.upper()
        assert "TRUNCATE" not in sql.upper()
        assert "DROP TABLE" not in sql.upper()


def test_migration_touches_only_the_records_table():
    for sql in (_upgrade_sql(), _downgrade_sql()):
        for table in ("plots", "plot_cycles", "plot_access_phones",
                      "plot_access_credentials", "suppliers", "users"):
            assert f" {table} " not in sql, f"migration must not touch {table}"
