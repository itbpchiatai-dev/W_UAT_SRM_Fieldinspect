"""Round 7.1 authorization regression — is_active can't be toggled through
the generic PATCH /plots/{id} (gated by the weaker plots.update). Permanent
closure must go through POST /{plot_id}/deactivate (gated by plots.delete).
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.api.v1.plots as plots_module
from app.api.v1.plots import update_plot
from app.schemas.plot import PlotUpdate

_MODULE = "app.api.v1.plots"


@pytest.mark.parametrize("is_active", [True, False])
async def test_patch_rejects_explicit_is_active(is_active: bool) -> None:
    """Sending isActive (either value) through the generic PATCH is a 400 —
    the plot is never looked up or mutated."""
    with patch(f"{_MODULE}.repo.get_plot_for_update", AsyncMock()) as mocked_get, \
         patch(f"{_MODULE}.repo.update_plot", AsyncMock()) as mocked_update:
        with pytest.raises(HTTPException) as exc:
            await update_plot(plot_id=uuid4(), payload=PlotUpdate(is_active=is_active), db=MagicMock())

    assert exc.value.status_code == 400
    mocked_get.assert_not_awaited()
    mocked_update.assert_not_awaited()


async def test_patch_without_is_active_proceeds() -> None:
    """A normal edit that omits isActive is unaffected."""
    plot = SimpleNamespace(id=uuid4())
    with patch(f"{_MODULE}.repo.get_plot_for_update", AsyncMock(return_value=plot)), \
         patch(f"{_MODULE}.repo.update_plot", AsyncMock(return_value=plot)) as mocked_update, \
         patch(f"{_MODULE}._to_read", MagicMock(return_value=SimpleNamespace())):
        await update_plot(plot_id=uuid4(), payload=PlotUpdate(name="แปลงใหม่"), db=MagicMock())

    mocked_update.assert_awaited_once()


def test_deactivate_still_gated_by_plots_delete() -> None:
    """Source guard: the deactivate route requires plots.delete and update
    requires the weaker plots.update — so the PATCH guard above is the only
    way plots.update-holders could otherwise have flipped is_active."""
    src = Path(inspect.getfile(plots_module)).read_text(encoding="utf-8")
    deactivate = src[src.index("async def deactivate_plot"):]
    # the decorator block sits just above the function; grab a window before it
    deactivate_decorator = src[src.index('/{plot_id}/deactivate'):src.index("async def deactivate_plot")]
    assert "PermissionKey.PLOTS_DELETE" in deactivate_decorator

    # Anchor on "update_plot(" (with the paren) so this doesn't accidentally
    # match "update_plot_cycle" — the round-7.2B cycle PATCH handler, which is
    # defined earlier in the file.
    update_decorator = src[src.index('@router.patch("/{plot_id}"'):src.index("async def update_plot(")]
    assert "PermissionKey.PLOTS_UPDATE" in update_decorator
    # and the guard itself is present in the handler
    assert 'is_active" in payload.model_fields_set' in src
