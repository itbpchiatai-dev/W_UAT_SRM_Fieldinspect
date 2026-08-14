"""Supplier Excel import schemas (round 8-20A).

Single sheet ("ข้อมูล Supplier"), 9 columns. Preview is read-only; commit is
all-or-nothing. See services/supplier_import.py's module docstring for the
full business-rule contract.

Shaped after schemas/master_data_import.py (round 8-15A/8-15A.1) — same
previewState-as-optimistic-concurrency design, same "the schema module stays
dependency-free" rule: the numeric/pattern literals here (50/255/20, the
64-hex sha256 shape, the operation set) intentionally DUPLICATE — never
import — services/supplier_import.py's MAX_*_LEN / OPERATION_* constants, so
services can keep importing schemas without a circular import.
tests/unit/test_supplier_import_service.py asserts the two stay in sync, so
drift is caught by CI rather than silently missed.

previewState is NEVER a credential or an authorization grant — every request
still needs its own permissions, and the service always re-derives the real
plan from a fresh parse + fresh DB query. A well-formed previewState only
means "this looks like something a real Preview call could have produced".
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelBaseModel

# Mirrors services/supplier_import.py's OPERATION_* constants — duplicated as
# literals (not imported) to avoid a schema→service circular import.
SupplierImportOperation = Literal["create", "update", "no_change"]


class SupplierImportRowResult(CamelBaseModel):
    """One row's parsed values + validation outcome, for the preview table.
    Populated even for an error row so the user can see how each cell was
    interpreted. Every value echoes the NORMALIZED (trimmed) text, never a
    UUID or any other internal identifier."""

    row_number: int
    action: str | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None
    tax_id: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    status: str | None = None
    row_status: str  # "READY" | "ERROR"
    operation: str
    error_message: str = ""
    # Round 8-20A — a non-blocking notice shown alongside a READY row (today:
    # only the "deactivating this Supplier keeps its plots/cycles/records"
    # reassurance). Never an error; never affects whether commit may run.
    warning_message: str = ""


class SupplierImportSummary(CamelBaseModel):
    total_rows: int
    ready_rows: int
    error_rows: int
    suppliers_to_create: int
    suppliers_to_update: int
    suppliers_to_activate: int
    suppliers_to_deactivate: int
    unchanged_rows: int


class SupplierImportPreviewStateRow(CamelBaseModel):
    """One row's normalized plan AND the live Supplier state it was resolved
    against, as the user saw it in Preview — the optimistic-concurrency
    binding re-compared at Commit under a fresh re-parse + re-query.

    Carries NO supplier UUID: a Supplier is matched by its `code`, the same
    identity the DB's own unique index uses (and the identity this importer
    forbids changing).

    `existing_state_digest` is a sha256 over the existing Supplier's material
    field values as they were at Preview time — a compact stand-in for
    echoing every field back. It is what makes a CONCURRENT edit (another
    admin changing that Supplier between Preview and Commit) fail as a state
    conflict. A digest, not the values, because `address` is an unbounded
    Text column and 1,000 rows of full field echoes would blow the
    previewState size cap. Null when the Supplier did not exist at Preview.
    """

    # Just "positive" — NOT a >=3 floor. Data rows normally start at row 3
    # (row 1 = header, row 2 = the fixed Thai description row), but a user can
    # leave row 2's action cell blank while typing in another column; that row
    # then reads as a genuine (if accidental) data row at row_number=2 with an
    # ordinary per-row error, and rejecting it at the schema level would crash
    # a legitimate Preview/Commit instead. The real "matches a real row of THIS
    # file" guarantee comes from the commit-time re-parse cross-check.
    row_number: int = Field(..., gt=0)
    # suppliers.code is varchar(50) (app/db/models/supplier.py).
    supplier_code: str = Field(..., max_length=50)
    operation: SupplierImportOperation
    supplier_existed: bool
    supplier_was_active: bool | None = None
    existing_state_digest: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


class SupplierImportPreviewState(CamelBaseModel):
    """Read-only optimistic-concurrency expectation the client echoes back on
    commit. `file_sha256` binds the expectation to the exact file bytes
    Previewed; `rows` binds each row's plan AND the Supplier state it was
    computed against. ANY drift — a different file, a different row set, or a
    Supplier changing underneath — fails the whole commit with one 409 before
    anything is written.

    `rows`' own LENGTH cap (against MAX_IMPORT_ROWS) and the duplicate-
    row_number check both need the service's constant / a list-wide scan, so
    they live in app/api/v1/suppliers.py's `_parse_preview_state` right after
    this model validates — same split as masterdata.py/plots.py.
    """

    # 64 lowercase hex chars — hashlib.sha256(...).hexdigest()'s exact shape.
    file_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    rows: list[SupplierImportPreviewStateRow] = Field(default_factory=list)


class SupplierImportPreview(CamelBaseModel):
    summary: SupplierImportSummary
    rows: list[SupplierImportRowResult]
    # Present only on the read-only preview endpoint — the client stores it
    # and echoes it back on commit. None on the error-preview embedded in a
    # commit's "has errors" 422 response.
    preview_state: SupplierImportPreviewState | None = None


class SupplierImportProcessedRow(CamelBaseModel):
    """One row's outcome in a committed result (round 8-20A Part E)."""

    row_number: int
    supplier_code: str | None = None
    status: str
    message: str = ""


class SupplierImportCommitResult(CamelBaseModel):
    total_rows: int
    created_suppliers: int
    updated_suppliers: int
    activated_suppliers: int
    deactivated_suppliers: int
    unchanged_rows: int
    error_rows: int
    processed_rows: list[SupplierImportProcessedRow] = Field(default_factory=list)
