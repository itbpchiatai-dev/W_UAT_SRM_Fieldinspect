"""Supplier contact/status search filters (round 8-20D).

POST /api/v1/suppliers/search — the Suppliers page's filter row: name/code,
contact name, contact-number fragment, and status, all ANDed.

Two layers, the same split every other search round in this repo uses
(test_plot_partial_search_8_18b1.py / test_plot_phone_search_8_17a2.py):
  - repository: compile the SQLAlchemy statement (literal binds) to inspect
    the WHERE clause without a real database.
  - endpoint: call the route function directly and patch the repository.

PII discipline: the contact-number filter is the one protected value here.
Every test that touches an invalid fragment asserts the rejection never
echoes it, and that no DB query is issued at all.
"""
from __future__ import annotations

import inspect
import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

import app.api.v1.suppliers as mod
from app.repositories.supplier_repository import list_suppliers as repo_list_suppliers
from app.repositories.supplier_repository import search_suppliers as repo_search_suppliers
from app.schemas.supplier import SupplierSearchRequest

_M = "app.api.v1.suppliers"


def _compiled(stmt) -> str:
    # Default dialect on purpose: the postgresql dialect escapes '%' as '%%'
    # under literal_binds (pyformat paramstyle), which would obscure the LIKE
    # patterns these assertions are about. Nothing here needs PG-only syntax.
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _where(stmt) -> str:
    """Just the WHERE clause — a bare column-name check would otherwise match
    the SELECT list, which names every column regardless of filtering."""
    compiled = _compiled(stmt)
    return compiled.split("WHERE", 1)[1] if "WHERE" in compiled else ""


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


def _capturing_db(rows=()):
    captured: dict = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        return _FakeResult(list(rows))

    return SimpleNamespace(execute=_execute), captured


def _supplier(**kw):
    base = dict(
        id=uuid4(), code="SUP001", name="Supplier One", is_active=True,
        contact_name="คุณสมชาย", contact_email="somchai@example.com",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- repository: q keeps its existing semantics -----------------------------


async def test_q_matches_code_partially():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], q="SUP0")
    assert "lower(suppliers.code) LIKE lower('%SUP0%')" in _compiled(captured["stmt"])


async def test_q_matches_name_partially():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], q="ซัพ")
    assert "lower(suppliers.name) LIKE lower('%ซัพ%')" in _compiled(captured["stmt"])


async def test_q_semantics_match_the_existing_list_endpoint():
    """Round 8-20D must not redefine what `q` means — the new search and the
    untouched GET list have to agree on code-OR-name."""
    db1, c1 = _capturing_db()
    await repo_search_suppliers(db1, [], q="ABC", status="all")
    db2, c2 = _capturing_db()
    await repo_list_suppliers(db2, [], q="ABC")
    for col in ("suppliers.name", "suppliers.code"):
        assert f"lower({col}) LIKE lower('%ABC%')" in _compiled(c1["stmt"])
        assert f"lower({col}) LIKE lower('%ABC%')" in _compiled(c2["stmt"])


async def test_q_is_trimmed():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], q="  SUP0  ")
    assert "'%SUP0%'" in _compiled(captured["stmt"])


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_blank_q_applies_no_text_filter(blank):
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], q=blank, status="all")
    where = _where(captured["stmt"])
    assert "suppliers.code) LIKE" not in where
    assert "suppliers.name) LIKE" not in where


# --- repository: contactName ------------------------------------------------


async def test_contact_name_matches_partially_and_case_insensitively():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], contact_name="สมชาย")
    assert "lower(suppliers.contact_name) LIKE lower('%สมชาย%')" in _compiled(captured["stmt"])


async def test_contact_name_is_trimmed():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], contact_name="  สมชาย  ")
    assert "'%สมชาย%'" in _compiled(captured["stmt"])


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_blank_contact_name_applies_no_filter(blank):
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], contact_name=blank, status="all")
    assert "contact_name" not in _where(captured["stmt"])


# --- repository: contact phone fragment -------------------------------------


async def test_phone_fragment_matches_contact_phone_as_a_substring():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], contact_phone_digits="5552")
    assert "suppliers.contact_phone LIKE '%5552%'" in _compiled(captured["stmt"])


async def test_phone_fragment_is_parameterized_not_string_built():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], contact_phone_digits="5552")
    assert "5552" not in str(captured["stmt"].compile())


async def test_no_phone_fragment_applies_no_phone_filter():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], status="all")
    assert "contact_phone" not in _where(captured["stmt"])


# --- repository: status -----------------------------------------------------


async def test_status_active_filters_is_active_true():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], status="active")
    assert "suppliers.is_active IS true" in _compiled(captured["stmt"])


