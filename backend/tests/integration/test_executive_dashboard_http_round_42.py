"""GET /dashboard/executive — the gate, proved over real HTTP (round 42).

tests/unit/test_executive_dashboard_round_42.py reads the permission key off
the route's dependency objects. That proves the wiring is declared; it does
not prove FastAPI enforces it, because it never dispatches a request. The
page shows company-wide totals across every supplier, so "a caller without
the key is refused" is worth proving at the layer that actually refuses.

Same dependency-override pattern as
tests/integration/test_plot_phone_search_http_8_17b.py — real ASGI dispatch
via httpx + ASGITransport, with auth/RLS/DB faked so no database is touched.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.dashboard import ExecutiveSummary

KEY = "executive_dashboard.read"
URL = "/api/v1/dashboard/executive"

_current_permissions: set[str] = set()
_current_role = "internal:admin"


def _user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name=_current_role)],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions=set(_current_permissions),
        is_active=True,
    )


async def _fake_get_db() -> AsyncIterator[object]:
    """A session stub, never a real connection — the repository is patched
    in every test below.

    It needs `add` because the REFUSAL path writes an activity-log row:
    require_permission records every denial before raising 403, so a bare
    object() here fails inside the logger rather than at the assertion, and
    the test reports an AttributeError instead of the status code it is
    actually about.
    """
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    yield db


async def _noop_rls_context() -> None:
    return None


def _summary() -> ExecutiveSummary:
    return ExecutiveSummary(
        plots_total=763, plots_inspected=122, plots_never_inspected=641,
        plots_overdue=60, overdue_days=30,
        records_total=191, records_last_7_days=10, records_last_30_days=44,
        avg_condition_score=6.7, scored_record_count=159, watch_plot_count=43,
        target_kg_total=Decimal("411525.00"), harvest_kg_total=Decimal("318737.34"),
        achievement_pct=77.5, cleaned_kg_total=Decimal("28712.22"),
        cleaned_record_count=12, cleaned_missing_count=179,
    )


@pytest.fixture(autouse=True)
def _override_dependencies():
    global _current_permissions, _current_role
    _current_permissions = {KEY}
    _current_role = "internal:admin"
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_rls_context] = _noop_rls_context
    yield
    app.dependency_overrides.clear()


async def _get() -> httpx.Response:
    with patch(
        "app.repositories.dashboard_repository.get_executive_summary",
        AsyncMock(return_value=_summary()),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(URL)


class TestTheGate:
    async def test_a_holder_of_the_key_gets_the_page(self) -> None:
        resp = await _get()
        assert resp.status_code == 200

    @pytest.mark.parametrize("permissions", [
        set(),                                  # nothing at all
        {"plots.read"},                         # what supplier:owner holds
        {"records.read", "suppliers.read"},     # a field role
        {"reports.plot_status"},                # a neighbouring report
    ])
    async def test_every_other_permission_set_is_refused(self, permissions) -> None:
        # plots.read is the one that matters: migration 0058 had to undo
        # exactly this reuse for the reports, and supplier:owner holds it.
        global _current_permissions
        _current_permissions = permissions
        resp = await _get()
        assert resp.status_code == 403

    async def test_the_refusal_says_nothing_about_the_numbers(self) -> None:
        global _current_permissions
        _current_permissions = {"plots.read"}
        resp = await _get()
        for leak in ("763", "411525", "122"):
            assert leak not in resp.text


class TestThePayload:
    async def test_it_is_camel_case_on_the_wire(self) -> None:
        # The frontend reads camelCase; a snake_case payload would silently
        # render every card as empty. This repo has shipped that bug before
        # (see frontend-query-params-must-be-snake-case).
        body = (await _get()).json()
        for field in ("plotsTotal", "plotsInspected", "plotsOverdue",
                      "recordsLast7Days", "recordsLast30Days",
                      "targetKgTotal", "harvestKgTotal", "achievementPct",
                      "cleanedRecordCount", "cleanedMissingCount",
                      "overdueDays", "watchPlotCount"):
            assert field in body, f"missing from the response: {field}"

    async def test_no_snake_case_key_leaks_through(self) -> None:
        body = (await _get()).json()
        assert [k for k in body if "_" in k] == []

    async def test_the_overdue_window_travels_with_the_numbers(self) -> None:
        # The card's label is built from this, so it can never claim 30 days
        # while the query used something else.
        assert (await _get()).json()["overdueDays"] == 30

    async def test_money_sized_numbers_survive_as_decimals(self) -> None:
        body = (await _get()).json()
        assert str(body["targetKgTotal"]).startswith("411525")
        assert str(body["harvestKgTotal"]).startswith("318737.3")
