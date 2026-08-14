"""Plot/supplier inspection-code gate — RETIRED (round 8-3G).

Supersedes this file's former wiring checks (permission/RLS on POST
/{plot_id}/verify-inspection-code, response-schema shape, supplier-code
verification) — that endpoint no longer exists. Logged-in record creation
needs no second gate beyond login+permission+RLS, which is unaffected by
this retirement. These tests instead pin that the retirement is real and
complete.
"""
from __future__ import annotations

import inspect

from app.api.v1 import plots as plots_module
from app.repositories import plot_repository, supplier_repository


def test_no_verify_inspection_code_route() -> None:
    assert not any(
        "verify-inspection-code" in getattr(r, "path", "") for r in plots_module.router.routes
    )


def test_verify_plot_inspection_code_function_no_longer_exists() -> None:
    assert not hasattr(plots_module, "verify_plot_inspection_code")


def test_legacy_verify_schemas_are_gone() -> None:
    import app.schemas.plot as plot_schemas

    assert not hasattr(plot_schemas, "InspectionCodeVerifyRequest")
    assert not hasattr(plot_schemas, "InspectionCodeVerifyResult")


def test_plots_module_no_longer_imports_verify_inspection_code_plain() -> None:
    src = inspect.getsource(plots_module)
    assert "verify_inspection_code_plain" not in src


def test_create_and_update_plot_never_touch_an_inspection_code() -> None:
    create_src = inspect.getsource(plot_repository.create_plot)
    update_src = inspect.getsource(plot_repository.update_plot)
    for src in (create_src, update_src):
        assert "inspection_code" not in src


def test_supplier_repository_no_longer_defaults_an_inspection_code() -> None:
    create_src = inspect.getsource(supplier_repository.create_supplier)
    update_src = inspect.getsource(supplier_repository.update_supplier)
    for src in (create_src, update_src):
        assert "inspection_code" not in src
