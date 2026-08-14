"""OpenAPI schema — round 8-3G retirement, whole-app view.

The route-table-level checks in test_public_plot_verify_wiring.py and
test_plot_inspection_code_wiring.py prove no *router* carries the retired
verify-inspection-code routes; this generates the app's actual OpenAPI
document (what a client/API-explorer sees) and confirms the same thing at
that level, across every mounted router at once.
"""
from __future__ import annotations

from app.main import app


def test_openapi_has_no_verify_inspection_code_path() -> None:
    schema = app.openapi()
    paths = list(schema["paths"])
    assert not any("verify-inspection-code" in p for p in paths)


def test_openapi_has_no_legacy_verify_schemas() -> None:
    schema = app.openapi()
    schema_names = set(schema.get("components", {}).get("schemas", {}))
    for name in (
        "InspectionCodeVerifyRequest",
        "InspectionCodeVerifyResult",
        "PublicInspectionCodeVerifyRequest",
        "PublicInspectionCodeVerifyResponse",
    ):
        assert not any(name in s for s in schema_names), name


def test_openapi_supplier_schemas_have_no_inspection_code() -> None:
    schema = app.openapi()
    schemas = schema.get("components", {}).get("schemas", {})
    supplier_schema_names = [n for n in schemas if "Supplier" in n]
    assert supplier_schema_names, "expected at least one Supplier schema in the OpenAPI doc"
    for name in supplier_schema_names:
        props = schemas[name].get("properties", {})
        assert "inspectionCode" not in props, name
