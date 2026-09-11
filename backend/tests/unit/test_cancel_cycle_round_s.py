"""A Supplier may END its own failed season — and only that (round S).

Round P made supplier:owner view-only, because plots.update was never merely
"edit a plot": it unlocked start cycle, edit cycle, close-as-harvested and the
Excel importer as one bundle.

One of those is genuinely the Supplier's. When a planting fails, the Supplier
is who knows, and should not wait on Chiatai to record it. So that single
action gets its own key rather than handing plots.update back:

    plots.update        start / edit / close as HARVESTED / Excel import
                        -> Chiatai. Closing as harvested is a claim about a
                           crop that was actually delivered.
    plots.cancel_cycle  end the season as CANCELLED, with a written reason
                        -> supplier:owner AND internal:admin.

Three things make cancelling its own endpoint rather than a status on the
close payload, and they all point the same way: a different permission runs
it, the reason is MANDATORY, and the plot is deactivated whoever performs it.
That last one is the only place a supplier:owner causes a deactivation, and it
is deliberate — under "one plot, one cycle" (round E) a cancelled plot is
finished, and leaving it in service would keep it in the farmer's
/public/inspect list forever.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.api.v1 import plots as plots_module
from app.auth.permissions import PermissionKey
from app.schemas.plot import PlotCycleCancel, PlotCycleClose

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _body(fn) -> str:
    src = inspect.getsource(fn)
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


def _seeded(role: str) -> set[str]:
    from app.seed import DEFAULT_ROLES

    for name, _display, _scope, keys in DEFAULT_ROLES:
        if name == role:
            return set(keys or [])
    raise AssertionError(f"role {role} is no longer seeded")


# --- who may do what ---------------------------------------------------------

def test_supplier_owner_may_cancel_but_still_not_manage_plots() -> None:
    perms = _seeded("supplier:owner")
    assert "plots.cancel_cycle" in perms
    for withheld in ("plots.update", "plots.create", "plots.delete", "plots.assign"):
        assert withheld not in perms, (
            f"supplier:owner regained {withheld} — round P removed it, and "
            f"round S was supposed to hand back ONE action, not the bundle"
        )


def test_admin_may_both_cancel_and_close_as_harvested() -> None:
    perms = _seeded("internal:admin")
    assert {"plots.cancel_cycle", "plots.update", "plots.delete"} <= perms


def test_cancel_is_gated_on_its_own_key_not_plots_update() -> None:
    """Gating this on plots.update would have made the whole round pointless:
    that is the permission a Supplier does not have."""
    src = inspect.getsource(plots_module)
    i = src.index('"/{plot_id}/cycles/{cycle_id}/cancel"')
    decorator = src[i:src.index("async def cancel_plot_cycle", i)]
    assert "PermissionKey.PLOTS_CANCEL_CYCLE" in decorator
    assert "PermissionKey.PLOTS_UPDATE" not in decorator
    # and it stays scoped: a supplier-scoped caller must not reach another
    # supplier's plot
    assert "get_rls_context" in decorator


def test_closing_as_harvested_still_needs_plots_update() -> None:
    src = inspect.getsource(plots_module)
    i = src.index('"/{plot_id}/cycles/{cycle_id}/close"')
    decorator = src[i:src.index("async def close_plot_cycle", i)]
    assert "PermissionKey.PLOTS_UPDATE" in decorator
    assert "PermissionKey.PLOTS_CANCEL_CYCLE" not in decorator, (
        "the cancel key now also opens the harvested close — a Supplier could "
        "claim a crop was delivered"
    )


# --- the reason is not optional ---------------------------------------------

def test_cancel_requires_a_reason() -> None:
    body = _body(plots_module.cancel_plot_cycle)
    assert "_MSG_CANCEL_REASON_REQUIRED" in body
    assert "strip()" in body, "a reason of only spaces must not count"
    assert "422" in body


def test_the_reason_field_is_skipvalidation_so_a_422_cannot_echo_it() -> None:
    """Business text an operator typed. Any Pydantic-level rejection puts the
    value in the 422 body — the lesson from PlotPhoneSearchRequest."""
    annotation = repr(PlotCycleCancel.model_fields["reason"].annotation)
    metadata = repr(PlotCycleCancel.model_fields["reason"].metadata)
    assert "SkipValidation" in annotation or "SkipValidation" in metadata


def test_cancel_accepts_no_harvest_figures_at_all() -> None:
    """A cancelled cycle was never harvested. The old close payload had to
    REFUSE figures sent with a cancel; this payload cannot carry them."""
    forbidden = {"harvest_yield", "final_yield_after_clean", "harvest_date", "status"}
    assert not (forbidden & set(PlotCycleCancel.model_fields))


# --- close is harvested-only now --------------------------------------------

def test_close_can_no_longer_be_used_to_cancel() -> None:
    """Otherwise there would be a second route to 'cancelled' that skips the
    mandatory reason and the deactivation."""
    assert repr(PlotCycleClose.model_fields["status"].annotation) == "typing.Literal['harvested']"


# --- what cancelling does ----------------------------------------------------

def test_cancel_deactivates_the_plot_unconditionally() -> None:
    """Not behind plots.delete the way close_plot_cycle gates it — a Supplier
    does not hold that key, and this is the one deactivation they may cause."""
    body = _body(plots_module.cancel_plot_cycle)
    assert "is_active=False" in body
    assert "PLOTS_DELETE" not in body, (
        "cancel was gated on plots.delete — a Supplier would close the cycle "
        "and leave the plot in the farmer's list forever"
    )


def test_cancel_writes_the_cancelled_status_and_the_reason() -> None:
    body = _body(plots_module.cancel_plot_cycle)
    assert "CYCLE_STATUS_CANCELLED" in body
    assert "reason=reason" in body


def test_cancel_and_deactivate_share_one_transaction() -> None:
    assert "commit" not in _body(plots_module.cancel_plot_cycle)


def test_cancel_refuses_a_cycle_that_is_not_the_plots_open_one() -> None:
    """A stale page must not cancel a season that already ended, nor name some
    other plot's cycle."""
    body = _body(plots_module.cancel_plot_cycle)
    assert "get_active_cycle_for_plot_for_update" in body
    assert "cycle.id != cycle_id" in body
    assert "409" in body


