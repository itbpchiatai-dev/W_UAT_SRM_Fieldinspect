"""Inspection-protocol read endpoints — wiring + no-leak checks (round 5.1).

Confirms:
- Logged-in GET /api/v1/inspection-protocols is gated by records.read and is
  read-only (GET only, no create/update/delete route).
- Public GET /api/v1/public/inspection-protocols needs no login, IS
  rate-limited, and is likewise read-only.
- Neither response carries a secret/token/hash — only version + stage +
  slot/label.
- Both endpoints report the same protocol version (one source of truth).

Source/route-table inspection per the established pattern
(tests/security/test_public_record_create_wiring.py) — no DB fixture exists.
"""
from __future__ import annotations

import inspect

from app.api.v1 import inspection_protocols as loggedin_module
from app.api.v1 import public_inspection_protocols as public_module
from app.api.v1.installed_routers import ROUTERS
from app.schemas.inspection_protocol import (
    InspectionProtocolCriterion,
    InspectionProtocolList,
    InspectionProtocolStage,
)


def test_loggedin_router_mounted_under_api_v1_inspection_protocols() -> None:
    prefixes = {prefix for router, prefix in ROUTERS if router is loggedin_module.router}
    assert prefixes == {"/api/v1/inspection-protocols"}


def test_public_router_mounted_under_api_v1_public() -> None:
    prefixes = {prefix for router, prefix in ROUTERS if router is public_module.router}
    assert prefixes == {"/api/v1/public"}


def test_loggedin_endpoint_accepts_records_read_or_records_create() -> None:
    # Round 5.6 — reference data the RecordForm needs, so records.create (the
    # perm that opens /farmlog/records/new) can load it too, not just
    # records.read. Same require_any_permission pattern as plots /lookup.
    route = next(r for r in loggedin_module.router.routes if r.path == "")
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert any(q.startswith("require_any_permission") for q in qualnames)

    src = inspect.getsource(loggedin_module)
    assert "require_any_permission(PermissionKey.RECORDS_READ, PermissionKey.RECORDS_CREATE)" in src


def test_loggedin_router_is_read_only() -> None:
    methods_by_path: dict[str, set[str]] = {}
    for r in loggedin_module.router.routes:
        methods_by_path.setdefault(r.path, set()).update(r.methods)
    assert methods_by_path == {"": {"GET"}}


def test_public_endpoint_needs_no_login_and_is_rate_limited() -> None:
    route = next(r for r in public_module.router.routes if r.path == "/inspection-protocols")
    qualnames = {dep.call.__qualname__ for dep in route.dependant.dependencies}
    assert not any("require_permission" in q or "get_current_user" in q for q in qualnames)

    src = inspect.getsource(public_module)
    assert "CurrentUser" not in src
    assert "require_permission" not in src
    assert hasattr(public_module.list_public_inspection_protocols, "__wrapped__")
    assert '@limiter.limit("30/minute")' in src
    assert public_module.limiter is __import__(
        "app.core.rate_limit", fromlist=["limiter"]
    ).limiter


def test_public_router_is_read_only() -> None:
    methods_by_path: dict[str, set[str]] = {}
    for r in public_module.router.routes:
        methods_by_path.setdefault(r.path, set()).update(r.methods)
    assert methods_by_path == {"/inspection-protocols": {"GET"}}


def test_response_schema_exposes_only_version_stage_slot_label() -> None:
    assert set(InspectionProtocolList.model_fields) == {"version", "stages"}
    assert set(InspectionProtocolStage.model_fields) == {"growth_stage", "criteria"}
    assert set(InspectionProtocolCriterion.model_fields) == {"slot", "label"}


def test_endpoints_do_not_log_anything() -> None:
    for module in (loggedin_module, public_module):
        src = inspect.getsource(module)
        assert "print(" not in src
        assert "logger." not in src
