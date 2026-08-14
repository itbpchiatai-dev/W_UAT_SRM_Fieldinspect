"""Regression guards for Plot read/update response loading.

PlotRead includes assignedUsers, so route serialization must not trigger an
async lazy-load of PlotAssignment.user after PATCH/assign/deactivate.
"""
from __future__ import annotations

import inspect

from app.repositories import plot_repository as repo


def test_get_plot_eager_loads_assignment_users_for_plot_read_response() -> None:
    # Round 8.0.7 — get_plot and get_plot_for_update share their loader
    # options via _plot_read_options() (avoids the two shapes drifting
    # apart), so this checks the shared helper's source rather than get_plot
    # itself.
    src = inspect.getsource(repo._plot_read_options)

    assert "selectinload(Plot.assignments).selectinload(PlotAssignment.user)" in src


def test_get_plot_for_update_locks_the_row_and_reuses_the_same_loader_options() -> None:
    src = inspect.getsource(repo.get_plot_for_update)

    assert "_plot_read_options()" in src
    assert ".with_for_update()" in src


def test_update_plot_reloads_via_get_plot_after_flush() -> None:
    src = inspect.getsource(repo.update_plot)

    assert "await db.flush()" in src
    assert "refreshed = await get_plot(db, plot.id)" in src
    assert 'attribute_names=["assignments"]' not in src
