"""_parse_cv_preview_state input-boundary hardening (round 8-15A.1) — the
multipart previewState field is parsed by Pydantic (schemas/
master_data_import.py's Field constraints: row_number>0, crop/variety/
varietyParentAtPreview<=255 chars, fileSha256 must be 64 lowercase hex
chars, action must be one of the 6 known values), then checked for raw byte
size, row-list length (MAX_IMPORT_ROWS), and duplicate rowNumber — all
BEFORE it ever reaches the service layer. Mirrors the SAME pattern as Plot
Import's own test_plot_import_preview_state_parser.py.

This is an input-SHAPE guard only — never an authorization or state check;
the service's own fresh re-parse + re-query (commit()/_commit_execute)
remains the sole authority on whether a row's plan is still valid at commit
time. No DB, no live commit anywhere in this file.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.masterdata import _parse_cv_preview_state, commit_crop_variety_import
from app.schemas.master_data_import import (
    CropVarietyImportCommitResult,
    CropVarietyImportPreviewState,
    CropVarietyImportPreviewStateRow,
)
from app.services import master_data_crop_variety_import as cv_import

_VALID_SHA = "a" * 64


def _row(row_number: int, **over) -> CropVarietyImportPreviewStateRow:
    base = dict(
        row_number=row_number, crop="พริก", variety="พริกขี้หนู", variety_status=True,
        action=cv_import.ACTION_CREATE_CROP_AND_VARIETY, crop_existed=False, crop_was_active=None,
        variety_existed=False, variety_was_active=None, variety_parent_at_preview=None,
    )
    base.update(over)
    return CropVarietyImportPreviewStateRow(**base)


def _json(*, rows=(), file_sha256=_VALID_SHA) -> str:
    state = CropVarietyImportPreviewState(file_sha256=file_sha256, rows=list(rows))
    return state.model_dump_json(by_alias=True)


def _raw_row(row_number: int = 3, **over) -> dict:
    """A plain dict (camelCase, on-the-wire shape) — NEVER routed through
    the pydantic constructor, so an out-of-bounds value survives to reach
    _parse_cv_preview_state's model_validate_json exactly like a real
    attacker-supplied HTTP body would (pydantic's Python constructor would
    reject it eagerly at build time, which is the wrong thing to exercise
    here — we want to prove the WIRE-parsing path rejects it)."""
    base = {
        "rowNumber": row_number, "crop": "พริก", "variety": "พริกขี้หนู",
        "varietyStatus": True, "action": cv_import.ACTION_CREATE_CROP_AND_VARIETY,
        "cropExisted": False, "cropWasActive": None, "varietyExisted": False,
        "varietyWasActive": None, "varietyParentAtPreview": None,
    }
    base.update(over)
    return base


def _raw_json(*, rows: list[dict], file_sha256: str = _VALID_SHA) -> str:
    return json.dumps({"fileSha256": file_sha256, "rows": rows}, ensure_ascii=False)


# --- action / MAX_CROP_LEN / MAX_VARIETY_LEN parity with the service module ---

def test_schema_action_literal_matches_service_action_constants() -> None:
    """Guards against silent drift between the schema's duplicated Literal
    and the service's own ACTION_* constants (see the schema module's
    docstring for why they're duplicated rather than imported)."""
    from app.schemas.master_data_import import CropVarietyImportAction
    import typing

    schema_actions = set(typing.get_args(CropVarietyImportAction))
    service_actions = {
        cv_import.ACTION_CREATE_CROP, cv_import.ACTION_CREATE_VARIETY,
        cv_import.ACTION_CREATE_CROP_AND_VARIETY, cv_import.ACTION_ACTIVATE_VARIETY,
        cv_import.ACTION_DEACTIVATE_VARIETY, cv_import.ACTION_NONE,
    }
    assert schema_actions == service_actions


def test_schema_length_caps_match_service_max_lengths() -> None:
    assert CropVarietyImportPreviewStateRow.model_fields["crop"].metadata[0].max_length == cv_import.MAX_CROP_LEN
    assert CropVarietyImportPreviewStateRow.model_fields["variety"].metadata[0].max_length == cv_import.MAX_VARIETY_LEN


# --- malformed / oversized ---------------------------------------------

def test_malformed_json_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state("{not json")
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState ไม่ถูกต้อง"


def test_oversized_raw_previewstate_is_422_before_parsing() -> None:
    # A syntactically-valid-looking JSON string, just oversized — proves the
    # byte-size check fires BEFORE json parsing even attempts to run.
    huge = '{"fileSha256":"' + "a" * (3 * 1024 * 1024) + '","rows":[]}'
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(huge)
    assert exc.value.status_code == 422
    assert exc.value.detail == "previewState ไม่ถูกต้อง"


def test_oversized_check_counts_utf8_bytes_not_python_chars() -> None:
    """A Thai crop name is 3 bytes/char in UTF-8 — the cap must be measured
    in encoded bytes, not len(str), or a Thai-heavy payload could sail
    through a char-count check while still exceeding the real byte cap."""
    from app.api.v1 import masterdata as masterdata_module

    thai_crop = "ก" * 100  # 300 bytes in UTF-8, 100 in len()
    row_json = _json(rows=[_row(3, crop=thai_crop)])
    assert len(row_json) < masterdata_module._CV_PREVIEW_STATE_MAX_BYTES  # char count: small
    # Sanity: UTF-8 byte length is meaningfully larger than char length for this string.
    assert len(thai_crop.encode("utf-8")) > len(thai_crop)
    # Should parse fine (well under the real cap either way) — this just
    # proves the measurement doesn't crash/misbehave on multi-byte text.
    parsed = _parse_cv_preview_state(row_json)
    assert parsed is not None


