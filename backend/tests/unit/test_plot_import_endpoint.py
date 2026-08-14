"""Plot import endpoints (round 7.5) — upload guards, scope-context building,
and error→HTTP mapping. Calls the route functions directly with mocks, like
test_record_create_endpoint.py; the service itself is covered separately.
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from app.api.v1.plots import (
    _build_import_ctx,
    _read_import_upload,
    commit_plot_import,
    preview_plot_import,
)
from app.auth.permissions import PermissionKey
from app.schemas.plot_import import PlotImportCommitResult, PlotImportPreview
from app.services import plot_import

_M = "app.services.plot_import"


def _upload(content: bytes = b"PK\x03\x04data", filename: str = "import.xlsx") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _user(*, roles: list[str], supplier_id=None, perms=None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name=r) for r in roles],
        supplier_id=supplier_id,
        is_supplier_admin=False,
        _effective_permissions=set(perms or []),
    )


_ADMIN = dict(roles=["internal:admin"], perms={PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE})


# --- upload guards --------------------------------------------------------

async def test_rejects_non_xlsx_extension() -> None:
    with pytest.raises(HTTPException) as exc:
        await _read_import_upload(_upload(filename="data.csv"))
    assert exc.value.status_code == 422


async def test_rejects_empty_file() -> None:
    with pytest.raises(HTTPException) as exc:
        await _read_import_upload(_upload(content=b"", filename="x.xlsx"))
    assert exc.value.status_code == 422


async def test_rejects_oversized_file() -> None:
    big = b"x" * (2 * 1024 * 1024 + 1)
    with pytest.raises(HTTPException) as exc:
        await _read_import_upload(_upload(content=big))
    assert exc.value.status_code == 422


async def test_accepts_valid_xlsx_upload() -> None:
    assert await _read_import_upload(_upload()) == b"PK\x03\x04data"


# --- scope context --------------------------------------------------------

def test_ctx_full_access_allows_any_supplier() -> None:
    ctx = _build_import_ctx(_user(**_ADMIN))
    assert ctx.allowed_supplier_id is None
    assert ctx.can_create and ctx.can_update


def test_ctx_supplier_owner_scoped_to_own_supplier() -> None:
    sid = uuid4()
    user = _user(roles=["supplier:owner"], supplier_id=sid, perms={PermissionKey.PLOTS_UPDATE})
    ctx = _build_import_ctx(user)
    assert ctx.allowed_supplier_id == sid
    assert ctx.can_update and not ctx.can_create


def test_ctx_field_officer_forbidden() -> None:
    with pytest.raises(HTTPException) as exc:
        _build_import_ctx(_user(roles=["farmlog:field_officer"]))
    assert exc.value.status_code == 403


# --- endpoint plumbing ----------------------------------------------------

async def test_preview_endpoint_returns_service_preview() -> None:
    preview = PlotImportPreview(total_rows=0, valid_rows=0, error_rows=0, rows=[])
    with patch(f"{_M}.build_preview", AsyncMock(return_value=preview)):
        result = await preview_plot_import(
            current_user=_user(**_ADMIN), file=_upload(), db=MagicMock(),
        )
    assert result is preview


async def test_commit_endpoint_maps_row_errors_to_422_with_preview() -> None:
    preview = PlotImportPreview(
        total_rows=1, valid_rows=0, error_rows=1,
        rows=[{"rowNumber": 2, "status": "error", "message": "bad"}],
    )
    with patch(f"{_M}.commit_import", AsyncMock(side_effect=plot_import.ImportHasErrors(preview))):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import(current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 422
    assert "preview" in exc.value.detail


async def test_commit_endpoint_maps_file_error_to_422() -> None:
    with patch(f"{_M}.commit_import", AsyncMock(side_effect=plot_import.ImportFileError("bad file"))):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import(current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 422


async def test_commit_endpoint_maps_integrity_error_to_409() -> None:
    from sqlalchemy.exc import IntegrityError

    err = IntegrityError("stmt", {}, Exception("dup"))
    with patch(f"{_M}.commit_import", AsyncMock(side_effect=err)):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import(current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 409


async def test_commit_endpoint_returns_summary_on_success() -> None:
    summary = PlotImportCommitResult(
        created_plots=1, started_cycles=0, updated_cycles=0, skipped_rows=0, row_results=[],
    )
    with patch(f"{_M}.commit_import", AsyncMock(return_value=summary)):
        result = await commit_plot_import(current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert result is summary


# --- round 8-2.7.2: previewState field parsing + conflict mapping ----------

async def test_commit_endpoint_parses_and_forwards_preview_state() -> None:
    # A well-formed previewState JSON string is parsed into the schema and
    # forwarded to the service (never re-derived by the endpoint).
    summary = PlotImportCommitResult(
        created_plots=0, started_cycles=1, updated_cycles=0, skipped_rows=0, row_results=[],
    )
    raw = (
        '{"fileSha256":"' + ("a" * 64) + '","startNextRows":['
        '{"rowNumber":3,"supplierCode":"SUP010","plotCode":"P010",'
        '"resolvedAction":"start_new_cycle","activeCycleId":null}]}'
    )
    with patch(f"{_M}.commit_import", AsyncMock(return_value=summary)) as m:
        await commit_plot_import(
            current_user=_user(**_ADMIN), file=_upload(), preview_state=raw, db=MagicMock())
    forwarded = m.await_args.kwargs["preview_state"]
    assert forwarded is not None
    assert forwarded.file_sha256 == "a" * 64
    assert forwarded.start_next_rows[0].plot_code == "P010"


async def test_commit_endpoint_malformed_preview_state_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await commit_plot_import(
            current_user=_user(**_ADMIN), file=_upload(),
            preview_state="{not valid json", db=MagicMock())
    assert exc.value.status_code == 422


async def test_commit_endpoint_maps_preview_state_conflict_to_409_with_code() -> None:
    conflict = plot_import.ImportPreviewStateConflict(
        "resolution_changed", "สถานะรอบปลูกมีการเปลี่ยนแปลง", [3])
    with patch(f"{_M}.commit_import", AsyncMock(side_effect=conflict)):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import(
                current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == plot_import.PREVIEW_STATE_CONFLICT_CODE
    assert exc.value.detail["changedRows"] == [3]


# --- round 8-2.4: result-workbook endpoints -------------------------------

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _state(row_number, action, *, errors=None, error_code=None, raw=None):
    from app.services.plot_import import _Parsed, _RowState
    return _RowState(
        row_number=row_number, parsed=_Parsed(action=action),
        errors=list(errors or []), error_code=error_code, raw=raw or {"action": action},
    )


async def test_preview_report_returns_xlsx_response() -> None:
    from app.api.v1.plots import preview_plot_import_report

    with patch(f"{_M}.preview_states", AsyncMock(return_value=[
        _state(3, "create_plot_with_cycle"),
    ])) as m:
        resp = await preview_plot_import_report(
            current_user=_user(**_ADMIN), file=_upload(), db=MagicMock())
    m.assert_awaited_once()
    assert resp.status_code == 200
    assert resp.media_type == _XLSX_MEDIA
    assert "validation-" in resp.headers["content-disposition"]


async def test_preview_report_file_error_maps_to_422() -> None:
    from app.api.v1.plots import preview_plot_import_report

    with patch(f"{_M}.preview_states", AsyncMock(side_effect=plot_import.ImportFileError("bad"))):
        with pytest.raises(HTTPException) as exc:
            await preview_plot_import_report(
                current_user=_user(**_ADMIN), file=_upload(), db=MagicMock())
    assert exc.value.status_code == 422


async def test_commit_report_success_returns_completed_xlsx_once() -> None:
    from app.api.v1.plots import commit_plot_import_report

    with patch(f"{_M}.commit_import_execute", AsyncMock(return_value=[
        _state(3, "create_plot_with_cycle"),
    ])) as m:
        resp = await commit_plot_import_report(
            current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    m.assert_awaited_once()  # exactly ONE execution per request
    assert resp.status_code == 200
    assert resp.media_type == _XLSX_MEDIA
    assert "result-" in resp.headers["content-disposition"]


async def test_commit_report_validation_error_returns_422_workbook_not_completed() -> None:
    from app.api.v1.plots import commit_plot_import_report
    from app.services.excel_reader import read_first_sheet

    preview = PlotImportPreview(
        total_rows=1, valid_rows=0, error_rows=1,
        rows=[{"rowNumber": 3, "status": "error", "message": "dup"}],
    )
    bad = _state(3, "close_and_start_new_cycle", errors=["dup"],
                 error_code="duplicate_rollover")
    err = plot_import.ImportHasErrors(preview, [bad])
    with patch(f"{_M}.commit_import_execute", AsyncMock(side_effect=err)):
        resp = await commit_plot_import_report(
            current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert resp.status_code == 422
    assert resp.media_type == _XLSX_MEDIA
    # nothing COMPLETED — the row is DUPLICATE, overall BLOCKED
    by_no = {n: v for n, v in read_first_sheet(resp.body)[1]}
    assert by_no[3]["resultStatus"] == "DUPLICATE"


async def test_commit_report_unexpected_exception_propagates_for_rollback() -> None:
    from app.api.v1.plots import commit_plot_import_report

    with patch(f"{_M}.commit_import_execute", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await commit_plot_import_report(
                current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())


async def test_commit_report_integrity_error_maps_to_409() -> None:
    from sqlalchemy.exc import IntegrityError

    from app.api.v1.plots import commit_plot_import_report

    err = IntegrityError("stmt", {}, Exception("dup"))
    with patch(f"{_M}.commit_import_execute", AsyncMock(side_effect=err)):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import_report(
                current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 409


async def test_commit_report_preview_state_conflict_maps_to_409_not_completed() -> None:
    # A preview-state conflict from commit-report → 409 JSON (never a COMPLETED
    # workbook, since no mutation happened).
    from app.api.v1.plots import commit_plot_import_report

    conflict = plot_import.ImportPreviewStateConflict(
        "file_digest_mismatch", "ไฟล์มีการเปลี่ยนแปลงหลังการตรวจสอบ กรุณา Preview ใหม่")
    with patch(f"{_M}.commit_import_execute", AsyncMock(side_effect=conflict)):
        with pytest.raises(HTTPException) as exc:
            await commit_plot_import_report(
                current_user=_user(**_ADMIN), file=_upload(), preview_state=None, db=MagicMock())
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == plot_import.PREVIEW_STATE_CONFLICT_CODE
    assert exc.value.detail["reason"] == "file_digest_mismatch"


def test_report_routes_are_authenticated_and_not_public() -> None:
    from app.api.v1.plots import router as plots_router
    from app.api.v1.public_plots import router as public_router

    report_routes = [r for r in plots_router.routes
                     if getattr(r, "path", "").endswith("-report")]
    paths = {r.path for r in report_routes}
    assert "/import/preview-report" in paths
    assert "/import/commit-report" in paths
    # every report route is gated (permission + rls dependencies present)
    for r in report_routes:
        assert r.dependencies, f"{r.path} must be gated"
    # the public router exposes no import/report route
    for r in public_router.routes:
        assert "import" not in getattr(r, "path", "")
        assert "report" not in getattr(r, "path", "")
