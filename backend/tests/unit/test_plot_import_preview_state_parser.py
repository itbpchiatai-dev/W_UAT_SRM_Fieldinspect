"""_parse_preview_state input-boundary hardening (round 8-7A.2) — the
multipart previewState field is parsed by Pydantic, then checked for size
(MAX_IMPORT_ROWS, combined across startNextRows+finalPlotRows) and duplicate
rowNumbers, BEFORE it ever reaches the service layer. This is an input-shape
guard only — it is not an authorization or state check; the service's own
under-lock verification (round 8-7A.1) remains the sole authority on whether
a row's resolution is still valid at commit time.
"""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.plots import _parse_preview_state, commit_plot_import
from app.schemas.plot_import import (
    PlotImportCommitResult,
    PlotImportFinalPlotPreviewStateRow,
    PlotImportPreviewState,
    PlotImportPreviewStateRow,
)
from app.services import plot_import

_M = "app.services.plot_import"


def _start_row(row_number: int, **over) -> PlotImportPreviewStateRow:
    base = dict(
        row_number=row_number, supplier_code="SUP001", plot_code=f"P{row_number:03d}",
        resolved_action="start_new_cycle", active_cycle_id=None,
    )
    base.update(over)
    return PlotImportPreviewStateRow(**base)


def _final_row(row_number: int, **over) -> PlotImportFinalPlotPreviewStateRow:
    base = dict(
        row_number=row_number, supplier_code="SUP001", plot_code=f"P{row_number:03d}",
        plot_updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        active_cycle_id=uuid4(), active_cycle_no=1,
        active_cycle_updated_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        cycle_label="jul2026", resolved_final_inspection_record_id=None,
    )
    base.update(over)
    return PlotImportFinalPlotPreviewStateRow(**base)


def _json(*, start_next_rows=(), final_plot_rows=(), file_sha256=None) -> str:
    state = PlotImportPreviewState(
        file_sha256=file_sha256 or ("a" * 64),
        start_next_rows=list(start_next_rows),
        final_plot_rows=list(final_plot_rows),
    )
    return state.model_dump_json(by_alias=True)


# --- item 1/2: finalPlotRows size limit -------------------------------------

def test_final_plot_rows_at_max_import_rows_is_accepted():
    rows = [_final_row(i + 2) for i in range(plot_import.MAX_IMPORT_ROWS)]
    raw = _json(final_plot_rows=rows)
    state = _parse_preview_state(raw)
    assert state is not None
    assert len(state.final_plot_rows) == plot_import.MAX_IMPORT_ROWS


def test_final_plot_rows_over_max_import_rows_is_422():
    rows = [_final_row(i + 2) for i in range(plot_import.MAX_IMPORT_ROWS + 1)]
    raw = _json(final_plot_rows=rows)
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState มีจำนวนแถวเกินกำหนด"


# --- item 3: combined size limit ---------------------------------------------

def test_combined_start_next_and_final_plot_rows_over_max_is_422():
    half = plot_import.MAX_IMPORT_ROWS // 2
    start_rows = [_start_row(i + 2) for i in range(half + 1)]
    final_rows = [_final_row(i + 2 + half + 1) for i in range(half + 1)]
    # Each list alone is under MAX_IMPORT_ROWS, but combined they exceed it.
    assert len(start_rows) <= plot_import.MAX_IMPORT_ROWS
    assert len(final_rows) <= plot_import.MAX_IMPORT_ROWS
    assert len(start_rows) + len(final_rows) > plot_import.MAX_IMPORT_ROWS
    raw = _json(start_next_rows=start_rows, final_plot_rows=final_rows)
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState มีจำนวนแถวเกินกำหนด"


# --- item 4/5/6: duplicate rowNumber -----------------------------------------

def test_duplicate_row_number_within_start_next_rows_is_422():
    raw = _json(start_next_rows=[_start_row(2), _start_row(2, plot_code="P999")])
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState มีเลขแถวซ้ำกัน"


def test_duplicate_row_number_within_final_plot_rows_is_422():
    raw = _json(final_plot_rows=[_final_row(2), _final_row(2, plot_code="P999")])
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState มีเลขแถวซ้ำกัน"


def test_duplicate_row_number_across_start_next_and_final_plot_lists_is_422():
    raw = _json(start_next_rows=[_start_row(2)], final_plot_rows=[_final_row(2)])
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState มีเลขแถวซ้ำกัน"


