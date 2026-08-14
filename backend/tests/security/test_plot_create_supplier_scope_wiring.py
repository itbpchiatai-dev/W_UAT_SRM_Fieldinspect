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


def test_supplier_owner_seed_grants_create_and_update_but_not_delete_or_assign() -> None:
    keys = set(_role_keys("supplier:owner"))
    assert {"plots.read", "plots.create", "plots.update"} <= keys
    assert "plots.delete" not in keys
    assert "plots.assign" not in keys


def test_supplier_staff_seed_still_has_no_plot_write_permissions() -> None:
    keys = set(_role_keys("supplier:staff"))
    assert "plots.create" not in keys
    assert "plots.update" not in keys
    assert "plots.delete" not in keys
