"""plot_repository.create_plot — qr_key generation (round 20 QR hardening).

Uses the real Plot ORM model + the real generate_qr_key(); only the DB
session I/O boundary (add/flush/refresh) is mocked, since no DB fixture
exists in this repo — same pattern as
tests/unit/test_plot_repository_inspection_code.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.repositories.plot_repository import create_plot
from app.schemas.plot import PlotCreate, PlotUpdate


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


async def test_create_plot_always_generates_a_qr_key() -> None:
    payload = PlotCreate(supplier_id=uuid4(), plot_code="P001", name="Plot One")
    plot = await create_plot(_mock_db(), payload)

    assert plot.qr_key
    assert isinstance(plot.qr_key, str)
    assert len(plot.qr_key) >= 30


async def test_create_plot_generates_distinct_qr_keys_per_plot() -> None:
    plot1 = await create_plot(
        _mock_db(), PlotCreate(supplier_id=uuid4(), plot_code="P002", name="Plot Two")
    )
    plot2 = await create_plot(
        _mock_db(), PlotCreate(supplier_id=uuid4(), plot_code="P003", name="Plot Three")
    )

    assert plot1.qr_key != plot2.qr_key


def test_plot_create_payload_has_no_qr_key_field() -> None:
    """qr_key must never be client-settable — absent from PlotCreate
    entirely, so there's nothing for a client to set even by accident
    (mirrors how inspection_code_hash is never a direct field either)."""
    assert "qr_key" not in PlotCreate.model_fields


def test_plot_update_payload_has_no_qr_key_field() -> None:
    """And no client-driven rotation path either — regenerating a plot's
    qr_key (a real future feature) must go through a dedicated action, not
    a generic PATCH field a client could set to an arbitrary value."""
    assert "qr_key" not in PlotUpdate.model_fields


def test_plot_read_and_summary_do_expose_qr_key() -> None:
    """Unlike inspection_code_hash (never exposed to anyone), qr_key IS
    meant to be visible to authenticated admins with plots.read — it's
    what the frontend embeds in a plot's printed QR deep link, and every
    viewer of these responses already has access to the existing QR-print
    buttons for the same plots."""
    from app.schemas.plot import PlotRead, PlotSummary

    assert "qr_key" in PlotRead.model_fields
    assert "qr_key" in PlotSummary.model_fields