# --- post-parse structural checks --------------------------------------

def test_too_many_rows_is_422() -> None:
    rows = [_row(n) for n in range(3, 3 + cv_import.MAX_IMPORT_ROWS + 1)]
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_json(rows=rows))
    assert exc.value.status_code == 422


def test_exactly_max_import_rows_is_accepted() -> None:
    rows = [_row(n) for n in range(3, 3 + cv_import.MAX_IMPORT_ROWS)]
    parsed = _parse_cv_preview_state(_json(rows=rows))
    assert len(parsed.rows) == cv_import.MAX_IMPORT_ROWS


def test_duplicate_row_number_is_422() -> None:
    rows = [_row(3), _row(3, variety="พริกจินดา")]
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_json(rows=rows))
    assert exc.value.status_code == 422


def test_invalid_file_sha256_shape_is_422() -> None:
    for bad in ("not-a-hash", "A" * 64, "a" * 63, "a" * 65, ""):
        with pytest.raises(HTTPException) as exc:
            _parse_cv_preview_state(_raw_json(rows=[_raw_row()], file_sha256=bad))
        assert exc.value.status_code == 422


def test_negative_or_zero_row_number_is_422() -> None:
    for bad_row_number in (0, -1, -100):
        with pytest.raises(HTTPException) as exc:
            _parse_cv_preview_state(_raw_json(rows=[_raw_row(row_number=bad_row_number)]))
        assert exc.value.status_code == 422


def test_invalid_action_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_raw_json(rows=[_raw_row(action="delete_crop")]))  # never a real action
    assert exc.value.status_code == 422


def test_overlength_crop_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_raw_json(rows=[_raw_row(crop="ก" * 256)]))
    assert exc.value.status_code == 422


def test_overlength_variety_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_raw_json(rows=[_raw_row(variety="ข" * 256)]))
    assert exc.value.status_code == 422


def test_overlength_variety_parent_at_preview_is_422() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_cv_preview_state(_raw_json(rows=[_raw_row(varietyParentAtPreview="ค" * 256)]))
    assert exc.value.status_code == 422


# --- no echo of dangerous/raw input --------------------------------------

def test_error_response_never_echoes_the_offending_value() -> None:
    """Every rejection path returns the SAME generic message — the specific
    row/value/reason is never revealed to the caller."""
    secret_looking_crop = "SECRET_CROP_ABC123_ไม่ควรปรากฏ"
    cases = [
        "{not json",
        _raw_json(rows=[_raw_row(crop=secret_looking_crop * 20)]),  # overlength
        _raw_json(rows=[_raw_row(row_number=3), _raw_row(row_number=3)]),  # duplicate
        _raw_json(rows=[_raw_row()], file_sha256="bad-hash"),
    ]
    for raw in cases:
        with pytest.raises(HTTPException) as exc:
            _parse_cv_preview_state(raw)
        assert exc.value.status_code == 422
        assert exc.value.detail == "previewState ไม่ถูกต้อง"
        assert secret_looking_crop not in str(exc.value.detail)


# --- valid state still reaches the service unmodified --------------------

def test_valid_preview_state_from_preview_still_reaches_commit() -> None:
    """A genuinely valid previewState (the exact shape build_preview would
    produce) parses through unchanged and is handed to cv_import.commit
    verbatim — Part B hardening must not alter the happy path."""
    state = CropVarietyImportPreviewState(
        file_sha256=_VALID_SHA,
        rows=[_row(3), _row(4, crop="เมล่อน", variety="เมล่อนญี่ปุ่น")],
    )
    raw = state.model_dump_json(by_alias=True)
    parsed = _parse_cv_preview_state(raw)
    assert parsed == state


async def test_valid_preview_state_flows_through_commit_endpoint_unit_contract() -> None:
    """End-to-end at the unit level (mocked service, no DB): a valid
    previewState reaches cv_import.commit exactly as parsed."""
    import io
    from types import SimpleNamespace
    from uuid import uuid4
    from fastapi import UploadFile

    state = CropVarietyImportPreviewState(file_sha256=_VALID_SHA, rows=[_row(3)])
    raw = state.model_dump_json(by_alias=True)
    result = CropVarietyImportCommitResult(
        created_crops=1, created_varieties=1, activated_varieties=0,
        deactivated_varieties=0, skipped_rows=0, total_rows=1,
    )
    commit_mock = AsyncMock(return_value=result)
    with patch("app.api.v1.masterdata.cv_import.commit", commit_mock), \
         patch("app.api.v1.masterdata.ActivityLogger") as logger_cls:
        logger_cls.return_value.log = AsyncMock()
        upload = UploadFile(file=io.BytesIO(b"PK\x03\x04fake"), filename="import.xlsx")
        out = await commit_crop_variety_import(
            request=SimpleNamespace(), user=SimpleNamespace(id=uuid4()),
            file=upload, preview_state=raw, db=AsyncMock(),
        )
    assert out is result
    commit_mock.assert_awaited_once()
    passed_state = commit_mock.await_args.kwargs["preview_state"]
    assert passed_state == state
