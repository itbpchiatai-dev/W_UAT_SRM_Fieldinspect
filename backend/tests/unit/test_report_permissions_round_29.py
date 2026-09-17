"""Each report is its own permission, and Supplier has its own pair (round 29).

Until now all four report endpoints were gated by `plots.read` — the "may see
plots" permission. Anyone who could open the Plots page could run both reports
and download both workbooks, and there was no way to say "this role may run
that report but not this one". supplier:owner holds plots.read, so Suppliers
were already running the internal reports.

The data itself was never leaking across suppliers — RLS scopes every report
row to the caller's own supplier (verified on UAT). What was missing was
CONTROL: which report, for whom, and which columns it carries.

So the Supplier gets its own two reports, with their own keys and their own
menu entries, and their column set lives in one place per variant — hiding a
column later is one edit that moves the on-screen table AND the Excel file
together, never one without the other.
"""
from __future__ import annotations

import pytest

from app.api.v1 import reports as reports_api
from app.auth.permissions import PermissionKey

INTERNAL_PLOT_STATUS = "reports.plot_status"
INTERNAL_CYCLE_YIELD = "reports.cycle_yield"
SUPPLIER_PLOT_STATUS = "reports.plot_status_supplier"
SUPPLIER_CYCLE_YIELD = "reports.cycle_yield_supplier"


def _route(path: str, method: str = "GET"):
    for r in reports_api.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _permission_keys(route) -> set[str]:
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
    return keys


# --- the catalogue --------------------------------------------------------

@pytest.mark.parametrize("key", [
    INTERNAL_PLOT_STATUS, INTERNAL_CYCLE_YIELD,
    SUPPLIER_PLOT_STATUS, SUPPLIER_CYCLE_YIELD,
])
def test_every_report_has_its_own_permission_key(key: str) -> None:
    # PermissionKey is a plain constants class, not an Enum.
    assert key in set(vars(PermissionKey).values())


# --- one report, one key --------------------------------------------------

@pytest.mark.parametrize("path,key", [
    ("/plot-status", INTERNAL_PLOT_STATUS),
    ("/plot-status/export", INTERNAL_PLOT_STATUS),
    ("/cycle-yield", INTERNAL_CYCLE_YIELD),
    ("/cycle-yield/export", INTERNAL_CYCLE_YIELD),
    ("/supplier/plot-status", SUPPLIER_PLOT_STATUS),
    ("/supplier/plot-status/export", SUPPLIER_PLOT_STATUS),
    ("/supplier/cycle-yield", SUPPLIER_CYCLE_YIELD),
    ("/supplier/cycle-yield/export", SUPPLIER_CYCLE_YIELD),
])
def test_each_endpoint_is_gated_by_its_own_report_key(path: str, key: str) -> None:
    assert _permission_keys(_route(path)) == {key}


def test_no_report_is_gated_by_plots_read_any_more() -> None:
    """The bundle this round breaks up: seeing plots no longer means running
    reports."""
    for path in (
        "/plot-status", "/plot-status/export", "/cycle-yield", "/cycle-yield/export",
        "/supplier/plot-status", "/supplier/plot-status/export",
        "/supplier/cycle-yield", "/supplier/cycle-yield/export",
    ):
        assert PermissionKey.PLOTS_READ not in _permission_keys(_route(path))


def test_the_excel_file_needs_exactly_what_the_screen_needs() -> None:
    """Never weaker: a workbook must not be reachable by someone who cannot
    open the report it belongs to."""
    for screen, export in (
        ("/plot-status", "/plot-status/export"),
        ("/cycle-yield", "/cycle-yield/export"),
        ("/supplier/plot-status", "/supplier/plot-status/export"),
        ("/supplier/cycle-yield", "/supplier/cycle-yield/export"),
    ):
        assert _permission_keys(_route(screen)) == _permission_keys(_route(export))


def test_the_supplier_reports_are_separate_routes() -> None:
    """Separate routes, not one route that decides by role: "who may open
    what" is then readable straight off the route table and the Roles page."""
    internal = [_route("/plot-status"), _route("/cycle-yield")]
    supplier = [_route("/supplier/plot-status"), _route("/supplier/cycle-yield")]
    # Routes are unhashable, so compare identity by path/endpoint.
    assert {r.path for r in internal}.isdisjoint({r.path for r in supplier})
    assert {r.endpoint for r in internal}.isdisjoint({r.endpoint for r in supplier})


