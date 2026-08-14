"""Supplier scope isolation (round 5) — wiring checks.

Confirms:
- list_suppliers / get_supplier use the new SupplierScopeFilter.
- create_supplier / update_supplier / deactivate_supplier are UNCHANGED —
  still permission-gated only, not scope-filtered. Per the task brief, no
  supplier-scoped role (supplier:owner, supplier:staff) carries
  suppliers.create/update/delete by default (see app/seed.py
  DEFAULT_ROLES), so the permission gate alone already prevents a supplier
  user from writing another supplier's record; adding scope there would be
  unrequested surface area.
- suppliers.py never touches RLS (get_rls_context) — suppliers has no RLS
  policy (only plots/records do, see app/api/deps/scope.py's own
  docstring), and this round is explicitly forbidden from adding one.

No DB fixture exists in this repo, so — matching the established pattern
(tests/security/test_plot_lookup_wiring.py) — this is a source/route-table
inspection rather than a live HTTP request.
"""
from __future__ import annotations

import inspect

from app.api.v1 import suppliers as suppliers_module
from app.auth.permissions import PermissionKey


def _route(path: str, method: str):
    return next(
        r for r in suppliers_module.router.routes
        if r.path == path and method in r.methods
    )


def test_list_suppliers_uses_scope_filter() -> None:
    src = inspect.getsource(suppliers_module.list_suppliers)
    assert "scope" in src
    assert "repo.list_suppliers" in src
    assert "scope_conditions=scope" in src


def test_get_supplier_uses_scoped_lookup() -> None:
    src = inspect.getsource(suppliers_module.get_supplier)
    assert "repo.get_supplier_scoped" in src


def test_write_endpoints_remain_unscoped() -> None:
    for fn in (
        suppliers_module.create_supplier,
        suppliers_module.update_supplier,
        suppliers_module.deactivate_supplier,
    ):
        src = inspect.getsource(fn)
        assert "scope" not in src, f"{fn.__name__} should stay permission-gated only, not scope-filtered"
        assert "repo.get_supplier(" in src or "repo.get_supplier_by_code(" in src or "repo.create_supplier(" in src


def test_read_routes_still_require_suppliers_read_permission() -> None:
    for path in ("", "/{supplier_id}"):
        route = _route(path, "GET")
        qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
        assert any(q.startswith("require_permission") for q in qualnames)

    src = inspect.getsource(suppliers_module)
    assert 'Depends(require_permission(PermissionKey.SUPPLIERS_READ))' in src
    assert PermissionKey.SUPPLIERS_READ == "suppliers.read"


def test_write_routes_still_require_their_original_permissions() -> None:
    src = inspect.getsource(suppliers_module)
    assert "Depends(require_permission(PermissionKey.SUPPLIERS_CREATE))" in src
    assert "Depends(require_permission(PermissionKey.SUPPLIERS_UPDATE))" in src
    assert "Depends(require_permission(PermissionKey.SUPPLIERS_DELETE))" in src


def test_suppliers_module_does_not_touch_rls() -> None:
    """suppliers has no RLS policy — this round must not start pretending
    it does (that would require a migration, which is out of scope)."""
    src = inspect.getsource(suppliers_module)
    assert "get_rls_context" not in src
    assert "RLSContext" not in src