def test_cancel_locks_the_plot_before_the_cycle() -> None:
    """Same Plot-before-PlotCycle order as every other cycle transition; the
    reverse order is what lets two transactions deadlock."""
    body = _body(plots_module.cancel_plot_cycle)
    assert body.index("get_plot_for_update") < body.index("get_active_cycle_for_plot_for_update")


# --- the migration -----------------------------------------------------------

def _migration() -> str:
    hits = [f for f in VERSIONS.glob("*.py") if "plots_cancel_cycle" in f.name]
    assert hits, "no migration adds the permission to existing databases"
    return hits[0].read_text(encoding="utf-8")


def test_a_migration_grants_the_key_to_both_roles() -> None:
    sql = re.sub(r"#.*", "", _migration()[_migration().index("def upgrade"):])
    assert "INSERT INTO permissions" in sql and "plots.cancel_cycle" in sql
    assert "internal:admin" in sql and "supplier:owner" in sql
    assert "def downgrade" in sql


def test_every_revision_id_fits_the_version_column() -> None:
    """alembic_version.version_num is VARCHAR(32). A longer id does NOT fail
    loudly: the migration runs, prints "Running upgrade", then rolls back when
    it tries to stamp itself — so the schema is unchanged while the log reads
    like success. Round S hit exactly that."""
    too_long = []
    for f in VERSIONS.glob("*.py"):
        m = re.search(r'^revision = "([^"]+)"', f.read_text(encoding="utf-8"), re.M)
        if m and len(m.group(1)) > 32:
            too_long.append((f.name, m.group(1), len(m.group(1))))
    assert not too_long, (
        f"revision id longer than alembic_version's VARCHAR(32): {too_long}"
    )


@pytest.mark.parametrize("role", ["supplier:owner", "internal:admin"])
def test_the_seed_and_the_migration_agree(role: str) -> None:
    """A fresh database is seeded from app/seed.py; an existing one is migrated.
    If the two disagree, the same role ends up with different powers depending
    on when its database was created."""
    assert "plots.cancel_cycle" in _seeded(role)
    assert role in _migration()


def test_every_migration_that_adds_a_permission_grants_it_to_super_admin() -> None:
    """internal:super_admin's keys=None means "every permission" only at SEED
    time: the seeder expands it against the catalogue AS IT STANDS THEN.

    A permission a MIGRATION introduces afterwards is new to an existing
    database, so nothing grants it to super_admin unless that migration says
    so. Round S's first draft left it out, reasoning that super_admin "already
    holds everything" — and the highest-privilege role ended up missing exactly
    one key, with no error anywhere. Migration 0051 had it right.

    Scanned rather than asserted against a live database so it fails in CI, on
    a fresh clone, before anyone deploys.
    """
    offenders = []
    for f in sorted(VERSIONS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        if "def upgrade" not in src:
            continue
        # Strip `#` comments FIRST. Without this the scan reads the comment
        # ABOVE the statement — this very migration explains in prose why
        # super_admin is listed — and passes while the SQL grants nothing.
        # The first version of this test did exactly that and missed the bug
        # it was written for.
        body = re.sub(r"#.*", "", src[src.index("def upgrade"):])
        if "INSERT INTO permissions" not in body:
            continue
        if "internal:super_admin" not in body:
            offenders.append(f.name)
    assert not offenders, (
        "these migrations add a permission but never grant it to "
        f"internal:super_admin, so an existing database silently leaves the "
        f"highest-privilege role without it: {offenders}"
    )


def test_the_permission_is_in_the_catalogue_for_the_admin_ui() -> None:
    """Otherwise the key works but cannot be granted or revoked from the Roles
    screen — invisible permissions are how a permission model rots."""
    from app.seed import DEFAULT_PERMISSIONS

    keys = {k for k, *_ in DEFAULT_PERMISSIONS}
    assert PermissionKey.PLOTS_CANCEL_CYCLE in keys
