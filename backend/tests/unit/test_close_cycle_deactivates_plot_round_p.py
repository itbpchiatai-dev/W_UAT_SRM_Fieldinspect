"""Closing a cycle also retires the plot — but only for a caller who may (round P).

Under "one plot, one cycle" (round E) a plot whose season is closed will never
take another one. Leaving it in service states something untrue, and keeps it
in the farmer's /public/inspect list, the plot-status report and the Dashboard
count. So a close now deactivates the plot too.

The gate is the whole design. Closing needs plots.update; deactivating needs
plots.delete, deliberately narrower — and after round P's companion change
supplier:owner holds neither. Deactivating unconditionally in the close path
would hand every plots.update holder an action the permission model withholds
from them, and one they could not undo, because reactivate is plots.delete as
well. So:

    caller has plots.delete  ->  cycle closed AND plot deactivated
    caller does not          ->  cycle closed, plot untouched

`plotDeactivated` on the response says which happened, so the plot never just
vanishes from the caller's list with no explanation.

The Excel `final_plot` action closes cycles through a different code path and
carries the same rule; both are covered here.
"""
from __future__ import annotations

import inspect
import re

from app.api.v1 import plots as plots_module
from app.auth.permissions import PermissionKey
from app.schemas.plot import PlotCycleCloseResult, PlotCycleRead
from app.services import plot_import


def _close_body() -> str:
    src = inspect.getsource(plots_module.close_plot_cycle)
    # strip comments so a comment mentioning a permission can't satisfy a test
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


def _final_branch() -> str:
    src = inspect.getsource(plot_import)
    start = src.index("if p.action == ACTION_FINAL:", src.index("async def _execute_row"))
    end = src.index("return ACTION_FINAL", start)
    body = src[start:end]
    return "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))


# --- the gate ----------------------------------------------------------------

def test_close_deactivates_only_behind_the_plots_delete_permission() -> None:
    body = _close_body()
    assert "is_active=False" in body, "close no longer retires the plot"
    assert "PLOTS_DELETE" in body, (
        "the close path deactivates the plot without checking plots.delete — "
        "every plots.update holder would gain an action reserved for admins, "
        "and could not undo it"
    )


def test_close_does_not_deactivate_an_already_inactive_plot() -> None:
    """Writing is_active=False over an already-false plot would bump updated_at
    and log a change that did not happen."""
    assert "plot.is_active" in _close_body()


def test_the_excel_final_action_uses_the_same_gate() -> None:
    """final_plot closes cycles through the importer, not the endpoint. A rule
    enforced in only one of the two is a rule with a way around it."""
    body = _final_branch()
    assert "is_active=False" in body, "final_plot closes the cycle but leaves the plot in service"
    assert "ctx.can_reactivate" in body, (
        "final_plot deactivates without checking plots.delete (ImportContext"
        ".can_reactivate) — the Excel path would bypass the endpoint's gate"
    )


def test_deactivation_happens_after_the_close_not_before() -> None:
    """Order matters: round 8-6H refuses to deactivate a plot that still has an
    active cycle. Deactivating first would hit that invariant."""
    body = _close_body()
    assert body.index("close_cycle") < body.index("is_active=False")


def test_close_and_deactivate_share_one_transaction() -> None:
    """A plot must never be left closed-but-active by a failure in between.
    Neither commits: the endpoint's DbDep commits once, after it returns."""
    body = _close_body()
    assert "commit" not in body


# --- the response tells the caller ------------------------------------------

def test_the_close_response_reports_whether_the_plot_was_retired() -> None:
    """Without this the plot silently disappears from the caller's default
    list — the Plots page hides finished plots by default (round O)."""
    assert "plot_deactivated" in PlotCycleCloseResult.model_fields
    assert PlotCycleCloseResult.model_fields["plot_deactivated"].default is False


def test_the_close_result_stays_a_superset_of_the_old_response() -> None:
    """This endpoint used to return PlotCycleRead. Every field it had must
    still be there, or an existing client breaks on a field it relies on."""
    missing = set(PlotCycleRead.model_fields) - set(PlotCycleCloseResult.model_fields)
    assert not missing, f"PlotCycleCloseResult dropped fields from PlotCycleRead: {missing}"


def test_the_endpoint_declares_the_new_response_model() -> None:
    src = inspect.getsource(plots_module)
    route = src[src.index('"/{plot_id}/cycles/{cycle_id}/close"'):]
    assert "response_model=PlotCycleCloseResult" in route[:200]


def test_closing_still_requires_only_plots_update() -> None:
    """Round P must not have quietly raised the bar for closing itself — that
    would lock out whoever is allowed to close but not to retire."""
    src = inspect.getsource(plots_module)
    i = src.index('"/{plot_id}/cycles/{cycle_id}/close"')
    decorator = src[i:src.index("async def close_plot_cycle", i)]
    assert "PermissionKey.PLOTS_UPDATE" in decorator
    assert "PermissionKey.PLOTS_DELETE" not in decorator, (
        "closing now demands plots.delete — a caller who may close but not "
        "retire can no longer close at all"
    )
    assert PermissionKey.PLOTS_DELETE != PermissionKey.PLOTS_UPDATE


# --- supplier:owner is view-only --------------------------------------------

def _seeded_role_permissions(role: str) -> set[str]:
    from app.seed import DEFAULT_ROLES

    for name, _display, _scope, keys in DEFAULT_ROLES:
        if name == role:
            return set(keys or [])
    raise AssertionError(f"role {role} is no longer seeded")


def test_supplier_owner_can_no_longer_manage_plots() -> None:
    """A Supplier Owner is a farmer who can log in. plots.update was never just
    "edit a plot": it unlocked start / edit / close cycle and the whole Excel
    importer — including, after round P, a close that retires the plot."""
    perms = _seeded_role_permissions("supplier:owner")
    assert "plots.update" not in perms
    assert "plots.create" not in perms


def test_supplier_owner_keeps_reading_and_recording() -> None:
    """The point of the role: everything /public/inspect gives a farmer, plus
    the history and reports that flow cannot."""
    perms = _seeded_role_permissions("supplier:owner")
    assert {"plots.read", "records.read", "records.create", "suppliers.read"} <= perms


def test_supplier_owner_never_had_and_still_lacks_the_retire_privilege() -> None:
    assert "plots.delete" not in _seeded_role_permissions("supplier:owner")


def test_admin_can_still_close_and_retire_in_one_step() -> None:
    perms = _seeded_role_permissions("internal:admin")
    assert {"plots.update", "plots.delete"} <= perms


def test_a_migration_removes_the_two_keys_from_existing_databases() -> None:
    """Editing app/seed.py alone only affects a FRESH database; every existing
    environment already has the rows."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    hits = [f for f in versions.glob("*.py") if "supplier_owner_view_only" in f.name]
    assert hits, "no migration drops the permissions from existing databases"
    src = hits[0].read_text(encoding="utf-8")
    # Only the executable half: the module docstring explains what is NOT
    # touched, and matching that prose would defeat the last assertion here.
    sql = re.sub(r"#.*", "", src[src.index("def upgrade"):])
    assert "DELETE FROM role_permissions" in sql
    assert "supplier:owner" in sql
    assert "plots.create" in sql and "plots.update" in sql
    assert "def downgrade" in sql and "INSERT INTO role_permissions" in sql, (
        "the migration cannot be undone"
    )
    assert "user_permission_overrides" not in sql, (
        "a per-user override is an admin's individual decision, not this "
        "role's to revoke"
    )