async def test_status_inactive_filters_is_active_false():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], status="inactive")
    assert "suppliers.is_active IS false" in _compiled(captured["stmt"])


async def test_status_all_applies_no_is_active_filter():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], status="all")
    assert "suppliers.is_active IS" not in _compiled(captured["stmt"])


# --- repository: AND / scope / pagination -----------------------------------


async def test_every_filter_is_anded_in_one_statement():
    db, captured = _capturing_db()
    await repo_search_suppliers(
        db, [], q="SUP", contact_name="สมชาย", contact_phone_digits="5552", status="active",
    )
    compiled = _compiled(captured["stmt"])
    assert "lower(suppliers.code) LIKE lower('%SUP%')" in compiled
    assert "lower(suppliers.contact_name) LIKE lower('%สมชาย%')" in compiled
    assert "suppliers.contact_phone LIKE '%5552%'" in compiled
    assert "suppliers.is_active IS true" in compiled
    assert " OR " in compiled  # only the code/name pair is an OR
    assert compiled.count(" AND ") >= 3


async def test_scope_conditions_are_applied_and_never_widened():
    from app.db.models.supplier import Supplier

    sid = uuid4()
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [Supplier.id == sid], status="all")
    # The default dialect renders a UUID literal as bare hex (no hyphens).
    assert sid.hex in _compiled(captured["stmt"])


async def test_no_join_so_a_supplier_can_never_be_duplicated():
    db, captured = _capturing_db()
    await repo_search_suppliers(
        db, [], q="SUP", contact_name="ก", contact_phone_digits="5552",
    )
    compiled = _compiled(captured["stmt"])
    assert " JOIN " not in compiled
    assert "DISTINCT" not in compiled


async def test_pagination_is_forwarded_with_a_stable_total_order():
    db, captured = _capturing_db()
    await repo_search_suppliers(db, [], limit=20, offset=40, status="all")
    compiled = _compiled(captured["stmt"])
    assert "LIMIT 20" in compiled
    assert "OFFSET 40" in compiled
    # name alone is not unique; the id tiebreak is what stops a row from
    # repeating (or vanishing) between pages.
    assert "ORDER BY suppliers.name ASC, suppliers.id ASC" in compiled


# --- endpoint: phone validation --------------------------------------------


@pytest.mark.parametrize("good", ["5552", "12345", "0812345678", "0000"])
async def test_endpoint_accepts_a_4_to_10_digit_fragment(good):
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[])) as mocked:
        await mod.search_suppliers(
            payload=SupplierSearchRequest(contactPhoneDigits=good),
            response=Response(), scope=[], db=AsyncMock(),
        )
    assert mocked.await_args.kwargs["contact_phone_digits"] == good


@pytest.mark.parametrize(
    "bad",
    [
        "1", "12", "123",                     # shorter than 4
        "08123456789", "0812345678901",       # longer than 10
        "084-555-2162", "084 555 2162",       # formatting characters
        "+66845552162", "08a5552162", "abcd", # non-digit
        "١٢٣٤",                                # Arabic-Indic digits
        "５５５２",                              # full-width digits
        "55%2", "_552", "%",                  # LIKE wildcards must not slip through
    ],
)
async def test_endpoint_rejects_anything_outside_4_to_10_ascii_digits(bad):
    with patch(f"{_M}.repo.search_suppliers", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await mod.search_suppliers(
                payload=SupplierSearchRequest(contactPhoneDigits=bad),
                response=Response(), scope=[], db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    # No DB round-trip at all for a rejected fragment.
    mocked.assert_not_awaited()


@pytest.mark.parametrize("bad", ["123", "0812345678901", "08a5552162", "55%2"])
async def test_endpoint_rejection_is_generic_and_never_echoes_the_number(bad):
    with pytest.raises(HTTPException) as exc:
        await mod.search_suppliers(
            payload=SupplierSearchRequest(contactPhoneDigits=bad),
            response=Response(), scope=[], db=AsyncMock(),
        )
    assert exc.value.detail == "รูปแบบหมายเลขติดต่อไม่ถูกต้อง"
    assert bad not in exc.value.detail
    assert not any(ch.isdigit() for ch in exc.value.detail)


@pytest.mark.parametrize("wrong_type", [1234, 5552.0, ["5552"], {"n": "5552"}, True])
async def test_endpoint_wrong_type_phone_is_generic_422_with_no_query(wrong_type):
    with patch(f"{_M}.repo.search_suppliers", AsyncMock()) as mocked:
        with pytest.raises(HTTPException) as exc:
            await mod.search_suppliers(
                payload=SupplierSearchRequest(contactPhoneDigits=wrong_type),
                response=Response(), scope=[], db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert str(wrong_type) not in exc.value.detail
    mocked.assert_not_awaited()


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_a_blank_phone_filter_is_not_an_error(blank):
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[])) as mocked:
        await mod.search_suppliers(
            payload=SupplierSearchRequest(contactPhoneDigits=blank),
            response=Response(), scope=[], db=AsyncMock(),
        )
    assert mocked.await_args.kwargs["contact_phone_digits"] is None


def test_schema_never_rejects_the_phone_itself():
    """Regression guard for the PII-echo bug this endpoint avoids: if a
    field/model validator ever rejects contactPhoneDigits at the schema
    layer, FastAPI's auto-422 echoes the value in `input`."""
    req = SupplierSearchRequest(contactPhoneDigits="not a number at all")
    assert req.contact_phone_digits == "not a number at all"


def test_digit_bounds_are_4_and_10():
    assert (mod._PHONE_SEARCH_MIN_DIGITS, mod._PHONE_SEARCH_MAX_DIGITS) == (4, 10)


# --- endpoint: wiring -------------------------------------------------------


async def test_endpoint_forwards_every_filter_and_the_scope():
    scope = ["SCOPE_SENTINEL"]
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[])) as mocked:
        await mod.search_suppliers(
            payload=SupplierSearchRequest(
                q="SUP", contactName="สมชาย", contactPhoneDigits="5552",
                status="inactive", limit=20, offset=40,
            ),
            response=Response(), scope=scope, db=AsyncMock(),
        )
    kwargs = mocked.await_args.kwargs
    assert kwargs["scope_conditions"] is scope
    assert kwargs["q"] == "SUP"
    assert kwargs["contact_name"] == "สมชาย"
    assert kwargs["contact_phone_digits"] == "5552"
    assert kwargs["status"] == "inactive"
    assert (kwargs["limit"], kwargs["offset"]) == (20, 40)


