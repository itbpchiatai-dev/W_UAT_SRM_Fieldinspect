"""Master Data crop/variety Excel import schemas (round 8-15A; previewState
input-boundary hardening round 8-15A.1).

Single sheet ("พืชและพันธุ์"), 4 columns (crop / variety / pCode /
varietyStatus — pCode added in round 8-26B).
Preview is read-only; commit is all-or-nothing. See
services/master_data_crop_variety_import.py's module docstring for the full
business-rule contract.

Round 8-15A.1 — CropVarietyImportPreviewStateRow/CropVarietyImportPreviewState
carry the FIELD-LEVEL bounds a hostile or corrupted previewState must satisfy
before it ever reaches the service layer (app/api/v1/masterdata.py's
`_parse_cv_preview_state` handles the bounds a plain Field can't express —
raw byte size, row-list length against MAX_IMPORT_ROWS, duplicate
row_number). The numeric/pattern literals here (255, 3, the 64-hex-char
sha256 shape, the action set) intentionally DUPLICATE — never import —
services/master_data_crop_variety_import.py's MAX_CROP_LEN/MAX_VARIETY_LEN/
ACTION_* constants, to keep this schema module dependency-free (services
import schemas, never the reverse — importing the constants back would be a
circular import). tests/unit/test_master_data_crop_variety_import_service.py
asserts the two stay in sync, so drift is caught by CI, not silently missed.
previewState is NEVER a credential or authorization grant — every request
still needs masterdata.create + masterdata.update, and the service always
re-derives the real plan from a fresh parse + fresh DB query; a passing
previewState only means "this looks like something a real Preview call could
have produced," not "this is trusted."
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelBaseModel

# Mirrors services/master_data_crop_variety_import.py's ACTION_* constants —
# duplicated as literals (not imported) to avoid a schema→service circular
# import. Kept in sync by test_master_data_crop_variety_import_service.py.
CropVarietyImportAction = Literal[
    "create_crop", "create_variety", "create_crop_and_variety",
    "activate_variety", "deactivate_variety", "none",
]

# Round 8-26B — the P.Code plan is a SEPARATE per-row action from the
# crop/variety one above (a row can create a crop, a variety AND a P.Code at
# once), so it gets its own closed vocabulary rather than expanding that one
# combinatorially. "none" is shared by both — a row where nothing changes.
CropVarietyImportPCodeAction = Literal["create_p_code", "activate_p_code", "none"]


class CropVarietyImportRowResult(CamelBaseModel):
    """One row's parsed values + validation outcome, for the preview table.
    Populated even for an error row so the user can see how the cell was
    interpreted. `variety_status` echoes the RAW cell text (e.g.
    "เปิดใช้งาน"/"ปิดใช้งาน"/None) — never a boolean — so a blank cell and an
    explicit value are visually distinguishable in the preview."""

    row_number: int
    crop: str | None = None
    variety: str | None = None
    p_code: str | None = None
    variety_status: str | None = None
    row_status: str  # "READY" | "SKIPPED" | "ERROR"
    action: str
    p_code_action: str = "none"
    error_message: str = ""


class CropVarietyImportSummary(CamelBaseModel):
    total_rows: int
    ready_rows: int
    skipped_rows: int
    error_rows: int
    crops_to_create: int
    varieties_to_create: int
    varieties_to_activate: int
    varieties_to_deactivate: int
    p_codes_to_create: int = 0
    p_codes_to_activate: int = 0


class CropVarietyImportPreviewStateRow(CamelBaseModel):
    """One row's normalized plan AND the live master_data state it was
    resolved against, as the user saw it in Preview — the optimistic-
    concurrency binding compared again at Commit under a fresh re-parse +
    re-query. Carries no id/uuid — crop/variety are matched by their
    (type, value) pair, the same key the DB's own unique index uses."""

    # Just "positive" — NOT a >=3 floor. Data rows normally start at row 3
    # (row 1 = header, row 2 = the fixed Thai description row), but a user
    # can leave row 2's `crop` cell blank while typing something in
    # `variety`/`varietyStatus` — that row then reads as a genuine (if
    # accidental) data row at row_number=2 with a "crop required" error, and
    # rejecting THAT at the schema level would crash a legitimate Preview/
    # Commit instead of showing the ordinary per-row error it should. The
    # real "matches a real row of THIS file" guarantee is enforced downstream
    # by the commit-time re-parse cross-check (_row_states_match_preview in
    # the service) — a row_number that doesn't correspond to any row the
    # fresh parse actually produced fails as a state conflict regardless of
    # what value it holds here.
    row_number: int = Field(..., gt=0)
    # max_length=255 mirrors master_data.value's own column cap (MAX_CROP_LEN/
    # MAX_VARIETY_LEN in the service module) — see the module docstring.
    crop: str = Field(..., max_length=255)
    variety: str | None = Field(None, max_length=255)
    # The normalized TARGET active flag for this row's variety (None for a
    # crop-only row) — not the raw Thai text (that's in the row result only).
    variety_status: bool | None = None
    action: CropVarietyImportAction
    crop_existed: bool
    crop_was_active: bool | None = None
    variety_existed: bool = False
    variety_was_active: bool | None = None
    variety_parent_at_preview: str | None = Field(None, max_length=255)
    # Round 8-26B — the P.Code half of the same optimistic-concurrency
    # binding. `variety_active_p_code_at_preview` is the variety's active
    # P.Code as of Preview WHATEVER it is, recorded even when the file never
    # mentions it: that is what turns "another admin gave this variety a
    # P.Code in the meantime" into a clean 409 instead of only a row error.
    p_code: str | None = Field(None, max_length=255)
    p_code_action: CropVarietyImportPCodeAction = "none"
    p_code_existed: bool = False
    p_code_was_active: bool | None = None
    p_code_parent_at_preview: str | None = Field(None, max_length=255)
    variety_active_p_code_at_preview: str | None = Field(None, max_length=255)


class CropVarietyImportPreviewState(CamelBaseModel):
    """Read-only optimistic-concurrency expectation the client echoes back on
    commit. `file_sha256` binds the expectation to the exact file bytes
    Previewed; `rows` binds each row's plan AND the master_data state it was
    computed against. ANY drift — a different file, a different row's cell
    values, or master_data changing underneath (another admin edited a crop/
    variety between Preview and Commit) — fails the whole commit with a
    single 409 before anything is written. Not a credential — the caller
    still needs masterdata.create + masterdata.update regardless.

    `rows`' own LENGTH cap (against MAX_IMPORT_ROWS) and a duplicate-
    row_number check both need the raw MAX_IMPORT_ROWS constant / a list-wide
    scan — checked in app/api/v1/masterdata.py's `_parse_cv_preview_state`
    right after this model validates, not here (see that function's
    docstring)."""

    # 64 lowercase hex chars — hashlib.sha256(...).hexdigest()'s exact shape;
    # never uppercase, never any other length.
    file_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    rows: list[CropVarietyImportPreviewStateRow] = Field(default_factory=list)


class CropVarietyImportPreview(CamelBaseModel):
    summary: CropVarietyImportSummary
    rows: list[CropVarietyImportRowResult]
    # Present only on the read-only preview endpoint — the client stores it
    # and echoes it back on commit. None on the error-preview embedded in a
    # commit's "has errors" 422 response.
    preview_state: CropVarietyImportPreviewState | None = None


class CropVarietyImportCommitResult(CamelBaseModel):
    created_crops: int
    created_varieties: int
    activated_varieties: int
    deactivated_varieties: int
    created_p_codes: int = 0
    activated_p_codes: int = 0
    skipped_rows: int
    total_rows: int
