"""Unit tests for GET /api/v1/plots/lookup (QR-scan supplier+plot auto-fill).

This repo has no integration-test DB fixture yet (tests/integration/ is an
empty scaffold — see docs/testing.md §3.4 for the aspirational pattern), so
these tests call the route function directly and monkeypatch the repository
layer instead of hitting a real Postgres/RLS-enabled database. Permission +
RLS-context wiring is covered separately by
tests/security/test_plot_lookup_wiring.py via source inspection, matching
this repo's existing DB-less pattern for endpoint coverage (see
tests/security/test_patch_user_self_guard.py).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.plots import lookup_plot


@pytest.fixture(autouse=True)
def _stub_active_cycle():
    """Round 7.1.1: lookup now gates on the plot having an active cycle.
    Default every test to a plot that HAS one; the no-cycle test overrides."""
    with patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot",
               AsyncMock(return_value=SimpleNamespace(id=uuid4()))):
        yield


def _supplier(**overrides):
    defaults = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plot(**overrides):
    defaults = dict(id=uuid4(), plot_code="PLOT001", name="Plot One", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_lookup_plot_success_returns_camel_case_fields() -> None:
    supplier = _supplier()
    plot = _plot()
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=plot)):
        result = await lookup_plot(supplier_code="sup001", plot_code="plot001", db=AsyncMock())

    assert result.plot_id == plot.id
    assert result.plot_code == plot.plot_code
    assert result.plot_name == plot.name
    assert result.supplier_id == supplier.id
    assert result.supplier_code == supplier.code
    assert result.supplier_name == supplier.name

    dumped = result.model_dump(by_alias=True)
    assert set(dumped) == {
        "plotId", "plotCode", "plotName", "supplierId", "supplierCode", "supplierName",
    }


async def test_lookup_plot_unknown_supplier_raises_generic_404() -> None:
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=None)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mocked_get_plot:
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot(supplier_code="NOPE", plot_code="PLOT001", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_get_plot.assert_not_awaited()


async def test_lookup_plot_unknown_plot_code_raises_same_generic_404() -> None:
    supplier = _supplier()
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot(supplier_code="SUP001", plot_code="NOPE", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_lookup_plot_inactive_supplier_raises_404_without_querying_plot() -> None:
    supplier = _supplier(is_active=False)
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mocked_get_plot:
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot(supplier_code="SUP001", plot_code="PLOT001", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
    mocked_get_plot.assert_not_awaited()


async def test_lookup_plot_inactive_plot_raises_generic_404() -> None:
    supplier = _supplier()
    plot = _plot(is_active=False)
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=plot)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot(supplier_code="SUP001", plot_code="PLOT001", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_lookup_plot_no_active_cycle_raises_generic_404() -> None:
    """Round 7.1.1 — an active plot with no active planting cycle can't take a
    new record, so its auto-fill lookup 404s the same as an unresolvable plot
    (consistent with records._create_record's 409 and the public verify gate)."""
    supplier = _supplier()
    plot = _plot()
    with patch("app.api.v1.plots.supplier_repo.get_supplier_by_code", AsyncMock(return_value=supplier)), \
         patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=plot)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot(supplier_code="SUP001", plot_code="PLOT001", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
