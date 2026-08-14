"""Plot import commit endpoints — real HTTP multipart contract (round 8-6F).

Round 8-6E's frontend fix sends the commit's optimistic-concurrency
snapshot as a multipart field named "previewState" (camelCase — matching
every other API field in this app, AGENTS.md §12). Both commit endpoints'
`preview_state: str | None = Form(None)` had NO alias, so FastAPI bound it
to the literal wire name "preview_state" instead — a real "previewState"
field was silently dropped to None, and every start_next_cycle commit hit
409 missing_preview_state regardless of the round 8-6E race fix. Round
8-6F adds `alias="previewState"` to both (see app/api/v1/plots.py).

tests/unit/test_plot_import_endpoint.py calls the route FUNCTIONS directly
with Python keyword arguments (`preview_state=raw`) — that bypasses
FastAPI's multipart parsing/alias resolution entirely, which is exactly why
this contract bug went undetected there. These tests instead dispatch real
ASGI HTTP requests through the mounted app (httpx + ASGITransport) so
Form(alias=...) resolution genuinely executes. The DB session and service
layer are mocked/rolled back only (this round's Part F) — no real DB is
touched, no row is ever written.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps.scope import get_rls_context
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.main import app
from app.schemas.plot_import import PlotImportCommitResult
from app.services import plot_import

_M = "app.services.plot_import"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_VALID_PREVIEW_STATE = (
    '{"fileSha256":"' + ("a" * 64) + '","startNextRows":['
    '{"rowNumber":3,"supplierCode":"SUP010","plotCode":"P010",'
    '"resolvedAction":"start_new_cycle","activeCycleId":null}]}'
)


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None,
        is_supplier_admin=False,
        _effective_permissions={"plots.create", "plots.update"},
        is_active=True,
    )


async def _fake_get_db() -> AsyncIterator[object]:
    # Never actually queried — the service layer (plot_import.commit_import /
    # commit_import_execute) is mocked in every test below, so this session
    # object is passed through but never awaited against a real connection.
    yield object()


async def _noop_rls_context() -> None:
    return None


@pytest.fixture(autouse=True)
def _override_dependencies():
    app.dependency_overrides[get_current_user] = _admin_user
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_rls_context] = _noop_rls_context
    yield
    app.dependency_overrides.clear()


async def _post_import(path: str, *, data: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            path,
            files={"file": ("import.xlsx", b"PK\x03\x04fake-xlsx-bytes", _XLSX_MIME)},
            data=data or {},
        )


# --- item 1: /import/commit parses a real previewState multipart field ----

async def test_commit_multipart_previewState_field_is_parsed_and_forwarded() -> None:
    summary = PlotImportCommitResult(
        created_plots=0, started_cycles=1, updated_cycles=0, skipped_rows=0, row_results=[],
    )
    with patch(f"{_M}.commit_import", AsyncMock(return_value=summary)) as m:
        resp = await _post_import(
            "/api/v1/plots/import/commit", data={"previewState": _VALID_PREVIEW_STATE},
        )

    assert resp.status_code == 200
    forwarded = m.await_args.kwargs["preview_state"]
    assert forwarded is not None, "previewState must not be dropped to None"
    assert forwarded.file_sha256 == "a" * 64
    assert forwarded.start_next_rows[0].plot_code == "P010"


# --- item 2: /import/commit-report parses + forwards the same field -------

async def test_commit_report_multipart_previewState_field_is_parsed_and_forwarded() -> None:
    with patch(f"{_M}.commit_import_execute", AsyncMock(return_value=[])) as m, \
         patch("app.api.v1.plots.plot_import_report.build_plot_import_result_workbook", return_value=b"xlsx-bytes"):
        resp = await _post_import(
            "/api/v1/plots/import/commit-report", data={"previewState": _VALID_PREVIEW_STATE},
        )

    assert resp.status_code == 200
    forwarded = m.await_args.kwargs["preview_state"]
    assert forwarded is not None, "previewState must not be dropped to None"
    assert forwarded.file_sha256 == "a" * 64
    assert forwarded.start_next_rows[0].plot_code == "P010"


# --- item 3: malformed previewState JSON -> 422, no mutation attempted ----

async def test_commit_malformed_previewState_is_422_with_no_mutation() -> None:
    with patch(f"{_M}.commit_import", AsyncMock()) as m:
        resp = await _post_import(
            "/api/v1/plots/import/commit", data={"previewState": "{not valid json"},
        )

    assert resp.status_code == 422
    m.assert_not_awaited()  # rejected before ever reaching the service layer


async def test_commit_report_malformed_previewState_is_422_with_no_mutation() -> None:
    with patch(f"{_M}.commit_import_execute", AsyncMock()) as m:
        resp = await _post_import(
            "/api/v1/plots/import/commit-report", data={"previewState": "{not valid json"},
        )

    assert resp.status_code == 422
    m.assert_not_awaited()


# --- item 4: start_next_cycle file, no previewState sent -> still 409 -----

async def test_commit_without_previewState_still_409_missing_preview_state() -> None:
    conflict = plot_import.ImportPreviewStateConflict(
        "missing_preview_state", "กรุณาตรวจสอบไฟล์ด้วย Preview ก่อนยืนยันนำเข้า",
    )
    with patch(f"{_M}.commit_import", AsyncMock(side_effect=conflict)) as m:
        resp = await _post_import("/api/v1/plots/import/commit")  # no previewState field at all

    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["code"] == "preview_state_conflict"
    assert body["reason"] == "missing_preview_state"
    # the service WAS called (with preview_state=None) and is the one that
    # raised the conflict — confirming "no previewState sent" still reaches
    # the same optimistic-concurrency guard, not a silent bypass.
    assert m.await_args.kwargs["preview_state"] is None


async def test_commit_report_without_previewState_still_409_missing_preview_state() -> None:
    conflict = plot_import.ImportPreviewStateConflict(
        "missing_preview_state", "กรุณาตรวจสอบไฟล์ด้วย Preview ก่อนยืนยันนำเข้า",
    )
    with patch(f"{_M}.commit_import_execute", AsyncMock(side_effect=conflict)) as m:
        resp = await _post_import("/api/v1/plots/import/commit-report")

    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert body["code"] == "preview_state_conflict"
    assert body["reason"] == "missing_preview_state"
    assert m.await_args.kwargs["preview_state"] is None


# --- item 5: OpenAPI schema names the multipart field previewState --------

def _multipart_field_names(path: str) -> set[str]:
    schema = app.openapi()
    op = schema["paths"][path]["post"]
    body_schema = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    ref = body_schema["$ref"]
    schema_name = ref.rsplit("/", 1)[-1]
    return set(schema["components"]["schemas"][schema_name]["properties"])


def test_openapi_commit_multipart_field_is_previewState_not_snake_case() -> None:
    props = _multipart_field_names("/api/v1/plots/import/commit")
    assert "previewState" in props
    assert "preview_state" not in props


def test_openapi_commit_report_multipart_field_is_previewState_not_snake_case() -> None:
    props = _multipart_field_names("/api/v1/plots/import/commit-report")
    assert "previewState" in props
    assert "preview_state" not in props
