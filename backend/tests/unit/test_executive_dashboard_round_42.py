"""Round 42 — the executive overview: one key, three roles, one menu entry.

The page shows COMPANY-WIDE totals: plots inspected vs registered, visits in
the last 7/30 days, plots overdue past 30 days, and harvest against plan
across every supplier. That is a different thing from /dashboard/summary,
which is scoped to the caller and open to everyone authenticated.

Migration 0058 had to undo exactly the mistake this round must not repeat:
four reports were gated on `plots.read`, which supplier:owner also holds, so
Suppliers were running internal reports. The guard below is therefore not
"does a permission exist" but "is it the SAME key in all four places, and is
supplier:owner out of it".
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.api.v1 import dashboard as dashboard_api
from app.auth.permissions import PermissionKey
from app.repositories import dashboard_repository as repo
from app.seed import DEFAULT_MENUS, DEFAULT_PERMISSIONS, DEFAULT_ROLES

KEY = "executive_dashboard.read"
MENU_KEY = "farmlog.executive"
MENU_PATH = "/farmlog/executive"
HOLDERS = {"internal:super_admin", "internal:admin", "farmlog:supervisor"}

_MIGRATION = (
    Path(repo.__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_10_15_0000-0059_executive_dashboard.py"
)


def _route(path: str, method: str = "GET"):
    for r in dashboard_api.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _permission_keys(route) -> set[str]:
    """The keys a route's require_permission dependencies close over."""
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
    return keys


# --- the key is one string, spelled the same in four places ---------------

class TestTheKeyIsConsistentEverywhere:
    def test_the_constant_exists(self) -> None:
        assert PermissionKey.EXECUTIVE_DASHBOARD_READ == KEY

    def test_the_seed_catalogue_has_it(self) -> None:
        entry = next((p for p in DEFAULT_PERMISSIONS if p[0] == KEY), None)
        assert entry is not None, "permission missing from DEFAULT_PERMISSIONS"
        # is_menu=True — the same key gates the sidebar entry.
        assert entry[3] is True

    def test_the_migration_uses_the_same_string(self) -> None:
        # A typo here would seed one key and migrate another: new installs
        # would work and every existing database would show an empty sidebar.
        assert KEY in _MIGRATION.read_text(encoding="utf-8")

    def test_the_menu_entry_points_at_the_page_and_the_key(self) -> None:
        entry = next((m for m in DEFAULT_MENUS if m[0] == MENU_KEY), None)
        assert entry is not None, "menu missing from DEFAULT_MENUS"
        _key, _th, _en, _icon, path, parent, order, perm = entry
        assert path == MENU_PATH
        assert parent == "farmlog"
        assert perm == KEY
        assert order < 10, "the overview should sit above จัดการข้อมูล (order 10)"


# --- who holds it ---------------------------------------------------------

class TestGrants:
    @pytest.mark.parametrize("role", sorted(HOLDERS - {"internal:super_admin"}))
    def test_the_seeded_roles_hold_it(self, role: str) -> None:
        keys = next(r[3] for r in DEFAULT_ROLES if r[0] == role)
        assert keys is not None and KEY in keys

    def test_super_admin_holds_it_by_carrying_every_key(self) -> None:
        keys = next(r[3] for r in DEFAULT_ROLES if r[0] == "internal:super_admin")
        assert keys is None  # None = all permissions, at seed time

    def test_the_migration_grants_super_admin_explicitly(self) -> None:
        # keys=None only applies when the seed runs. A permission added by a
        # migration reaches super_admin on an EXISTING database only through
        # an explicit grant — the lesson migrations 0057 and 0058 both record.
        src = _MIGRATION.read_text(encoding="utf-8")
        grants = src.split("grants = (")[1].split(")")[0]
        assert "internal:super_admin" in grants

    def test_the_migration_grants_exactly_the_full_access_roles(self) -> None:
        src = _MIGRATION.read_text(encoding="utf-8")
        grants = src.split("grants = (")[1].split(")")[0]
        granted = {part.strip().strip('"\'') for part in grants.split(",") if part.strip()}
        assert granted == HOLDERS

    @pytest.mark.parametrize("role", ["supplier:owner", "farmlog:field_officer",
                                      "internal:user", "internal:super_user"])
    def test_no_scoped_or_minor_role_holds_it(self, role: str) -> None:
        # supplier:owner is the one that matters: it holds plots.read, which
        # is why reusing plots.read here would have shown a Supplier a page of
        # whole-company totals.
        entry = next((r for r in DEFAULT_ROLES if r[0] == role), None)
        if entry is None:
            pytest.skip(f"{role} is not a seeded role")
        keys = entry[3]
        assert keys is None or KEY not in keys

    def test_the_holders_are_the_roles_that_already_read_everything(self) -> None:
        # The permission must not widen anyone's data access — it adds a view
        # of rows these roles can already open one at a time.
        from app.api.deps.scope import _FULL_ACCESS_ROLES

        assert HOLDERS == set(_FULL_ACCESS_ROLES)