# --- every report still scopes its rows at the database ------------------

@pytest.mark.parametrize("path", [
    "/plot-status", "/plot-status/export", "/cycle-yield", "/cycle-yield/export",
    "/supplier/plot-status", "/supplier/plot-status/export",
    "/supplier/cycle-yield", "/supplier/cycle-yield/export",
])
def test_every_report_sets_the_rls_context(path: str) -> None:
    """Permissions say WHICH report; RLS still says WHICH ROWS. A supplier
    report that forgot this would show one supplier another's plots."""
    names = [
        getattr(dep.dependency, "__name__", "") for dep in _route(path).dependencies
    ]
    assert "get_rls_context" in names


# --- hiding a column, when the time comes --------------------------------
#
# The user will say which columns a Supplier should not see. What this round
# owes them is that saying it ONCE is enough: the same name must take the
# column out of the JSON the page renders AND out of the workbook, or a
# column disappears from the screen and rides out in the download.


def test_the_two_reports_declare_their_columns_in_one_place() -> None:
    assert reports_api.PLOT_STATUS_COLUMNS
    assert reports_api.CYCLE_YIELD_COLUMNS
    # The header row is derived from the spec, never a second list.
    assert reports_api._PLOT_STATUS_HEADERS == [
        c.header for c in reports_api.PLOT_STATUS_COLUMNS
    ]
    assert reports_api._CYCLE_YIELD_HEADERS == [
        c.header for c in reports_api.CYCLE_YIELD_COLUMNS
    ]


@pytest.mark.parametrize("columns,field,header", [
    (reports_api.PLOT_STATUS_COLUMNS, "oracle_invoice", "Oracle Invoice"),
    (reports_api.CYCLE_YIELD_COLUMNS, "oracle_invoice", "Oracle Invoice"),
])
def test_hiding_a_field_removes_its_workbook_column(columns, field, header) -> None:
    assert header in [c.header for c in reports_api._visible(columns, frozenset())]
    shown = reports_api._visible(columns, frozenset({field}))
    assert header not in [c.header for c in shown]
    assert len(shown) == len(columns) - 1


def test_a_hidden_field_is_a_real_row_field_so_the_json_drops_it_too() -> None:
    """response_model_exclude works on FIELD names — a column key that is not
    one would silently hide the Excel column while the API kept sending the
    value."""
    from app.schemas.report import ReportCycleYieldRow, ReportPlotStatusRow

    for columns, model in (
        (reports_api.PLOT_STATUS_COLUMNS, ReportPlotStatusRow),
        (reports_api.CYCLE_YIELD_COLUMNS, ReportCycleYieldRow),
    ):
        for column in columns:
            if column.key.startswith("calc:"):
                continue   # derived, workbook-only by nature
            assert column.key in model.model_fields, column.key


def test_nothing_is_hidden_from_the_supplier_yet() -> None:
    """Round 29 ships the mechanism, not a policy: Oracle Invoice in
    particular is shared on purpose so a Supplier can reconcile against it."""
    assert reports_api.SUPPLIER_HIDDEN_PLOT_STATUS == frozenset()
    assert reports_api.SUPPLIER_HIDDEN_CYCLE_YIELD == frozenset()


def test_the_supplier_export_uses_the_supplier_hidden_set() -> None:
    """The workbook must read the SAME set the JSON response excludes — the
    whole point of naming it once."""
    import inspect

    src = inspect.getsource(reports_api.export_supplier_plot_status_report)
    assert "SUPPLIER_HIDDEN_PLOT_STATUS" in src
    src = inspect.getsource(reports_api.export_supplier_cycle_yield_report)
    assert "SUPPLIER_HIDDEN_CYCLE_YIELD" in src

    for path, name in (
        ("/supplier/plot-status", "SUPPLIER_HIDDEN_PLOT_STATUS"),
        ("/supplier/cycle-yield", "SUPPLIER_HIDDEN_CYCLE_YIELD"),
    ):
        route = _route(path)
        assert route.response_model_exclude == getattr(reports_api, name)