async def test_status_defaults_to_active():
    assert SupplierSearchRequest().status == "active"
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[])) as mocked:
        await mod.search_suppliers(
            payload=SupplierSearchRequest(), response=Response(), scope=[], db=AsyncMock(),
        )
    assert mocked.await_args.kwargs["status"] == "active"


async def test_response_is_the_same_supplier_summary_shape_with_no_phone():
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[_supplier()])):
        result = await mod.search_suppliers(
            payload=SupplierSearchRequest(), response=Response(), scope=[], db=AsyncMock(),
        )
    row = result[0]
    assert row.code == "SUP001"
    # SupplierSummary deliberately carries no contactPhone/taxId/address.
    dumped = row.model_dump(by_alias=True)
    assert "contactPhone" not in dumped
    assert "taxId" not in dumped


async def test_endpoint_sets_cache_control_no_store():
    response = Response()
    with patch(f"{_M}.repo.search_suppliers", AsyncMock(return_value=[])):
        await mod.search_suppliers(
            payload=SupplierSearchRequest(), response=response, scope=[], db=AsyncMock(),
        )
    assert response.headers["Cache-Control"] == "no-store"


def test_schema_forbids_unknown_fields():
    with pytest.raises(Exception):
        SupplierSearchRequest(somethingElse="nope")


# --- structural: permission, POST-only, body-only ---------------------------


def test_route_requires_suppliers_read():
    src = inspect.getsource(mod)
    idx = src.index('@router.post("/search"')
    assert "SUPPLIERS_READ" in src[idx: idx + 300]


def test_route_is_post_not_get():
    matched = [r for r in mod.router.routes if getattr(r, "path", "") == "/search"]
    assert len(matched) == 1
    assert matched[0].methods == {"POST"}


def test_route_takes_a_body_payload_so_the_number_never_reaches_a_url():
    hints = typing.get_type_hints(mod.search_suppliers)
    assert hints["payload"] is SupplierSearchRequest
    # No query-parameter variant of the phone filter exists on this route.
    assert "contact_phone_digits" not in hints


def test_search_is_registered_before_the_supplier_id_route():
    paths = [getattr(r, "path", "") for r in mod.router.routes]
    assert paths.index("/search") < paths.index("/{supplier_id}")


def test_the_existing_get_list_endpoint_is_untouched():
    """Round 8-20D keeps GET /suppliers for backward compatibility — its
    signature must still accept the pre-8-20D query parameters."""
    hints = typing.get_type_hints(mod.list_suppliers)
    for name in ("limit", "offset", "q", "active_only"):
        assert name in hints


def test_the_search_module_never_logs_the_number():
    src = inspect.getsource(mod.search_suppliers) + inspect.getsource(mod._validated_phone_digits)
    for forbidden in ("logger", "logging", "print(", "ActivityLogger"):
        assert forbidden not in src
