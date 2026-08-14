"""Round 8-6K Part B/C — batched cycleLabel-reuse validation for
reactivate_plot_with_cycle rows. Fixes the N+1 introduced in round 8-6J
Part E (plot_cycle_repository.get_cycles_for_plot called once PER reactivate
row); this file covers the repository helper (get_cycle_labels_for_plots)
and the _validate_all second-pass wiring (_apply_cycle_label_history_checks)
in isolation from test_plot_import_reactivate_action.py's broader coverage.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from app.repositories.plot_cycle_repository import get_cycle_labels_for_plots
from app.services.plot_import import _label_reused_in_history_labels


# --- repository helper: one query, grouped by plot_id, None labels dropped -

async def test_get_cycle_labels_for_plots_issues_exactly_one_query():
    db = AsyncMock()
    result = AsyncMock()
    result.all = lambda: []
    db.execute.return_value = result
    await get_cycle_labels_for_plots(db, [uuid4(), uuid4(), uuid4()])
    db.execute.assert_awaited_once()


async def test_get_cycle_labels_for_plots_empty_list_never_queries():
    db = AsyncMock()
    out = await get_cycle_labels_for_plots(db, [])
    assert out == {}
    db.execute.assert_not_awaited()


async def test_get_cycle_labels_for_plots_groups_multiple_labels_per_plot():
    plot_a, plot_b = uuid4(), uuid4()
    db = AsyncMock()
    result = AsyncMock()
    result.all = lambda: [
        (plot_a, "jun2025"), (plot_a, "dec2025"), (plot_b, "aug2026"),
    ]
    db.execute.return_value = result
    out = await get_cycle_labels_for_plots(db, [plot_a, plot_b])
    assert out == {plot_a: {"jun2025", "dec2025"}, plot_b: {"aug2026"}}


async def test_get_cycle_labels_for_plots_plot_with_no_labelled_cycles_is_absent():
    plot_a = uuid4()
    db = AsyncMock()
    result = AsyncMock()
    result.all = lambda: []
    db.execute.return_value = result
    out = await get_cycle_labels_for_plots(db, [plot_a])
    assert out == {}
    assert out.get(plot_a, set()) == set()


# --- comparison helper: trim + casefold, same contract as before -----------

def test_label_reused_matches_case_and_whitespace_insensitively():
    assert _label_reused_in_history_labels({" Aug2026 "}, "aug2026") is True


def test_label_reused_no_match_returns_false():
    assert _label_reused_in_history_labels({"jun2025", "dec2025"}, "aug2026") is False


def test_label_reused_blank_label_never_matches():
    assert _label_reused_in_history_labels({"aug2026"}, "") is False
    assert _label_reused_in_history_labels({"aug2026"}, None) is False


def test_label_reused_empty_history_never_matches():
    assert _label_reused_in_history_labels(set(), "aug2026") is False
