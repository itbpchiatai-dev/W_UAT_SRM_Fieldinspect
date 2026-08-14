"""Inspection-code schema — retirement + still-valid Plot-schema checks.

Round 8-3G retired suppliers.inspection_code (SupplierCreate/SupplierUpdate
no longer carry it) and the legacy InspectionCodeVerifyRequest schema
entirely. The Plot-schema assertions below (PlotCreate/PlotUpdate/PlotRead/
PlotSummary never carrying an inspection code or hash) predate and are
unaffected by this round — Plot never had a plaintext inspection_code
field, only the hashed inspection_code_hash column dropped back in
migration 0027 — kept here unchanged."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.plot import PlotCreate, PlotRead, PlotSummary, PlotUpdate
from app.schemas.supplier import SupplierCreate, SupplierUpdate

_PLOT_BASE = dict(supplier_id=uuid4(), plot_code="P001", name="Plot One")
_SUP_BASE = dict(code="SUP001", name="Supplier One")


# --- SupplierCreate / SupplierUpdate — inspection_code retired --------

def test_supplier_create_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierCreate.model_fields


def test_supplier_update_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in SupplierUpdate.model_fields


def test_supplier_create_never_sets_an_inspection_code_attribute() -> None:
    supplier = SupplierCreate(**_SUP_BASE)
    assert not hasattr(supplier, "inspection_code")


# --- Plot schemas no longer carry an inspection code (pre-existing,
#     unaffected by round 8-3G) -----------------------------------------

def test_plot_create_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in PlotCreate.model_fields


def test_plot_update_has_no_inspection_code_field() -> None:
    assert "inspection_code" not in PlotUpdate.model_fields


def test_plot_create_rejects_a_stray_inspection_code() -> None:
    # extra="forbid" (round 8.0.4) — a stray/unknown field is now a clean
    # 422 (ValidationError), not a silent ignore.
    with pytest.raises(ValidationError):
        PlotCreate(**_PLOT_BASE, inspection_code="9999")


def test_plot_read_never_exposes_code_or_hash() -> None:
    fields = set(PlotRead.model_fields)
    assert "inspection_code" not in fields
    assert "inspection_code_hash" not in fields


def test_plot_summary_never_exposes_code_or_hash() -> None:
    fields = set(PlotSummary.model_fields)
    assert "inspection_code" not in fields
    assert "inspection_code_hash" not in fields


# --- legacy verify request schema is gone -------------------------------

def test_legacy_inspection_code_verify_request_no_longer_exists() -> None:
    import app.schemas.plot as plot_schemas

    assert not hasattr(plot_schemas, "InspectionCodeVerifyRequest")
    assert not hasattr(plot_schemas, "InspectionCodeVerifyResult")
