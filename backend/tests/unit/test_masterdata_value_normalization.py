"""Master Data value whitespace normalization (round 8-22B).

Before this round, repo.create() stripped `value` but repo.update() did not
— "พริก" (created) and "พริก " (updated-in later) could coexist as two
DB rows that LOOK identical in the UI but aren't (the unique index on
(type, value) never caught it). A whitespace-only value also slipped past
`Field(..., min_length=1)`, since that constraint checks the RAW string
BEFORE any stripping — `"   "` has length 3.

The fix is centralized on the schema itself (MasterDataCreate/
MasterDataUpdate.value field_validator) so create and update always see the
identical normalized value, with no separate .strip() call anywhere else to
drift out of sync. This file locks in that single source of truth; route-
level duplicate/IntegrityError behavior itself is covered by round 8-22A's
test_masterdata_endpoint_duplicate.py (still green — see that file's own
run in this round's Final Report).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.masterdata import create_master_data
from app.schemas.master_data import MasterDataCreate, MasterDataUpdate

_M = "app.api.v1.masterdata.repo"


def _item(**overrides) -> SimpleNamespace:
    base = dict(
        id=uuid4(), type="crop", value="พริก", parent=None, order_index=0,
        active=True, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- schema-level: MasterDataCreate --------------------------------------

def test_create_strips_leading_and_trailing_whitespace() -> None:
    payload = MasterDataCreate(type="crop", value="  พริก  ")
    assert payload.value == "พริก"


def test_create_whitespace_only_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MasterDataCreate(type="crop", value="   ")
    assert "กรุณาระบุค่า" in str(exc.value)


def test_create_never_lowercases_or_otherwise_changes_case() -> None:
    payload = MasterDataCreate(type="crop", value="  Cherry Tomato  ")
    assert payload.value == "Cherry Tomato"


def test_create_order_index_zero_still_accepted_alongside_trimmed_value() -> None:
    payload = MasterDataCreate(type="crop", value="  ข้าว  ", order_index=0)
    assert payload.value == "ข้าว"
    assert payload.order_index == 0


# --- schema-level: MasterDataUpdate ---------------------------------------

def test_update_strips_leading_and_trailing_whitespace() -> None:
    payload = MasterDataUpdate(value="  พริก  ")
    assert payload.value == "พริก"


def test_update_whitespace_only_value_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        MasterDataUpdate(value="   ")
    assert "กรุณาระบุค่า" in str(exc.value)


def test_update_omitted_value_is_untouched() -> None:
    """value=None ("not being changed") must never be coerced into an
    error — only a PROVIDED-but-blank value is rejected."""
    payload = MasterDataUpdate(order_index=3)
    assert payload.value is None
    assert payload.order_index == 3


# --- endpoint-level: trim closes the "looks like a duplicate" gap --------

async def test_create_with_trailing_space_is_treated_as_duplicate_of_the_trimmed_existing_value() -> None:
    """Round 8-22A's duplicate pre-check compares payload.value against the
    DB — this only works if "พริก " (submitted) and "พริก" (already stored)
    normalize to the SAME string before that comparison happens."""
    existing = _item(value="พริก", active=True)
    with patch(f"{_M}.get_by_type_value", AsyncMock(return_value=existing)) as get_mock, \
         patch(f"{_M}.create", AsyncMock()) as create_mock:
        with pytest.raises(HTTPException) as exc:
            await create_master_data(
                payload=MasterDataCreate(type="crop", value="พริก "), db=AsyncMock(),
            )
    # The lookup itself must have been made with the TRIMMED value, not
    # "พริก " verbatim — otherwise a same-looking-but-whitespace-padded
    # value could dodge the duplicate check entirely.
    assert get_mock.call_args.args[1:] == ("crop", "พริก")
    assert exc.value.status_code == 409
    create_mock.assert_not_awaited()
