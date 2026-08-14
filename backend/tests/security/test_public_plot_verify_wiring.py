"""Public inspection-code verification — RETIRED (round 8-3G).

Supersedes this file's former wiring checks (no-login, rate-limit, RLS
context, response-schema shape) for POST
/api/v1/public/plots/verify-inspection-code, which no longer exists. The
public inspection flow is phone-access-only now — see
tests/unit/test_phone_access_endpoints.py for the live wiring checks. These
tests instead pin that the retirement is real and complete: no route, no
schemas, no plaintext-code-verification helper wired into any public
endpoint.
"""
from __future__ import annotations

import inspect

from app.api.v1 import public_plots as public_plots_module
from app.api.v1.installed_routers import ROUTERS


def test_public_plots_router_still_mounted_but_empty() -> None:
    """The empty compatibility router (see its own module docstring) stays
    mounted under /api/v1/public — mounting an empty router adds no paths,
    so this changes nothing about the app's actual surface."""
    prefixes = {prefix for router, prefix in ROUTERS if router is public_plots_module.router}
    assert prefixes == {"/api/v1/public"}
    assert public_plots_module.router.routes == []


def test_no_verify_inspection_code_route_anywhere_public() -> None:
    from app.api.v1 import public_inspection_access as access_module
    from app.api.v1 import public_records as records_module

    for mod in (public_plots_module, access_module, records_module):
        assert not any(
            "verify-inspection-code" in getattr(r, "path", "") for r in mod.router.routes
        ), mod.__name__


def test_legacy_verify_schemas_are_gone() -> None:
    import app.schemas.plot as plot_schemas

    for name in (
        "InspectionCodeVerifyRequest",
        "InspectionCodeVerifyResult",
        "PublicInspectionCodeVerifyRequest",
        "PublicInspectionCodeVerifyResponse",
    ):
        assert not hasattr(plot_schemas, name)


def test_verify_inspection_code_plain_has_no_runtime_caller() -> None:
    """The plaintext-compare helper still exists (app/services/
    inspection_code.py is historical-migration-compatibility only, never
    deleted), but no API module may import/call it anymore."""
    from app.api.v1 import plots as plots_module

    for mod in (public_plots_module, plots_module):
        src = inspect.getsource(mod)
        assert "verify_inspection_code_plain" not in src


def test_logged_in_plots_router_has_no_verify_inspection_code_route() -> None:
    from app.api.v1 import plots as plots_module

    assert not any(
        "verify-inspection-code" in getattr(r, "path", "") for r in plots_module.router.routes
    )
