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
def _default_no_reported_harvest(request: pytest.FixtureRequest):
    """Round 31 — default "the field team has reported nothing" for every unit
    test, unless it opts out with `@pytest.mark.nodefault_reported_harvest`.

    Same rationale as the crop/variety default below. The plot importer now
    reads each cycle's harvest source record to decide whether a harvest figure
    in the file was CHANGED, and dozens of pre-existing plot_import tests pass a
    bare mock as `db` — a real query errors there rather than finding nothing.
    None of those tests are about this rule.

    Returning {} means "no cycle has a usable field report", which is the state
    every one of those fixtures was implicitly written against. Tests that
    exercise the rule patch the resolver themselves with the exact report they
    want (test_plot_import_reported_harvest_round_31.py).
    """
    if "nodefault_reported_harvest" in request.keywords:
        yield
        return

    async def _fake_sources(db, cycle_ids):
        return {}

    with patch(
        "app.repositories.plot_cycle_repository."
        "get_actual_harvest_source_records_for_cycles",
        side_effect=_fake_sources,
    ):
        yield


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

@pytest.fixture(autouse=True)
def _default_permissive_province(request: pytest.FixtureRequest):
    """Round 39 — same rationale as the crop/variety default above, for the new
    province-vs-Master-Data check.

    Dozens of plot_import tests pass arbitrary province strings and a bare
    sentinel for `db`; a real lookup query would raise rather than simply find
    nothing. Tests that exercise the validation itself opt out with
    `@pytest.mark.nodefault_province`.

    normalize_province_name is deliberately NOT stubbed. It touches no
    database, and the importer rewrites the stored value with its result — a
    test that silently skipped it would stop covering the half of this round
    that cleans the data.
    """
    if "nodefault_province" in request.keywords:
        yield
        return

    async def _fake_lookup(db, values):
        return {}

    def _fake_errors(lookup, normalized):
        return []

    with (
        patch(
            "app.services.master_data_validation.load_province_lookup",
            side_effect=_fake_lookup,
        ),
        patch(
            "app.services.master_data_validation.province_errors",
            side_effect=_fake_errors,
        ),
    ):
        yield

@pytest.fixture(autouse=True)
def _default_no_template_provinces(request: pytest.FixtureRequest):
    """Round 39 — default "no province list" for the import-template builders.

    The template now offers the 77 master_data provinces as an Excel dropdown,
    which means reading them from the database. The template tests predate that
    and pass a MagicMock for `db`, so the read raises rather than returning
    nothing. They are about excluded counts, headers and plot_status forwarding
    — not about the dropdown — so the default keeps them untouched.

    Tests that exercise the dropdown opt out with
    `@pytest.mark.nodefault_template_provinces` and supply their own list.
    """
    if "nodefault_template_provinces" in request.keywords:
        yield
        return

    async def _fake_provinces(db):
        return []

    with patch("app.api.v1.plots._template_provinces", side_effect=_fake_provinces):
        yield
