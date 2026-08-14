"""Master Data crop/variety import endpoints (round 8-15A, brief items 37-39
plus upload-guard/error-mapping regression). Calls route functions directly
with mocks — same pattern as test_plot_import_endpoint.py and the permission-
closure inspection from test_plot_access_credential_endpoint.py; never a live
HTTP server, never a live commit.
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError

from app.api.v1 import masterdata as masterdata_module
from app.api.v1.installed_routers import ROUTERS
from app.api.v1.masterdata import (
    _parse_cv_preview_state,
    _read_cv_import_upload,
    commit_crop_variety_import,
    commit_crop_variety_import_report,
    download_crop_variety_import_template,
    preview_crop_variety_import,
    preview_crop_variety_import_report,
)
from app.auth.permissions import PermissionKey
from app.schemas.master_data_import import (
    CropVarietyImportCommitResult,
    CropVarietyImportPreview,
    CropVarietyImportSummary,
)
from app.services import master_data_crop_variety_import as cv_import

_M = "app.api.v1.masterdata.cv_import"


def _upload(content: bytes = b"PK\x03\x04data", filename: str = "import.xlsx") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), email="admin@example.invalid")


# --- upload guards -----------------------------------------------------

async def test_rejects_non_xlsx_extension() -> None:
    with pytest.raises(HTTPException) as exc:
        await _read_cv_import_upload(_upload(filename="data.csv"))
    assert exc.value.status_code == 422


async def test_rejects_empty_file() -> None:
    with pytest.raises(HTTPException) as exc:
        await _read_cv_import_upload(_upload(content=b""))
    assert exc.value.status_code == 422


async def test_rejects_oversized_file() -> None:
    big = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(HTTPException) as exc:
        await _read_cv_import_upload(_upload(content=big))
    assert exc.value.status_code == 422


async def test_accepts_valid_xlsx_upload() -> None:
    assert await _read_cv_import_upload(_upload()) == b"PK\x03\x04data"


def test_malformed_preview_state_json_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state("{not json")
    assert exc.value.status_code == 422


def test_blank_preview_state_parses_to_none() -> None:
    assert _parse_cv_preview_state(None) is None
    assert _parse_cv_preview_state("") is None


# --- permission / route wiring (item 37, 38) ----------------------------

def _route(path: str, method: str):
    for r in masterdata_module.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"route not found: {method} {path}")


def _permission_keys(route) -> set[str]:
    """The PermissionKey values a route's require_permission dependencies
    close over (PermissionKey members are plain strings, e.g. 'masterdata.read')."""
    keys: set[str] = set()
    for dep in route.dependencies:
        closure = getattr(dep.dependency, "__closure__", None) or ()
        for cell in closure:
            if isinstance(cell.cell_contents, str) and "." in cell.cell_contents:
                keys.add(cell.cell_contents)
    return keys


def test_template_requires_only_masterdata_read() -> None:
    route = _route("/crop-variety-import/template", "GET")
    assert _permission_keys(route) == {PermissionKey.MASTERDATA_READ}


@pytest.mark.parametrize("path,method", [
    ("/crop-variety-import/preview", "POST"),
    ("/crop-variety-import/commit", "POST"),
    ("/crop-variety-import/preview-report", "POST"),
    ("/crop-variety-import/commit-report", "POST"),
])
def test_preview_and_commit_require_both_create_and_update(path: str, method: str) -> None:
    route = _route(path, method)
    keys = _permission_keys(route)
    assert PermissionKey.MASTERDATA_CREATE in keys
    assert PermissionKey.MASTERDATA_UPDATE in keys
    # Each is its OWN require_permission dependency (not require_any_permission)
    # — a caller missing EITHER one is rejected, matching "ต้องมีทั้ง" (both).
    assert len(route.dependencies) == 2


def test_masterdata_router_is_not_mounted_under_public() -> None:
    """Item 38: no public endpoint. The crop-variety-import routes live on
    THIS router, mounted only under /api/v1/masterdata — never
    /api/v1/public."""
    prefixes = {prefix for router, prefix in ROUTERS if router is masterdata_module.router}
    assert prefixes == {"/api/v1/masterdata"}


@pytest.mark.parametrize("path,method", [
    ("/crop-variety-import/template", "GET"),
    ("/crop-variety-import/preview", "POST"),
    ("/crop-variety-import/commit", "POST"),
    ("/crop-variety-import/preview-report", "POST"),
    ("/crop-variety-import/commit-report", "POST"),
])
def test_every_crop_variety_import_route_requires_a_permission(path: str, method: str) -> None:
    """No route in this feature is reachable without at least one
    require_permission dependency — the negative-space check for item 38."""
    route = _route(path, method)
    assert route.dependencies, f"{method} {path} has no permission dependency at all"


# --- error → HTTP mapping ------------------------------------------------

async def test_preview_maps_file_error_to_422() -> None:
    with patch(f"{_M}.build_preview", AsyncMock(side_effect=cv_import.CropVarietyImportFileError("bad"))):
        with pytest.raises(HTTPException) as exc:
            await preview_crop_variety_import(file=_upload(), db=AsyncMock())
    assert exc.value.status_code == 422


async def test_commit_maps_has_errors_to_422_with_preview_body() -> None:
    bogus_preview = CropVarietyImportPreview.model_construct(summary=None, rows=[], preview_state=None)
    with patch(f"{_M}.commit", AsyncMock(side_effect=cv_import.CropVarietyImportHasErrors(bogus_preview))):
        with pytest.raises(HTTPException) as exc:
            await commit_crop_variety_import(
                request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 422
    assert "preview" in exc.value.detail


async def test_commit_maps_state_conflict_to_409() -> None:
    with patch(f"{_M}.commit", AsyncMock(side_effect=cv_import.CropVarietyImportStateConflict())):
        with pytest.raises(HTTPException) as exc:
            await commit_crop_variety_import(
                request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 409
    assert exc.value.detail == cv_import._MSG_STATE_CHANGED


async def test_commit_maps_integrity_error_to_409_rollback() -> None:
    """Item 33 at the HTTP boundary: an IntegrityError from the service
    becomes a clean 409 — the endpoint itself never writes anything after
    that (the caller's get_db session rolls back on the raised exception)."""
    with patch(f"{_M}.commit", AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup")))):
        with pytest.raises(HTTPException) as exc:
            await commit_crop_variety_import(
                request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
            )
    assert exc.value.status_code == 409


# --- activity log wiring (item 39) --------------------------------------

async def test_commit_logs_activity_on_success() -> None:
    result = CropVarietyImportCommitResult(
        created_crops=1, created_varieties=1, activated_varieties=0,
        deactivated_varieties=0, skipped_rows=0, total_rows=1,
    )
    with patch(f"{_M}.commit", AsyncMock(return_value=result)), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        logger_instance = logger_cls.return_value
        logger_instance.log = AsyncMock()
        out = await commit_crop_variety_import(
            request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
        )
    assert out is result
    logger_instance.log.assert_awaited_once()
    kwargs = logger_instance.log.await_args.kwargs
    assert kwargs["action"] == "masterdata.crop_variety_import.commit"
    assert kwargs["resource_type"] == "master_data"
    assert kwargs["metadata"]["createdCrops"] == 1


async def test_commit_never_logs_when_it_raises() -> None:
    """No activity row for a rejected/rolled-back commit — the log call sits
    strictly AFTER a successful commit() in source order."""
    with patch(f"{_M}.commit", AsyncMock(side_effect=cv_import.CropVarietyImportStateConflict())), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        logger_instance = logger_cls.return_value
        logger_instance.log = AsyncMock()
        with pytest.raises(HTTPException):
            await commit_crop_variety_import(
                request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
            )
    logger_instance.log.assert_not_called()


async def test_commit_report_also_logs_activity_on_success() -> None:
    row_views = [{
        "row_number": 3, "raw": {}, "row_status": cv_import.ROW_STATUS_READY,
        "action": cv_import.ACTION_CREATE_CROP_AND_VARIETY, "error_message": "",
    }]
    result = CropVarietyImportCommitResult(
        created_crops=1, created_varieties=1, activated_varieties=0,
        deactivated_varieties=0, skipped_rows=0, total_rows=1,
    )
    with patch(f"{_M}.commit_row_views", AsyncMock(return_value=(result, row_views))), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        logger_instance = logger_cls.return_value
        logger_instance.log = AsyncMock()
        response = await commit_crop_variety_import_report(
            request=SimpleNamespace(), user=_user(), file=_upload(), preview_state=None, db=AsyncMock(),
        )
    logger_instance.log.assert_awaited_once()
    kwargs = logger_instance.log.await_args.kwargs
    assert kwargs["metadata"]["createdCrops"] == 1
    assert kwargs["metadata"]["createdVarieties"] == 1
    assert response.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def test_download_template_never_writes(monkeypatch) -> None:
    """Template stays a pure GET — build_template is the ONLY service call,
    and it never touches ActivityLogger (no mutation happened)."""
    with patch(f"{_M}.build_template", AsyncMock(return_value=b"PK\x03\x04fake")) as tpl, \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        response = await download_crop_variety_import_template(db=AsyncMock())
    tpl.assert_awaited_once()
    logger_cls.assert_not_called()
    assert response.body == b"PK\x03\x04fake"


async def test_preview_report_never_logs_activity() -> None:
    empty_summary = CropVarietyImportSummary(
        total_rows=0, ready_rows=0, skipped_rows=0, error_rows=0,
        crops_to_create=0, varieties_to_create=0, varieties_to_activate=0, varieties_to_deactivate=0,
    )
    with patch(f"{_M}.preview_row_views", AsyncMock(return_value=(empty_summary, []))), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        await preview_crop_variety_import_report(file=_upload(), db=AsyncMock())
    logger_cls.assert_not_called()
