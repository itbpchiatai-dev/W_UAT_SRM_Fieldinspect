"""Supplier import endpoints (round 8-20A) — routing, permissions, scope
wiring, and the HTTP mapping of every service outcome.

DB-less: the service is patched and the route functions are called directly,
matching the style of test_plot_import_template_endpoint.py /
test_phone_access_endpoints.py. No test touches a real database or creates a
real Supplier.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import app.api.v1.suppliers as mod
from app.auth.permissions import PermissionKey
from app.schemas.supplier_import import (
    SupplierImportCommitResult,
    SupplierImportPreview,
    SupplierImportPreviewState,
    SupplierImportPreviewStateRow,
    SupplierImportSummary,
)
from app.services import supplier_import as svc

_M = "app.api.v1.suppliers"


def _user(*perms):
    u = SimpleNamespace(id=uuid4(), roles=[], supplier_id=None)
    object.__setattr__(u, "_effective_permissions", set(perms))
    return u


def _upload_file(name="suppliers.xlsx", content=b"PK\x03\x04fake"):
    class _F:
        filename = name

        async def read(self):
            return content

    return _F()


def _empty_preview():
    return SupplierImportPreview(
        summary=SupplierImportSummary(
            total_rows=0, ready_rows=0, error_rows=0, suppliers_to_create=0,
            suppliers_to_update=0, suppliers_to_activate=0,
            suppliers_to_deactivate=0, unchanged_rows=0,
        ),
        rows=[],
        preview_state=SupplierImportPreviewState(file_sha256="a" * 64, rows=[]),
    )


def _empty_result():
    return SupplierImportCommitResult(
        total_rows=0, created_suppliers=0, updated_suppliers=0,
        activated_suppliers=0, deactivated_suppliers=0, unchanged_rows=0,
        error_rows=0, processed_rows=[],
    )


# --- routing / permission wiring -------------------------------------------


def _route(path, method):
    for r in mod.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


@pytest.mark.parametrize(
    "path,method",
    [
        ("/import/template", "GET"),
        ("/import/preview", "POST"),
        ("/import/commit", "POST"),
        ("/import/preview-report", "POST"),
        ("/import/commit-report", "POST"),
    ],
)
def test_every_import_route_exists(path, method):
    assert _route(path, method) is not None


@pytest.mark.parametrize(
    "path,method",
    [
        ("/import/template", "GET"),
        ("/import/preview", "POST"),
        ("/import/commit", "POST"),
        ("/import/preview-report", "POST"),
        ("/import/commit-report", "POST"),
    ],
)
def test_every_import_route_requires_suppliers_read(path, method):
    src = inspect.getsource(mod)
    idx = src.index(f'"{path}"')
    block = src[idx: idx + 400]
    assert "SUPPLIERS_READ" in block


def test_import_routes_are_registered_before_the_supplier_id_route():
    """A static multi-segment path can't collide with `/{supplier_id}`, but
    keeping the static routes first matches plots.py/masterdata.py."""
    paths = [getattr(r, "path", "") for r in mod.router.routes]
    assert paths.index("/import/template") < paths.index("/{supplier_id}")
    assert paths.index("/import/commit") < paths.index("/{supplier_id}")


def test_row_permissions_reads_the_effective_permission_set():
    assert mod._row_permissions(_user()) == (False, False)
    assert mod._row_permissions(_user(PermissionKey.SUPPLIERS_CREATE)) == (True, False)
    assert mod._row_permissions(_user(PermissionKey.SUPPLIERS_UPDATE)) == (False, True)
    assert mod._row_permissions(
        _user(PermissionKey.SUPPLIERS_CREATE, PermissionKey.SUPPLIERS_UPDATE)
    ) == (True, True)


async def test_preview_forwards_the_callers_row_permissions_and_scope():
    scope = ["SCOPE_SENTINEL"]
    with patch(f"{_M}.supplier_import.build_preview",
               AsyncMock(return_value=_empty_preview())) as mocked:
        await mod.preview_supplier_import(
            user=_user(PermissionKey.SUPPLIERS_CREATE), scope=scope,
            file=_upload_file(), db=AsyncMock(),
        )
    kwargs = mocked.await_args.kwargs
    assert kwargs["scope_conditions"] is scope
    assert kwargs["can_create"] is True
    assert kwargs["can_update"] is False


async def test_commit_forwards_the_callers_row_permissions_and_scope():
    scope = ["SCOPE_SENTINEL"]
    with patch(f"{_M}.supplier_import.commit",
               AsyncMock(return_value=_empty_result())), \
         patch(f"{_M}.ActivityLogger") as logger, \
         patch(f"{_M}.supplier_import.commit", AsyncMock(return_value=_empty_result())) as mocked:
        logger.return_value.log = AsyncMock()
        await mod.commit_supplier_import(
            request=AsyncMock(), user=_user(PermissionKey.SUPPLIERS_UPDATE), scope=scope,
            file=_upload_file(), preview_state=None, db=AsyncMock(),
        )
    kwargs = mocked.await_args.kwargs
    assert kwargs["scope_conditions"] is scope
    assert kwargs["can_create"] is False
    assert kwargs["can_update"] is True


async def test_template_forwards_scope():
    scope = ["SCOPE_SENTINEL"]
    with patch(f"{_M}.supplier_import.build_template",
               AsyncMock(return_value=b"xlsx")) as mocked:
        response = await mod.download_supplier_import_template(scope=scope, db=AsyncMock())
    assert mocked.await_args.kwargs["scope_conditions"] is scope
    assert response.headers["Cache-Control"] == "no-store"
    assert "supplier-import-template.xlsx" in response.headers["Content-Disposition"]


# --- upload validation ------------------------------------------------------


async def test_a_non_xlsx_upload_is_422():
    with pytest.raises(HTTPException) as exc:
        await mod._read_import_upload(_upload_file(name="suppliers.csv"))
    assert exc.value.status_code == 422


async def test_an_empty_upload_is_422():
    with pytest.raises(HTTPException) as exc:
        await mod._read_import_upload(_upload_file(content=b""))
    assert exc.value.status_code == 422


async def test_an_oversized_upload_is_422():
    big = b"x" * (mod._IMPORT_MAX_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        await mod._read_import_upload(_upload_file(content=big))
    assert exc.value.status_code == 422


# --- previewState boundary --------------------------------------------------


def test_blank_preview_state_is_none():
    assert mod._parse_preview_state(None) is None
    assert mod._parse_preview_state("   ") is None


def test_a_valid_preview_state_parses():
    state = SupplierImportPreviewState(
        file_sha256="a" * 64,
        rows=[SupplierImportPreviewStateRow(
            row_number=3, supplier_code="SUP001", operation="create",
            supplier_existed=False,
        )],
    )
    raw = json.dumps(state.model_dump(by_alias=True, mode="json"))
    assert mod._parse_preview_state(raw) == state


@pytest.mark.parametrize(
    "raw",
    [
        "{",                                          # malformed JSON
        '{"fileSha256": "nope", "rows": []}',         # bad digest shape
        '{"fileSha256": "' + "A" * 64 + '", "rows": []}',   # uppercase hex
        '{"rows": []}',                               # missing digest
        '{"fileSha256": "' + "a" * 64 + '", "rows": [{"rowNumber": 0, '
        '"supplierCode": "S", "operation": "create", "supplierExisted": false}]}',
        '{"fileSha256": "' + "a" * 64 + '", "rows": [{"rowNumber": 3, '
        '"supplierCode": "S", "operation": "bogus", "supplierExisted": false}]}',
    ],
)
def test_a_malformed_preview_state_is_a_generic_422(raw):
    with pytest.raises(HTTPException) as exc:
        mod._parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == mod._MSG_INVALID_PREVIEW_STATE
    # Never echoes what was rejected.
    assert "bogus" not in str(exc.value.detail)


def test_an_oversized_preview_state_is_rejected_before_parsing():
    raw = "x" * (mod._PREVIEW_STATE_MAX_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        mod._parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == mod._MSG_INVALID_PREVIEW_STATE


def test_too_many_preview_state_rows_is_rejected():
    rows = [
        {"rowNumber": i + 3, "supplierCode": f"S{i}", "operation": "create",
         "supplierExisted": False}
        for i in range(svc.MAX_IMPORT_ROWS + 1)
    ]
    raw = json.dumps({"fileSha256": "a" * 64, "rows": rows})
    with pytest.raises(HTTPException) as exc:
        mod._parse_preview_state(raw)
    assert exc.value.status_code == 422


def test_duplicate_row_numbers_in_preview_state_are_rejected():
    row = {"rowNumber": 3, "supplierCode": "S", "operation": "create",
           "supplierExisted": False}
    raw = json.dumps({"fileSha256": "a" * 64, "rows": [row, dict(row)]})
    with pytest.raises(HTTPException) as exc:
        mod._parse_preview_state(raw)
    assert exc.value.status_code == 422


# --- HTTP mapping of every service outcome ---------------------------------


async def test_row_errors_map_to_422_with_the_preview_attached():
    preview = _empty_preview()
    with patch(f"{_M}.supplier_import.commit",
               AsyncMock(side_effect=svc.SupplierImportHasErrors(preview))):
        with pytest.raises(HTTPException) as exc:
            await mod.commit_supplier_import(
                request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
                preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "preview" in exc.value.detail
    assert "ไม่ได้บันทึกข้อมูลใด" in exc.value.detail["message"]


async def test_a_state_conflict_maps_to_409():
    with patch(f"{_M}.supplier_import.commit",
               AsyncMock(side_effect=svc.SupplierImportStateConflict())):
        with pytest.raises(HTTPException) as exc:
            await mod.commit_supplier_import(
                request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
                preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 409


async def test_an_integrity_error_maps_to_409_without_echoing_the_driver_message():
    err = IntegrityError("INSERT INTO suppliers ...", {}, Exception("duplicate key SUP001"))
    with patch(f"{_M}.supplier_import.commit", AsyncMock(side_effect=err)):
        with pytest.raises(HTTPException) as exc:
            await mod.commit_supplier_import(
                request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
                preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert "SUP001" not in str(exc.value.detail)
    assert "INSERT" not in str(exc.value.detail)


async def test_a_file_error_maps_to_422():
    with patch(f"{_M}.supplier_import.commit",
               AsyncMock(side_effect=svc.SupplierImportFileError("ไม่พบคอลัมน์ status"))):
        with pytest.raises(HTTPException) as exc:
            await mod.commit_supplier_import(
                request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
                preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 422


async def test_an_unexpected_error_propagates_so_get_db_rolls_back():
    """Not caught, not converted — get_db's `except BaseException: rollback`
    is what makes the whole file all-or-nothing."""
    with patch(f"{_M}.supplier_import.commit", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError):
            await mod.commit_supplier_import(
                request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
                preview_state=None, db=AsyncMock(),
            )


async def test_a_successful_commit_is_activity_logged():
    with patch(f"{_M}.supplier_import.commit", AsyncMock(return_value=_empty_result())), \
         patch(f"{_M}.ActivityLogger") as logger:
        logger.return_value.log = AsyncMock()
        await mod.commit_supplier_import(
            request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
            preview_state=None, db=AsyncMock(),
        )
    kwargs = logger.return_value.log.await_args.kwargs
    assert kwargs["action"] == "suppliers.import.commit"
    assert kwargs["resource_type"] == "supplier"
    assert set(kwargs["metadata"]) == {
        "totalRows", "createdSuppliers", "updatedSuppliers",
        "activatedSuppliers", "deactivatedSuppliers", "unchangedRows",
    }


# --- result-workbook endpoints ---------------------------------------------


async def test_preview_report_returns_an_xlsx_and_never_writes():
    summary = _empty_preview().summary
    with patch(f"{_M}.supplier_import.preview_row_views",
               AsyncMock(return_value=(summary, []))):
        response = await mod.preview_supplier_import_report(
            user=_user(), scope=[], file=_upload_file(), db=AsyncMock(),
        )
    assert response.media_type.endswith("spreadsheetml.sheet")
    assert response.headers["Cache-Control"] == "no-store"
    assert "supplier-import-validation-" in response.headers["Content-Disposition"]


async def test_commit_report_returns_a_completed_xlsx():
    with patch(f"{_M}.supplier_import.commit_row_views",
               AsyncMock(return_value=(_empty_result(), []))), \
         patch(f"{_M}.ActivityLogger") as logger:
        logger.return_value.log = AsyncMock()
        response = await mod.commit_supplier_import_report(
            request=AsyncMock(), user=_user(), scope=[], file=_upload_file(),
            preview_state=None, db=AsyncMock(),
        )
    assert "supplier-import-result-" in response.headers["Content-Disposition"]


async def test_the_download_filename_is_never_taken_from_the_upload():
    with patch(f"{_M}.supplier_import.preview_row_views",
               AsyncMock(return_value=(_empty_preview().summary, []))):
        response = await mod.preview_supplier_import_report(
            user=_user(), scope=[],
            file=_upload_file(name="../../etc/passwd.xlsx"), db=AsyncMock(),
        )
    assert "passwd" not in response.headers["Content-Disposition"]


# --- no schema/DB change this round ----------------------------------------


def test_this_round_adds_no_migration():
    """Round 8-20A is explicitly migration-free: the Supplier import writes
    only through columns that already exist."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    assert not list(versions.glob("*supplier_import*"))
    assert not list(versions.glob("*8_20*"))