# --- the endpoint ---------------------------------------------------------

class TestEndpointGate:
    def test_the_executive_route_requires_the_key(self) -> None:
        assert _permission_keys(_route("/executive")) == {KEY}

    def test_the_scoped_summary_stays_open_to_everyone(self) -> None:
        # /summary is every authenticated user's own view; gating it would be
        # a regression, not an improvement.
        assert _permission_keys(_route("/summary")) == set()

    def test_the_executive_route_still_sets_the_rls_context(self) -> None:
        # The permission is the gate; RLS is the backstop. If the key were
        # ever granted to a scoped user, this is what keeps the totals theirs
        # instead of the company's.
        import inspect

        src = inspect.getsource(dashboard_api.get_executive)
        assert "RLSContext" in src


# --- the one piece of arithmetic that has edge cases ----------------------

class TestAchievementPct:
    def test_a_normal_season(self) -> None:
        assert repo.achievement_pct(Decimal("1000"), Decimal("775")) == 77.5

    def test_over_plan_is_a_real_result_not_a_cap(self) -> None:
        assert repo.achievement_pct(Decimal("1000"), Decimal("1250")) == 125.0

    @pytest.mark.parametrize("target,harvest", [
        (None, Decimal("500")),        # no plan recorded
        (Decimal("0"), Decimal("500")),  # a plan of zero is not a plan
        (Decimal("-5"), Decimal("500")),  # defensive: never divide by this
        (Decimal("1000"), None),       # nothing harvested yet
    ])
    def test_nothing_to_compare_returns_none_not_zero(self, target, harvest) -> None:
        # "0% of plan" reads as a failed season; None reads as "no plan on
        # record". The card shows a dash for one and a number for the other.
        assert repo.achievement_pct(target, harvest) is None

    def test_zero_harvest_against_a_real_plan_is_zero_not_none(self) -> None:
        assert repo.achievement_pct(Decimal("1000"), Decimal("0")) == 0.0


# --- the thresholds the cards describe ------------------------------------

class TestThresholds:
    def test_overdue_is_thirty_days(self) -> None:
        assert repo.OVERDUE_DAYS == 30

    def test_the_window_is_sent_to_the_client(self) -> None:
        # The card's label reads the number off the payload instead of
        # hardcoding "30", so changing the constant moves the words too.
        from app.schemas.dashboard import ExecutiveSummary

        assert "overdue_days" in ExecutiveSummary.model_fields

    def test_low_score_matches_the_other_dashboard(self) -> None:
        # DashboardSummary.low_score_count is written as `<= 3` inline; this
        # page reads the same boundary from a constant. If someone moves one,
        # this fails and they have to move both — the two pages must not
        # disagree about what "needs attention" means.
        import inspect

        assert repo.LOW_SCORE == 3
        assert "<= 3" in inspect.getsource(repo.get_summary)

    def test_every_field_of_the_payload_is_actually_filled_in(self) -> None:
        # Most of ExecutiveSummary's fields have defaults, so forgetting one
        # in the constructor yields a card quietly showing None instead of a
        # failure. Checked against the source of the return statement.
        import inspect

        from app.schemas.dashboard import ExecutiveSummary

        src = inspect.getsource(repo.get_executive_summary)
        body = src.split("return ExecutiveSummary(")[1]
        missing = [f for f in ExecutiveSummary.model_fields if f"{f}=" not in body]
        assert missing == [], f"not passed to the schema: {missing}"