# --- the migration that ships them ---------------------------------------
#
# The generic guard (test_cancel_cycle_round_s.py) asks whether a migration
# mentions internal:super_admin at all. This round adds FOUR keys at once, so
# "mentions it somewhere" is no longer the same question as "grants it every
# key" — a mutation that dropped super_admin from one of the four passed that
# guard untouched.

import pathlib  # noqa: E402
import re  # noqa: E402

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_09_17_0000-0058_report_permissions.py"
)


def _migration_source() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", [
    INTERNAL_PLOT_STATUS, INTERNAL_CYCLE_YIELD,
    SUPPLIER_PLOT_STATUS, SUPPLIER_CYCLE_YIELD,
])
def test_the_migration_grants_every_key_to_super_admin(key: str) -> None:
    """Its keys=None means "every permission" only at SEED time, so a key a
    MIGRATION adds reaches super_admin only if that migration says so — per
    key, not once per file."""
    src = _migration_source()
    body = re.sub(r"#.*", "", src[src.index("def upgrade"):])
    entry = re.search(rf'"{re.escape(key)}":\s*\(([^)]*)\)', body)
    assert entry, f"{key} is not granted to anyone in upgrade()"
    assert "internal:super_admin" in entry.group(1)


@pytest.mark.parametrize("key,roles", [
    (INTERNAL_PLOT_STATUS, {"internal:admin", "farmlog:supervisor"}),
    (INTERNAL_CYCLE_YIELD, {"internal:admin", "farmlog:supervisor"}),
    (SUPPLIER_PLOT_STATUS, {"supplier:owner"}),
    (SUPPLIER_CYCLE_YIELD, {"supplier:owner"}),
])
def test_each_key_reaches_the_roles_that_asked_for_it(key: str, roles: set[str]) -> None:
    src = _migration_source()
    entry = re.search(rf'"{re.escape(key)}":\s*\(([^)]*)\)', src)
    assert entry
    granted = set(re.findall(r"'([^']+)'|\"([^\"]+)\"", entry.group(1)))
    flat = {a or b for a, b in granted}
    assert roles <= flat, f"{key}: missing {roles - flat}"


def test_a_supplier_is_never_granted_an_internal_report() -> None:
    """The whole point of the split: supplier:owner runs the Supplier copies,
    not the internal ones."""
    src = _migration_source()
    for key in (INTERNAL_PLOT_STATUS, INTERNAL_CYCLE_YIELD):
        entry = re.search(rf'"{re.escape(key)}":\s*\(([^)]*)\)', src)
        assert entry
        assert "supplier:" not in entry.group(1)


def test_the_reports_menu_group_carries_no_permission_of_its_own() -> None:
    """api/v1/me.py keeps a permission-less parent only while it has a visible
    child — so "รายงาน" appears for whoever can run at least one report, and
    disappears for everyone else. Left on plots.read it would have shown an
    empty group to anyone who could see plots."""
    src = _migration_source()
    assert "UPDATE menu_items SET required_permission_key = ''" in src
    assert "'farmlog.reports'" in src


@pytest.mark.parametrize("key", [
    INTERNAL_PLOT_STATUS, INTERNAL_CYCLE_YIELD,
    SUPPLIER_PLOT_STATUS, SUPPLIER_CYCLE_YIELD,
])
def test_every_report_key_is_in_the_seed_catalogue(key: str) -> None:
    """Otherwise the key works but cannot be granted or revoked from the Roles
    screen — invisible permissions are how a permission model rots."""
    from app.seed import DEFAULT_PERMISSIONS

    assert key in {k for k, *_ in DEFAULT_PERMISSIONS}


def test_a_field_officer_runs_no_report() -> None:
    """Decided with the user: a Field Officer lost report access when reports
    stopped riding on plots.read, and does not get it back by default. It can
    be granted per role from the Roles screen if that changes."""
    from app.seed import DEFAULT_ROLES

    officer = next(keys for name, _l, _s, keys in DEFAULT_ROLES
                   if name == "farmlog:field_officer")
    assert not [k for k in (officer or []) if k.startswith("reports.")]

    src = _migration_source()
    assert "farmlog:field_officer" not in src