# --- item 7: valid mixed rows -------------------------------------------------

def test_valid_mixed_start_next_and_final_plot_rows_is_accepted():
    raw = _json(start_next_rows=[_start_row(2)], final_plot_rows=[_final_row(3)])
    state = _parse_preview_state(raw)
    assert state is not None
    assert len(state.start_next_rows) == 1
    assert len(state.final_plot_rows) == 1
    assert state.start_next_rows[0].row_number == 2
    assert state.final_plot_rows[0].row_number == 3


# --- item 8: error response never echoes previewState content ---------------

def test_duplicate_row_error_never_echoes_plot_code_or_uuid():
    secret_plot_code = "SECRET-PLOT-XYZ"
    secret_uuid = str(uuid4())
    raw = _json(
        start_next_rows=[_start_row(2, plot_code=secret_plot_code, active_cycle_id=secret_uuid)],
        final_plot_rows=[_final_row(2)],
    )
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    detail_text = str(exc.value.detail)
    assert secret_plot_code not in detail_text
    assert secret_uuid not in detail_text
    assert "Traceback" not in detail_text


def test_oversized_error_never_echoes_supplier_code():
    secret_supplier_code = "SECRET-SUP-777"
    rows = [
        _final_row(i + 2, supplier_code=secret_supplier_code)
        for i in range(plot_import.MAX_IMPORT_ROWS + 1)
    ]
    raw = _json(final_plot_rows=rows)
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state(raw)
    assert secret_supplier_code not in str(exc.value.detail)


def test_malformed_json_error_never_echoes_raw_body():
    secret = "SECRET-RAW-MARKER-abc123"
    with pytest.raises(HTTPException) as exc:
        _parse_preview_state("{not valid json, " + secret)
    assert secret not in str(exc.value.detail)


# --- item 9/10: regression — absent previewState still flows through as None -

def test_absent_preview_state_still_parses_to_none():
    assert _parse_preview_state(None) is None
    assert _parse_preview_state("") is None
    assert _parse_preview_state("   ") is None


async def test_final_plot_only_commit_without_preview_state_still_forwards_none_to_service():
    """Round 8-7A.2 regression: the parser hardening must not change what
    happens when previewState is genuinely absent — the endpoint still
    forwards None to commit_import, which is the ONLY layer that decides
    final_plot/start_next_cycle files require one (round 8-7A.1)."""
    from types import SimpleNamespace

    from app.auth.permissions import PermissionKey
    user = SimpleNamespace(
        id=uuid4(), roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None, is_supplier_admin=False,
        _effective_permissions={PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE},
    )

    import io

    from fastapi import UploadFile
    upload = UploadFile(file=io.BytesIO(b"PK\x03\x04data"), filename="import.xlsx")

    summary = PlotImportCommitResult(
        created_plots=0, started_cycles=0, updated_cycles=0, skipped_rows=0, row_results=[],
    )
    with patch(f"{_M}.commit_import", AsyncMock(return_value=summary)) as m:
        await commit_plot_import(
            current_user=user, file=upload, preview_state=None, db=MagicMock(),
        )
    assert m.await_args.kwargs["preview_state"] is None


async def test_legacy_action_file_commit_still_needs_no_preview_state_regression():
    """Same regression as above, phrased for a legacy-action file: passing no
    previewState at all must still reach commit_import as None (never
    rejected by the new size/duplicate checks, which only ever fire on a
    NON-empty parsed state)."""
    from types import SimpleNamespace

    from app.auth.permissions import PermissionKey
    user = SimpleNamespace(
        id=uuid4(), roles=[SimpleNamespace(name="internal:admin")],
        supplier_id=None, is_supplier_admin=False,
        _effective_permissions={PermissionKey.PLOTS_CREATE, PermissionKey.PLOTS_UPDATE},
    )

    import io

    from fastapi import UploadFile
    upload = UploadFile(file=io.BytesIO(b"PK\x03\x04data"), filename="import.xlsx")

    summary = PlotImportCommitResult(
        created_plots=1, started_cycles=1, updated_cycles=0, skipped_rows=0, row_results=[],
    )
    with patch(f"{_M}.commit_import", AsyncMock(return_value=summary)) as m:
        result = await commit_plot_import(
            current_user=user, file=upload, preview_state=None, db=MagicMock(),
        )
    assert m.await_args.kwargs["preview_state"] is None
    assert result.created_plots == 1
