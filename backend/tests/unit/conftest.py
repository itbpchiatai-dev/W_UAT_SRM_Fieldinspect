"""Round 8-15D — default the crop/variety-vs-Master-Data validation
(`app.services.master_data_validation`) to a permissive no-op for every test
under `backend/tests/unit/`, unless a test opts out.

Rationale: dozens of pre-existing plot_cycle/plot_import unit tests use
arbitrary crop/variety strings that were never meant to be checked against a
real Master Data table — they exercise lot numbers, phone semantics, final
harvest, rollover mechanics, permission scoping, etc. Without this default,
every one of those tests would need editing just to keep passing, even
though none of them are about this validation (and `db` in most of them is
a bare mock/sentinel — a real lookup query would error, not just "find
nothing"). Tests that specifically exercise the new validation opt out with
`@pytest.mark.nodefault_crop_variety` and patch
`app.services.master_data_validation` (or the repo it calls) with the exact
active/inactive/missing/parent scenario they want.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _default_permissive_crop_variety(request: pytest.FixtureRequest):
    if "nodefault_crop_variety" in request.keywords:
        yield
        return

    # Signatures mirror the real ones, including round 8-26C's p_code kwargs
    # — a stub that silently dropped them would still pass every test while
    # the real call site raised TypeError in production.
    async def _fake_assert(
        db, crop, variety, *, current_crop=None, current_variety=None,
        p_code=None, current_p_code=None,
    ):
        return None

    async def _fake_lookup(db, crop_values, variety_values, p_code_values=None):
        return SimpleNamespace(crops={}, varieties={}, p_codes={})

    def _fake_errors(
        lookup, crop, variety, *, current_crop=None, current_variety=None,
        p_code=None, current_p_code=None,
    ):
        return []

    with (
        patch(
            "app.services.master_data_validation.assert_crop_variety_valid",
            side_effect=_fake_assert,
        ),
        patch(
            "app.services.master_data_validation.load_crop_variety_lookup",
            side_effect=_fake_lookup,
        ),
        patch(
            "app.services.master_data_validation.crop_variety_errors",
            side_effect=_fake_errors,
        ),
    ):
        yield
