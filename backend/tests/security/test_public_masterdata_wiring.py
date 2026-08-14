"""Public master-data endpoint (round 19.1) — wiring + no-leak checks.

Confirms:
- GET /api/v1/public/masterdata requires no login (no CurrentUser/
  require_permission dependency) — same posture as the other two public
  endpoints (verify-inspection-code, records/with-photos).
- It IS rate-limited (@limiter.limit).
- `type` is a Literal allowlist containing exactly {crop, variety,
  growth_stage, weather} — no path to `level`/`severity`/`irrigation`/
  `fertilizer` or any other internal-only master-data category.
- active_only is hardcoded True in source, never a request-controlled
  parameter.
- The response schema (PublicMasterDataItem) carries only value/parent —
  no id, type, order_index, active flag, or timestamps.
- This router carries no POST/PATCH/PUT/DELETE route at all — read-only.
- It's mounted under /api/v1/public per the router table.
- Nothing in the route logs anything.

No DB fixture exists in this repo, so — matching the established pattern
(tests/security/test_public_plot_verify_wiring.py) — this is a
source/route-table inspection rather than a live HTTP request.
"""
from __future__ import annotations

import inspect

from app.api.v1 import public_masterdata as public_masterdata_module
from app.api.v1.installed_routers import ROUTERS
from app.schemas.master_data import PublicMasterDataItem


def _public_route():
    return next(
        r for r in public_masterdata_module.router.routes
        if r.path == "/masterdata"
    )


def test_public_router_mounted_under_api_v1_public() -> None:
    prefixes = {prefix for router, prefix in ROUTERS if router is public_masterdata_module.router}
    assert prefixes == {"/api/v1/public"}


def test_endpoint_does_not_require_login() -> None:
    route = _public_route()
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert not any("require_permission" in q or "require_any_permission" in q for q in qualnames)
    assert not any("get_current_user" in q for q in qualnames)

    src = inspect.getsource(public_masterdata_module)
    assert "CurrentUser" not in src
    assert "require_permission" not in src


def test_endpoint_is_rate_limited() -> None:
    assert hasattr(public_masterdata_module.list_public_master_data, "__wrapped__")
    src = inspect.getsource(public_masterdata_module)
    assert '@limiter.limit("30/minute")' in src
    assert "from app.core.rate_limit import limiter" in src


def test_type_allowlist_contains_exactly_the_four_intended_categories() -> None:
    import typing

    args = typing.get_args(public_masterdata_module.PublicMasterDataType)
    assert set(args) == {"crop", "variety", "growth_stage", "weather"}
    # Internal-only categories (MasterData.tsx admin still manages these)
    # must never be reachable unauthenticated.
    for internal_only in ("level", "severity", "irrigation", "fertilizer"):
        assert internal_only not in args


def test_active_only_is_hardcoded_true_not_a_request_parameter() -> None:
    src = inspect.getsource(public_masterdata_module.list_public_master_data.__wrapped__)
    assert "active_only=True" in src
    # Not a function parameter a caller could override.
    sig = inspect.signature(public_masterdata_module.list_public_master_data.__wrapped__)
    assert "active_only" not in sig.parameters


def test_response_schema_excludes_internal_fields() -> None:
    fields = set(PublicMasterDataItem.model_fields)
    assert fields == {"value", "parent"}
    for internal_field in ("id", "type", "order_index", "active", "created_at", "updated_at"):
        assert internal_field not in fields


def test_router_carries_no_mutation_routes() -> None:
    methods = {m for route in public_masterdata_module.router.routes for m in route.methods}
    assert methods == {"GET"}


def test_endpoint_does_not_log_anything() -> None:
    src = inspect.getsource(public_masterdata_module.list_public_master_data.__wrapped__)
    assert "print(" not in src
    assert "logger." not in src
    assert ".log(" not in src
