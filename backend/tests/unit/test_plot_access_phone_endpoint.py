"""GET/PUT /plots/{plotId}/access-phones (round 8-3A).

DB-less: patches the repos and calls the route functions directly (same style
as test_plot_with_cycle_create.py). Verifies permission/RLS wiring, the
generic 404 for out-of-scope/unknown plots, the 409-on-IntegrityError, the
Plot-locked-first order for the write, the response shape, and that there is no
public route.
"""
from __future__ import annotations

import datetime
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.api.v1.plots as plots_module
from app.api.v1.plots import get_plot_access_phones, replace_plot_access_phones
from app.schemas.plot import PlotAccessPhoneConfig

_P = "app.api.v1.plots"
_NOW = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)


def _phone(phone: str, access_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), phone_normalized=phone, access_type=access_type,
        is_active=True, created_at=_NOW, updated_at=_NOW,
    )


def _db():
    return AsyncMock()


# --- GET --------------------------------------------------------------------

async def test_get_returns_active_config() -> None:
    rows = [_phone("0845552162", "primary"), _phone("0812345678", "additional")]
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.phone_repo.list_active_plot_access_phones", AsyncMock(return_value=rows)):
        result = await get_plot_access_phones(plot_id=uuid4(), db=_db())
    assert result.primary_phone == "0845552162"
    assert result.additional_phones == ["0812345678"]
    assert len(result.items) == 2
    assert {i.phone for i in result.items} == {"0845552162", "0812345678"}


async def test_get_out_of_scope_or_unknown_is_404() -> None:
    with patch(f"{_P}.repo.get_plot", AsyncMock(return_value=None)), \
         patch(f"{_P}.phone_repo.list_active_plot_access_phones", AsyncMock()) as mk:
        with pytest.raises(HTTPException) as exc:
            await get_plot_access_phones(plot_id=uuid4(), db=_db())
    assert exc.value.status_code == 404
    mk.assert_not_awaited()  # never queried phones for an out-of-scope plot


# --- PUT --------------------------------------------------------------------

async def test_put_replaces_and_returns_config() -> None:
    rows = [_phone("0845552162", "primary")]
    payload = PlotAccessPhoneConfig(primaryPhone="0845552162")
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=SimpleNamespace(id=uuid4()))) as mk_lock, \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock(return_value=rows)) as mk_replace:
        result = await replace_plot_access_phones(plot_id=uuid4(), payload=payload, db=_db())
    mk_lock.assert_awaited_once()      # Plot locked first
    mk_replace.assert_awaited_once()
    assert result.primary_phone == "0845552162"
    assert result.additional_phones == []


async def test_put_locks_plot_before_touching_phones() -> None:
    """The write path must use get_plot_for_update (the aggregate lock), not the
    unlocked get_plot."""
    payload = PlotAccessPhoneConfig()
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.repo.get_plot", AsyncMock()) as mk_unlocked, \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock(return_value=[])):
        await replace_plot_access_phones(plot_id=uuid4(), payload=payload, db=_db())
    mk_unlocked.assert_not_awaited()


async def test_put_out_of_scope_or_unknown_is_404() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=None)), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock()) as mk:
        with pytest.raises(HTTPException) as exc:
            await replace_plot_access_phones(
                plot_id=uuid4(), payload=PlotAccessPhoneConfig(), db=_db()
            )
    assert exc.value.status_code == 404
    mk.assert_not_awaited()


# --- round 8-17C: invalid phone is 422, never echoed, fails before DB -----

async def test_put_invalid_phone_is_422_never_echoed_and_fails_before_lock() -> None:
    """Round 8-17C — normalize_and_validate_phone_config runs first thing,
    before the Plot is even looked up, exactly like the old model_validator
    used to run before the endpoint ran at all."""
    secret_bad_phone = "0712345678-not-a-real-number"
    payload = PlotAccessPhoneConfig(primaryPhone=secret_bad_phone)
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock()) as mk_lock, \
         patch(f"{_P}.phone_repo.replace_plot_access_phones", AsyncMock()) as mk_replace:
        with pytest.raises(HTTPException) as exc:
            await replace_plot_access_phones(plot_id=uuid4(), payload=payload, db=_db())
    assert exc.value.status_code == 422
    assert secret_bad_phone not in exc.value.detail
    mk_lock.assert_not_awaited()
    mk_replace.assert_not_awaited()


async def test_put_duplicate_additional_is_422_never_echoed() -> None:
    secret_phone = "0812345678"
    payload = PlotAccessPhoneConfig(additionalPhones=[secret_phone, "081-234-5678"])
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock()) as mk_lock:
        with pytest.raises(HTTPException) as exc:
            await replace_plot_access_phones(plot_id=uuid4(), payload=payload, db=_db())
    assert exc.value.status_code == 422
    assert secret_phone not in exc.value.detail
    mk_lock.assert_not_awaited()


async def test_put_error_detail_is_a_plain_string_not_a_pydantic_error_list() -> None:
    """The response `detail` must be a single string — never Pydantic's
    auto-generated list-of-error-objects shape (which is where the
    `input` PII echo used to live)."""
    payload = PlotAccessPhoneConfig(additionalPhones=["0812345678"])  # no primary
    with pytest.raises(HTTPException) as exc:
        await replace_plot_access_phones(plot_id=uuid4(), payload=payload, db=_db())
    assert isinstance(exc.value.detail, str)


async def test_put_integrityerror_becomes_clean_409() -> None:
    with patch(f"{_P}.repo.get_plot_for_update", AsyncMock(return_value=SimpleNamespace(id=uuid4()))), \
         patch(f"{_P}.phone_repo.replace_plot_access_phones",
               AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await replace_plot_access_phones(
                plot_id=uuid4(), payload=PlotAccessPhoneConfig(primaryPhone="0845552162"), db=_db()
            )
    assert exc.value.status_code == 409
    assert exc.value.detail  # non-empty, no raw stack trace


# --- wiring / permissions / no-public --------------------------------------

def test_routes_require_correct_permissions_and_rls() -> None:
    src = inspect.getsource(plots_module)
    get_block = src[
        src.index('"/{plot_id}/access-phones"'):
        src.index("async def get_plot_access_phones")
    ]
    assert "require_permission(PermissionKey.PLOTS_READ)" in get_block
    assert "get_rls_context" in get_block

    put_block = src[
        src.index("async def get_plot_access_phones"):
        src.index("async def replace_plot_access_phones")
    ]
    assert "require_permission(PermissionKey.PLOTS_UPDATE)" in put_block
    assert "get_rls_context" in put_block


def test_no_new_permission_key_introduced() -> None:
    from app.auth.permissions import PermissionKey
    # Only the existing plots.read/plots.update gate these — no phone-specific perm.
    keys = {v for k, v in vars(PermissionKey).items() if not k.startswith("_")}
    assert not any("phone" in str(v).lower() for v in keys)


def test_no_public_access_phones_route() -> None:
    import app.api.v1.public_plots as public_plots
    import app.api.v1.public_records as public_records
    for mod in (public_plots, public_records):
        assert not any(
            "access-phone" in r.path.lower() for r in mod.router.routes
        ), mod.__name__
