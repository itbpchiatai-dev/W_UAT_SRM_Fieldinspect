"""GET /api/v1/plots/lookup-by-qr (round 20 QR hardening) must require
login + (plots.read OR records.create), must set the RLS context before
querying, must be registered before /{plot_id}, and its 404 must not leak
which check failed — same requirements as /lookup
(tests/security/test_plot_lookup_wiring.py), just for the qr_key locator.

No integration-test DB fixture exists in this repo, so this is a static
source/route-table inspection, matching the established pattern.
"""
from __future__ import annotations

import inspect

from app.api.v1 import plots as plots_module
from app.auth.permissions import PermissionKey


def _lookup_by_qr_route():
    return next(r for r in plots_module.router.routes if r.path == "/lookup-by-qr")


def test_lookup_by_qr_route_requires_plots_read_or_records_create() -> None:
    route = _lookup_by_qr_route()
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames), (
        "expected /lookup-by-qr to depend on require_any_permission(...), not require_permission(...)"
    )

    src = inspect.getsource(plots_module)
    route_src = src[src.index('@router.get("/lookup-by-qr"'):src.index("async def lookup_plot_by_qr")]
    assert "require_any_permission(PermissionKey.PLOTS_READ, PermissionKey.RECORDS_CREATE)" in route_src
    assert PermissionKey.PLOTS_READ == "plots.read"
    assert PermissionKey.RECORDS_CREATE == "records.create"


def test_lookup_by_qr_route_sets_rls_context() -> None:
    src = inspect.getsource(plots_module)
    route_src = src[src.index('@router.get("/lookup-by-qr"'):src.index("async def lookup_plot_by_qr")]
    assert "get_rls_context" in route_src


def test_lookup_by_qr_registered_before_plot_id_route() -> None:
    """/lookup-by-qr must be registered ahead of /{plot_id} or FastAPI will
    try to parse "lookup-by-qr" as a UUID path param and 422 before the
    literal route ever gets a chance to match."""
    paths = [r.path for r in plots_module.router.routes]
    assert paths.index("/lookup-by-qr") < paths.index("/{plot_id}")


def test_lookup_by_qr_404_message_is_generic_and_reused_for_every_miss() -> None:
    src = inspect.getsource(plots_module.lookup_plot_by_qr)
    # Every 404 raise uses the same generic message (unknown key, inactive
    # plot/supplier, AND no-active-cycle round 7.1.1) — indistinguishable.
    raises = src.count("raise HTTPException")
    assert raises >= 1
    assert src.count('detail="Plot not found"') == raises
    assert "No active" not in src


def test_lookup_by_qr_uses_the_qr_key_repository_lookup() -> None:
    src = inspect.getsource(plots_module.lookup_plot_by_qr)
    assert "repo.get_plot_by_qr_key(db, qr_key)" in src
