"""POST /api/v1/public/plots/verify-inspection-code — RETIRED (round 8-3G).

Supersedes this file's former content, which exercised the endpoint's
business logic directly (rate limiting, code verification, QR-key locator,
active-cycle resolution). That endpoint no longer exists — the public
inspection flow is phone-access-only now (see
app/api/v1/public_inspection_access.py and its own test suite for the live
equivalent of these behaviors). These tests instead pin the retirement
itself so a future round can't accidentally reintroduce the route or the
request/response schemas that backed it.
"""
from __future__ import annotations

from app.api.v1 import public_plots as public_plots_module


def test_public_plots_router_has_no_routes() -> None:
    """The whole module is now an empty compatibility router — kept only so
    installed_routers.py and other modules' "no public route does X" tests
    don't need touching (see the module's own docstring)."""
    assert public_plots_module.router.routes == []


def test_verify_inspection_code_public_no_longer_exists() -> None:
    assert not hasattr(public_plots_module, "verify_inspection_code_public")


def test_legacy_verify_schemas_no_longer_exist() -> None:
    import app.schemas.plot as plot_schemas

    for name in (
        "InspectionCodeVerifyRequest",
        "InspectionCodeVerifyResult",
        "PublicInspectionCodeVerifyRequest",
        "PublicInspectionCodeVerifyResponse",
    ):
        assert not hasattr(plot_schemas, name), f"{name} should have been removed"


def test_public_plots_module_no_longer_imports_verify_inspection_code_plain() -> None:
    import inspect

    src = inspect.getsource(public_plots_module)
    assert "verify_inspection_code_plain" not in src
    assert "encode_inspection_session_token" not in src
