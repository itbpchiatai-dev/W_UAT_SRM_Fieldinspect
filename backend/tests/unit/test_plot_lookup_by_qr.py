"""Unit tests for GET /api/v1/plots/lookup-by-qr (round 20 QR hardening) —
sibling of /lookup, keyed by the opaque qr_key instead of
supplierCode+plotCode. Same DB-less mocking pattern as
tests/unit/test_plot_lookup.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.plots import lookup_plot_by_qr


@pytest.fixture(autouse=True)
def _stub_active_cycle():
    """Round 7.1.1: lookup-by-qr now gates on an active cycle (see
    test_plot_lookup.py). Default to a plot that has one; no-cycle test overrides."""
    with patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot",
               AsyncMock(return_value=SimpleNamespace(id=uuid4()))):
        yield


def _supplier(**overrides):
    defaults = dict(id=uuid4(), code="SUP001", name="Supplier One", is_active=True)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _plot(**overrides):
    defaults = dict(id=uuid4(), plot_code="PLOT001", name="Plot One", is_active=True, supplier=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_lookup_by_qr_success_returns_camel_case_fields() -> None:
    supplier = _supplier()
    plot = _plot(supplier=supplier, supplier_id=supplier.id)
    with patch("app.api.v1.plots.repo.get_plot_by_qr_key", AsyncMock(return_value=plot)):
        result = await lookup_plot_by_qr(qr_key="opaque-key-abc", db=AsyncMock())

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


async def test_lookup_by_qr_unknown_key_raises_generic_404() -> None:
    with patch("app.api.v1.plots.repo.get_plot_by_qr_key", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot_by_qr(qr_key="nope", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_lookup_by_qr_inactive_plot_raises_generic_404() -> None:
    supplier = _supplier()
    plot = _plot(supplier=supplier, supplier_id=supplier.id, is_active=False)
    with patch("app.api.v1.plots.repo.get_plot_by_qr_key", AsyncMock(return_value=plot)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot_by_qr(qr_key="opaque-key-abc", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_lookup_by_qr_inactive_supplier_raises_generic_404() -> None:
    supplier = _supplier(is_active=False)
    plot = _plot(supplier=supplier, supplier_id=supplier.id)
    with patch("app.api.v1.plots.repo.get_plot_by_qr_key", AsyncMock(return_value=plot)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot_by_qr(qr_key="opaque-key-abc", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"


async def test_lookup_by_qr_no_active_cycle_raises_generic_404() -> None:
    """Round 7.1.1 — same active-cycle gate as the legacy /lookup."""
    supplier = _supplier()
    plot = _plot(supplier=supplier, supplier_id=supplier.id)
    with patch("app.api.v1.plots.repo.get_plot_by_qr_key", AsyncMock(return_value=plot)), \
         patch("app.api.v1.plots.plot_cycle_repo.get_active_cycle_for_plot", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await lookup_plot_by_qr(qr_key="opaque-key-abc", db=AsyncMock())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Plot not found"
