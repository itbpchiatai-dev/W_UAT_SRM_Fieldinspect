"""POST /api/v1/plots — supplier self-service scope guard.

A supplier-scoped caller (supplier:owner role, or is_supplier_admin with a
linked supplier) may only create plots for their OWN supplier; naming any
other supplier_id in the payload is a clean 403 at the app layer, before
any repository call. Full-access callers (internal admin etc.) are
unaffected. RLS WITH CHECK would also reject the foreign insert at the DB,
but only as an opaque error and only when connected as the non-BYPASSRLS
role — this guard is what makes the contract explicit.

No DB fixture exists in this repo — matching tests/unit/test_plot_lookup.py,
these call the route function directly with mocked repository calls.
Permission/RLS wiring is covered by
tests/security/test_plot_create_supplier_scope_wiring.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.plots import create_plot
from app.schemas.plot import PlotCreate


def _user(roles: list[str], supplier_id=None, is_supplier_admin: bool = False):
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name=r) for r in roles],
        supplier_id=supplier_id,
        is_supplier_admin=is_supplier_admin,
    )


def _payload(supplier_id) -> PlotCreate:
    return PlotCreate(supplier_id=supplier_id, plot_code="P001", name="Test Plot")


async def test_supplier_owner_can_create_for_their_own_supplier() -> None:
    supplier_id = uuid4()
    user = _user(["supplier:owner"], supplier_id=supplier_id)
    fake_plot = SimpleNamespace(id=uuid4())

    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock(return_value=fake_plot)) as mocked_create, \
         patch("app.api.v1.plots._to_read", MagicMock(return_value="read-result")):
        result = await create_plot(
            payload=_payload(supplier_id), current_user=user, db=AsyncMock(),
        )

    assert result == "read-result"
    mocked_create.assert_awaited_once()


async def test_supplier_owner_creating_for_another_supplier_is_403_before_any_repo_call() -> None:
    user = _user(["supplier:owner"], supplier_id=uuid4())

    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock()) as mocked_lookup, \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_plot(
                payload=_payload(uuid4()), current_user=user, db=AsyncMock(),
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Cannot create a plot for another supplier"
    mocked_lookup.assert_not_awaited()
    mocked_create.assert_not_awaited()


async def test_is_supplier_admin_flag_is_scoped_the_same_as_the_owner_role() -> None:
    """_resolve_scope treats is_supplier_admin (with a linked supplier) the
    same as the supplier:owner role — so the guard must too."""
    user = _user(["external:user"], supplier_id=uuid4(), is_supplier_admin=True)

    with patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_plot(
                payload=_payload(uuid4()), current_user=user, db=AsyncMock(),
            )

    assert exc_info.value.status_code == 403
    mocked_create.assert_not_awaited()


async def test_internal_admin_is_not_blocked_for_any_supplier() -> None:
    """scope 'all' callers create for whichever supplier they name — the
    guard only constrains 'supplier'-scoped callers."""
    user = _user(["internal:admin"])
    fake_plot = SimpleNamespace(id=uuid4())

    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=None)), \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock(return_value=fake_plot)) as mocked_create, \
         patch("app.api.v1.plots._to_read", MagicMock(return_value="read-result")):
        result = await create_plot(
            payload=_payload(uuid4()), current_user=user, db=AsyncMock(),
        )

    assert result == "read-result"
    mocked_create.assert_awaited_once()


async def test_duplicate_plot_code_is_still_409_for_a_supplier_owner() -> None:
    """The pre-existing duplicate-code check still runs after the scope
    guard passes — creating your own duplicate is 409, not silently
    replaced."""
    supplier_id = uuid4()
    user = _user(["supplier:owner"], supplier_id=supplier_id)

    with patch("app.api.v1.plots.repo.get_plot_by_code", AsyncMock(return_value=SimpleNamespace())), \
         patch("app.api.v1.plots.repo.create_plot", AsyncMock()) as mocked_create:
        with pytest.raises(HTTPException) as exc_info:
            await create_plot(
                payload=_payload(supplier_id), current_user=user, db=AsyncMock(),
            )

    assert exc_info.value.status_code == 409
    mocked_create.assert_not_awaited()
