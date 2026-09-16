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

    # Signatures mirror the real ones — a stub that silently dropped a kwarg
    # would still pass every test while the real call site raised TypeError in
    # production. Round Y: the inputs are crop + p_code, and the assert hands
    # back the DERIVED variety, so the permissive default has to hand back
    # something too. None ("no P.Code, no variety") keeps every pre-existing
    # test's stored variety exactly as it was: those tests pass their own
    # cycle fixtures around and never read this value.
    async def _fake_assert(db, crop, p_code):
        return None

    async def _fake_lookup(db, crop_values, p_code_values):
        return SimpleNamespace(crops={}, varieties={}, p_codes={})

    def _fake_errors(lookup, crop, p_code):
        return []

    def _fake_variety_for_p_code(lookup, p_code):
        return None

    with (
        patch(
            "app.services.master_data_validation.assert_crop_p_code_valid",
            side_effect=_fake_assert,
        ),
        patch(
            "app.services.master_data_validation.load_crop_p_code_lookup",
            side_effect=_fake_lookup,
        ),
        patch(
            "app.services.master_data_validation.crop_p_code_errors",
            side_effect=_fake_errors,
        ),
        patch(
            "app.services.master_data_validation.variety_for_p_code",
            side_effect=_fake_variety_for_p_code,
        ),
    ):
        yield
