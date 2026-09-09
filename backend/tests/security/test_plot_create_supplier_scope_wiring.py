"""Supplier self-service plots — wiring checks.

Confirms:
- POST /api/v1/plots still requires plots.create + sets RLS context
  (granting the permission to supplier:owner must not have loosened the
  endpoint itself).
- The route body carries the app-layer supplier-scope guard (403 for a
  payload naming another supplier) via _resolve_scope — not left to RLS
  WITH CHECK alone.
- The seed grants supplier:owner exactly plots.create + plots.update on
  top of its reads — NOT plots.delete/plots.assign, and supplier:staff
  gains nothing.

No DB fixture exists in this repo, so — matching the established pattern
(tests/security/test_plot_lookup_wiring.py) — this is a source/route-table
inspection rather than a live HTTP request.
"""
from __future__ import annotations

import inspect

from app.api.v1 import plots as plots_module
from app.seed import DEFAULT_ROLES


def _role_keys(name: str) -> list[str]:
    return next(keys for role_name, _, _, keys in DEFAULT_ROLES if role_name == name)


def _create_route():
    return next(
        r for r in plots_module.router.routes
        if r.path == "" and "POST" in r.methods
    )


def test_create_plot_still_requires_permission_and_rls_context() -> None:
    route = _create_route()
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert any("require_permission" in q for q in qualnames)
    assert any(q.startswith("get_rls_context") for q in qualnames)

    src = inspect.getsource(plots_module.create_plot)
    # The decorator itself isn't in the function source; check the module
    # for the exact permission binding on this route instead.
    module_src = inspect.getsource(plots_module)
    assert "require_permission(PermissionKey.PLOTS_CREATE)" in module_src
    assert src  # sanity


def test_create_plot_has_the_app_layer_supplier_scope_guard() -> None:
    src = inspect.getsource(plots_module.create_plot)
    assert "_resolve_scope" in src
    assert 'scope == "supplier"' in src
    assert "Cannot create a plot for another supplier" in src


def test_supplier_owner_seed_is_read_only_on_plots() -> None:
    """Round P narrowed this role to view-only (migration 0056 drops the two
    keys from existing databases as well).

    A Supplier Owner is now a farmer who can log in: the inspection they could
    already record through /public/inspect, plus their own supplier's plots,
    history and reports. Managing plots is Chiatai's.

    plots.update was never merely "edit a plot" — it unlocked start / edit /
    CLOSE cycle and the whole Excel importer. Round P made closing heavier
    still: a close now also retires the plot for anyone holding plots.delete.
    Leaving plots.update here would have kept this role one permission away
    from an action reserved for admins.

    The scope guard the rest of this module tests is unchanged and still
    matters: it is what confines a supplier-scoped caller to their OWN
    supplier on every plots route, whatever permissions a future round grants
    back."""
    keys = set(_role_keys("supplier:owner"))
    assert {"plots.read", "records.read", "records.create"} <= keys, (
        "the role lost the reading and recording that are its entire purpose"
    )
    for withheld in ("plots.create", "plots.update", "plots.delete", "plots.assign"):
        assert withheld not in keys, f"supplier:owner regained {withheld}"


def test_supplier_staff_seed_still_has_no_plot_write_permissions() -> None:
    keys = set(_role_keys("supplier:staff"))
    assert "plots.create" not in keys
    assert "plots.update" not in keys
    assert "plots.delete" not in keys
