"""Plot + cycle Excel import schemas (round 7.5; rollover round 7.8; unified
start_next_cycle round 8-2.7.1).

Preview and commit share the same per-row result shape. The importer supports
five actions — create_plot_with_cycle / start_new_cycle / update_current_cycle /
close_and_start_new_cycle / start_next_cycle (see services/plot_import.py).
start_next_cycle resolves at validation/commit time to whichever of start_new_
cycle or close_and_start_new_cycle the plot's current state calls for; only a
resolved rollover (an explicit close_and_start_new_cycle row, or a start_next_
cycle row that resolves to one) closes a cycle (harvested, history preserved).
None deactivate a plot, regenerate a QR key, or create a record. Preview is
read-only; commit is all-or-nothing.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelBaseModel


class PlotImportRowPayload(CamelBaseModel):
    """The row's parsed + normalized values, echoed back for the preview table
    (camelCase for the frontend). Populated even for error rows so the user can
    see how each cell was interpreted."""

    action: str | None = None
    supplier_code: str | None = None
    plot_code: str | None = None
    plot_name: str | None = None
    # Access-phone columns (round 8-3E) — canonical Thai-mobile digits echoed
    # back for the preview table; both null/empty means the row's phone
    # columns were left blank (preserve for an existing plot, none for a new
    # one). Never populated from anywhere but this row's own parsed input.
    primary_phone: str | None = None
    additional_phones: list[str] = Field(default_factory=list)
    village: str | None = None
    district: str | None = None
    province: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    rai: Decimal | None = None
    crop: str | None = None
    variety: str | None = None
    cycle_label: str | None = None
    # PO / P.Code (round 8-5B) — normalized (PO upper-cased) echo of the row's
    # input. Defaulted so older JSON clients are unaffected.
    po_number: str | None = None
    p_code: str | None = None
    lot_no: str | None = None
    # Round 8-12A — the SUPPLIER's own lot number, echoed back exactly as
    # parsed (trimmed, blank→None). Independent of lot_no: it never affects the
    # Auto Lot formula, the running number, or the Manual/Auto decision.
    # Defaulted so a pre-8-12A workbook (no such column) and any older JSON
    # client are both unaffected.
    supplier_lot_no: str | None = None
    # Round 8-21A — three independent, OPTIONAL back-office reference fields,
    # echoed back exactly as parsed (trimmed, blank→None). Defaulted so a
    # pre-8-21A workbook (no such columns) and any older JSON client are both
    # unaffected.
    oracle_supplier_code: str | None = None
    oracle_invoice: str | None = None
    ref_account: str | None = None
    planting_date: date | None = None
    plant_count: int | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None
    # Actual harvest — final_plot only (round 8-7A). harvest_yield/final_
    # yield_after_clean/final_yield_unit/harvest_date are the REAL figures
    # (distinct from expected_yield_full above, a planning estimate);
    # final_note is optional; final_inspection_record_id is the row's OWN
    # input (echoed as parsed — the server-RESOLVED id, when the row left
    # this blank, is not part of the payload echo, only of preview_state).
    # Round 8-10B.1 — final_yield_unit is null for every action but
    # final_plot; it is never a fabricated constant on an unrelated row.
    harvest_yield: Decimal | None = None
    final_yield_after_clean: Decimal | None = None
    final_yield_unit: str | None = None
    harvest_date: date | None = None
    final_note: str | None = None
    final_inspection_record_id: UUID | None = None
    # Round 8-9B.1 — there is deliberately NO newInspectionPassword field here.
    # Every other column is echoed back so the user can see how it was parsed;
    # the password is the one thing that must never travel back out of the
    # server. What the row will DO is carried by PlotImportRowResult's
    # inspection_password_change instead ("set"/"replace"/null), which says
    # nothing about the value — not even its length.


class PlotImportRowResult(CamelBaseModel):
    row_number: int
    action: str | None = None
    supplier_code: str | None = None
    plot_code: str | None = None
    status: str  # "valid" | "error"
    message: str = ""
    payload: PlotImportRowPayload | None = None
    existing_plot_id: UUID | None = None
    active_cycle_id: UUID | None = None
    # Additive machine-readable fields (round 8-2.4). Both optional/defaulted so
    # existing JSON clients are unaffected; status stays "valid"/"error".
    #   error_code    — structured reason for an error row (e.g.
    #                   "duplicate_rollover"); lets clients/report classify
    #                   without parsing the Thai message. None for valid rows.
    #   result_cycle_no — cycle_no created/edited on a successful commit; None
    #                   for preview and errored rows.
    error_code: str | None = None
    result_cycle_no: int | None = None
    # start_next_cycle only (round 8-2.7.1) — additive, read-only server
    # result. The client never sends this and can never force it; the backend
    # recomputes it fresh at commit time under the plot's row lock instead of
    # trusting whatever a preview call returned.
    #   resolved_action     — "start_new_cycle" or "close_and_start_new_cycle",
    #                         whichever the plot's state resolves this row to.
    #                         None for every other action, and for a start_
    #                         next_cycle row that errors before resolving.
    #   current_cycle_no/current_cycle_label — identify the ACTIVE cycle a
    #                         resolved rollover would close (from data already
    #                         loaded validating the row — no extra query).
    resolved_action: str | None = None
    current_cycle_no: int | None = None
    current_cycle_label: str | None = None
    # Round 8-5B lot fields. On PREVIEW (read-only):
    #   lot_mode        — 'auto' | 'manual' | 'preserve' (what the row will do).
    #   proposed_lot_no — Auto shows "{cycleLabel}-{supplierCode}-{pCode}-###"
    #                     (round 8-12A; the running number is authoritative
    #                     only server-side); Manual/preserve show the concrete
    #                     value.
    # On COMMIT (result), the REAL values the backend produced:
    #   result_lot_no / result_lot_no_source / result_lot_running_no.
    # All optional/defaulted so older JSON clients are unaffected.
    lot_mode: str | None = None
    proposed_lot_no: str | None = None
    result_lot_no: str | None = None
    result_lot_no_source: str | None = None
    result_lot_running_no: int | None = None
    # final_plot only (round 8-7A) — non-blocking: a row can be status="valid"
    # with a warning set (e.g. finalYieldAfterClean > harvestYield). Never
    # set for any other action. Distinct from `message`/error_code, which are
    # always BLOCKING (status="error") — this never is.
    warning: str | None = None
    # Round 8-10B — final_plot only, purely INFORMATIONAL: which inspection
    # record the server resolved for this row, in words. Deliberately NOT
    # `warning` (that means "something may be wrong"; "a record was found" is
    # the normal, healthy case) and deliberately not the record's id — the id
    # is server-owned now and has no reason to travel to a client. Optional, so
    # a client that does not know about it is unaffected.
    final_record_note: str | None = None
    # Round 8-9B.1 — plot inspection password, SAFE metadata only:
    #   inspection_password_configured — does the plot have an active password
    #     right now (before this import runs)? False for a brand-new plot.
    #   inspection_password_change — "set" (none yet → this row creates one),
    #     "replace" (one exists → this row replaces it), or null (the
    #     newInspectionPassword cell was blank → keep the existing password).
    # Neither field reveals the password, its length, or its last digits, and
    # there is no hash/digest/version here.
    inspection_password_configured: bool | None = None
    inspection_password_change: str | None = None


class PlotImportCredentialPreviewStateRow(CamelBaseModel):
    """One row that will SET or REPLACE a plot inspection password, as the user
    saw it in Preview (round 8-9B.1) — the same optimistic-concurrency role
    PlotImportPreviewStateRow plays for start_next_cycle.

    Commit re-reads each plot's live credential under the Plot row lock and
    compares: if someone else set/changed/removed the password between Preview
    and Commit, the user approved a state that no longer exists, so the WHOLE
    file is rejected before anything is written. expected_configured +
    expected_credential_version together detect all three drifts (appeared,
    disappeared, changed).

    Carries NO password, hash or digest — only the version number, which is
    bookkeeping and reveals nothing about the secret."""

    row_number: int
    supplier_code: str
    plot_code: str
    plot_id: UUID | None = None
    expected_configured: bool
    expected_credential_version: int | None = None
    intended_change: str  # "set" | "replace"


class PlotImportPreviewStateRow(CamelBaseModel):
    """One start_next_cycle row's resolution as the user saw it in Preview
    (round 8-2.7.2). The commit re-computes the actual resolution fresh under
    the plot's row lock and compares against this — if a row would now resolve
    differently (an active cycle appeared/disappeared/changed since Preview),
    the whole file is rejected before any mutation, so the user never rolls
    over a cycle they weren't shown.

    active_cycle_id is the AUTHORITATIVE identity of the cycle a resolved
    rollover would close — never cycle_label (editable). null ⟺ resolved to
    start_new_cycle (no active cycle at Preview time)."""

    row_number: int
    supplier_code: str
    plot_code: str
    resolved_action: str  # "start_new_cycle" | "close_and_start_new_cycle"
    active_cycle_id: UUID | None = None


class PlotImportFinalPlotPreviewStateRow(CamelBaseModel):
    """One final_plot row's binding as the user saw it in Preview (round
    8-7A) — mirrors PlotImportPreviewStateRow's role for start_next_cycle,
    but final_plot needs a wider binding since it closes a SPECIFIC cycle
    and (optionally) snapshots a SPECIFIC inspection record: any of these
    changing between Preview and Commit means the user approved a state that
    no longer exists, and the whole file must be rejected before anything
    is written.

    plot_updated_at / active_cycle_updated_at are plain optimistic-
    concurrency stamps (Plot.updated_at / PlotCycle.updated_at) — cheap,
    generic "has ANYTHING about this row changed" guards, on top of the
    more specific active_cycle_id/cycle_label/resolved_final_inspection_
    record_id checks below. resolved_final_inspection_record_id is the id
    ACTUALLY used for this row (the row's own explicit finalInspectionRecordId
    if it gave one, else the server's own "latest active record" pick) —
    never the raw column value alone, since a blank column resolves to
    whatever the server picks."""

    row_number: int
    supplier_code: str
    plot_code: str
    plot_updated_at: datetime
    active_cycle_id: UUID
    active_cycle_no: int
    active_cycle_updated_at: datetime
    cycle_label: str | None = None
    resolved_final_inspection_record_id: UUID | None = None


class PlotImportPreviewState(CamelBaseModel):
    """Read-only optimistic-concurrency expectation the client echoes back on
    commit (round 8-2.7.2; final_plot binding added round 8-7A). NOT an
    authorization credential and NOT authority: the backend always
    recomputes the real state under lock and only uses this to detect
    divergence. No signing/token — the caller still passes plots.update +
    RLS as before.

    file_sha256 binds the expectation to the exact file bytes Previewed;
    start_next_rows binds each start_next_cycle row to the resolution shown;
    final_plot_rows (round 8-7A) binds each final_plot row to the plot/cycle/
    record state shown. Defaulted to an empty list so a pre-8-7A caller/test
    that only sets start_next_rows is unaffected."""

    file_sha256: str
    start_next_rows: list[PlotImportPreviewStateRow]
    final_plot_rows: list[PlotImportFinalPlotPreviewStateRow] = Field(default_factory=list)
    # Round 8-9B.1 — one entry per row that will set/replace a plot inspection
    # password. Defaulted to an empty list so a pre-8-9B.1 caller/test is
    # unaffected. Contains no secret of any kind (see the row model).
    credential_rows: list[PlotImportCredentialPreviewStateRow] = Field(default_factory=list)


class PlotImportPreview(CamelBaseModel):
    total_rows: int
    valid_rows: int
    error_rows: int
    rows: list[PlotImportRowResult]
    # Present only on the read-only preview endpoints (round 8-2.7.2) — the
    # client stores it in memory and echoes it back on commit. None on the
    # error-preview embedded in a commit's ImportHasErrors detail.
    preview_state: PlotImportPreviewState | None = None


class PlotImportCommitResult(CamelBaseModel):
    created_plots: int
    started_cycles: int
    updated_cycles: int
    rolled_over_cycles: int = 0
    # Round 8-6H — count of reactivate_plot_with_cycle rows executed.
    # Additive/defaulted so an older client that doesn't know this field
    # yet is unaffected.
    reactivated_plots: int = 0
    # Round 8-7A — count of final_plot rows executed (the cycle they closed
    # stays harvested; the plot stays is_active=true). Additive/defaulted so
    # an older client that doesn't know this field yet is unaffected.
    finalized_plots: int = 0
    skipped_rows: int
    row_results: list[PlotImportRowResult]
