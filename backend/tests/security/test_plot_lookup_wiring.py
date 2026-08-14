"""GET /api/v1/plots/lookup must require login + (plots.read OR records.create),
must set the RLS context before querying, and must never let its 404 reveal
whether the supplier code or the plot code was the one that didn't match
(caller could otherwise enumerate valid supplier codes).

No integration-test DB fixture exists in this repo yet, so — matching the
pattern already used by test_patch_user_self_guard.py — this is a static
source/route-table inspection rather than a live HTTP request.
"""
from __future__ import annotations

import inspect

from app.api.v1 import plots as plots_module
from app.auth.permissions import PermissionKey


def _lookup_route():
    return next(r for r in plots_module.router.routes if r.path == "/lookup")


def test_lookup_route_requires_plots_read_or_records_create() -> None:
    route = _lookup_route()
    # __qualname__ on the nested `_checker` closure reflects its enclosing
    # factory (require_any_permission vs. plain require_permission), so this
    # distinguishes the two even though both closures share the name _checker.
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames), (
        "expected /lookup to depend on require_any_permission(...), not require_permission(...)"
    )

    src = inspect.getsource(plots_module)
    lookup_src = src[src.index('@router.get("/lookup"'):src.index("async def lookup_plot")]
    assert "require_any_permission(PermissionKey.PLOTS_READ, PermissionKey.RECORDS_CREATE)" in lookup_src
    assert PermissionKey.PLOTS_READ == "plots.read"
    assert PermissionKey.RECORDS_CREATE == "records.create"


def test_lookup_route_sets_rls_context() -> None:
    src = inspect.getsource(plots_module)
    lookup_src = src[src.index('@router.get("/lookup"'):src.index("async def lookup_plot")]
    assert "get_rls_context" in lookup_src


def test_lookup_registered_before_plot_id_route() -> None:
    """/lookup must be registered ahead of /{plot_id} or FastAPI will try to
    parse "lookup" as a UUID path param and 422 before the literal route
    ever gets a chance to match."""
    paths = [r.path for r in plots_module.router.routes]
    assert paths.index("/lookup") < paths.index("/{plot_id}")


def test_lookup_404_message_is_generic_and_reused_for_every_miss() -> None:
    src = inspect.getsource(plots_module.lookup_plot)
    # Every 404 raise uses the SAME generic message — supplier-not-found,
    # plot-not-found, inactive, out-of-RLS-scope, AND no-active-cycle (round
    # 7.1.1) must all be indistinguishable to the caller.
    raises = src.count("raise HTTPException")
    assert raises >= 1
    assert src.count('detail="Plot not found"') == raises
    assert "Supplier not found" not in src
    # the no-active-cycle 404 must not leak "between cycles"
    assert "No active" not in src


def test_lookup_reuses_existing_case_insensitive_repository_helpers() -> None:
    """Case-insensitive trim matching must come from the already-used
    get_supplier_by_code / get_plot_by_code helpers, not a re-implementation
    in the route (which would be an easy place to regress the behavior)."""
    src = inspect.getsource(plots_module.lookup_plot)
    assert "supplier_repo.get_supplier_by_code(db, supplier_code)" in src
    assert "repo.get_plot_by_code(db, supplier.id, plot_code)" in src
