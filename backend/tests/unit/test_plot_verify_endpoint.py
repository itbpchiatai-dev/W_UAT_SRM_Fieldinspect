"""POST /api/v1/plots/{plot_id}/verify-inspection-code — RETIRED (round 8-3G).

Supersedes this file's former content, which exercised the endpoint's
business logic directly (correct/wrong code, per-supplier lookup, 404s,
response shape) via mocked repo calls. That endpoint no longer exists —
logged-in record creation needs no second gate beyond login+permission+RLS.
Companion checks to tests/security/test_plot_inspection_code_wiring.py,
from the endpoint-behavior angle rather than route-wiring.
"""
from __future__ import annotations

import inspect

from app.api.v1 import plots as plots_module


def test_verify_plot_inspection_code_function_is_gone() -> None:
    assert not hasattr(plots_module, "verify_plot_inspection_code")


def test_plots_router_carries_no_verify_inspection_code_route() -> None:
    assert not any(
        "verify-inspection-code" in getattr(r, "path", "") for r in plots_module.router.routes
    )


def test_legacy_verify_request_schema_is_gone() -> None:
    import app.schemas.plot as plot_schemas

    assert not hasattr(plot_schemas, "InspectionCodeVerifyRequest")
    assert not hasattr(plot_schemas, "InspectionCodeVerifyResult")


def test_plots_module_source_has_no_trace_of_the_retired_verify_flow() -> None:
    src = inspect.getsource(plots_module)
    assert "verify_inspection_code_plain" not in src
    assert "InspectionCodeVerifyRequest" not in src
    assert "InspectionCodeVerifyResult" not in src
