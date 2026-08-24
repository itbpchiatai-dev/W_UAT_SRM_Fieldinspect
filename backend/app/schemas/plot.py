"""Plot request/response schemas."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, SecretStr, SkipValidation, field_validator, model_validator

from app.core.phone import normalize_thai_mobile
from app.schemas.base import CamelBaseModel
from app.services.cycle_reference_fields import normalize_cycle_reference_text
from app.services.lot_number import (
    normalize_p_code,
    normalize_po_number,
    normalize_supplier_lot_no,
)


class AssignedUserSummary(CamelBaseModel):
    user_id: UUID
    email: str
    full_name: str
    assigned_at: datetime


class PlotAccessPhoneConfig(CamelBaseModel):
    """Full declaration of a plot's ACTIVE access phones (round 8-3A; business
    rule tightened round 8-3C) — the PUT /plots/{plotId}/access-phones body, and
    the optional accessPhones on POST /plots/with-cycle.

    At most one primary + up to 10 additional. Every number is normalized to
    canonical Thai-mobile form at this boundary (invalid → 422). Duplicates and
    a primary repeated in additional are REJECTED, never silently deduped, so
    the user sees their own data-entry mistake rather than a quietly-dropped
    number. A blank primaryPhone means "no primary" (→ null); a blank additional
    entry is a mistake and rejected.

    Round 8-3C: a plot MAY have an entirely empty config (no primary, no
    additional — the "not yet set up" state), but it may NOT have additional
    numbers with no primary — if the plot has any phone at all, one of them
    must be designated primary. additionalPhones alone (primaryPhone null/blank)
    is rejected, not silently promoted or ignored.

    Round 8-17C — normalization/validation is NOT a model_validator here
    (that was the original round 8-3A/8-3C shape). A model_validator raising
    ValueError is caught by FastAPI's automatic RequestValidationError
    handler, which echoes the rejected input verbatim in each error's
    `input` key — a phone-number PII leak into the HTTP 422 response, the
    same class of bug PlotPhoneSearchRequest was fixed for in round
    8-17A.2.1/8-17B (see that schema's docstring below). See
    normalize_and_validate_phone_config() — every endpoint that accepts this
    schema calls it by hand, converting a ValueError into a plain-string
    HTTPException(422, detail=...) that never echoes the raw phone. Business
    rules/messages are UNCHANGED from the original validator — only the
    delivery mechanism moved.

    Round 8-17C.1 — round 8-17C's fix still left BOTH fields as ordinary
    `str`/`list[str]` types, so a wrong-typed payload (primaryPhone as an
    int/object, additionalPhones as a non-list, or a non-string item inside
    it) was still rejected by Pydantic itself — same PII-echo class of bug,
    since the length cap (`max_length=10`) and the item/type checks are
    themselves Pydantic-level rejections whose auto-422 echoes the full
    submitted list (every phone in it) in `input`. Both fields are now
    `SkipValidation[...]` — Pydantic performs ZERO validation/coercion on
    them (any JSON type passes straight through unchanged) while still
    reporting the ORIGINAL declared type (`string`/`array of string`) in the
    generated OpenAPI schema. Every shape check that used to be `Field`
    constraints (`max_length=10`) or implicit type coercion is now done by
    hand inside normalize_and_validate_phone_config(), first, before any of
    the original business-rule checks.
    """

    primary_phone: SkipValidation[str | None] = None
    additional_phones: SkipValidation[list[str]] = Field(default_factory=list)


_MAX_ADDITIONAL_PHONES = 10


def normalize_and_validate_phone_config(config: PlotAccessPhoneConfig) -> PlotAccessPhoneConfig:
    """Normalize + validate a PlotAccessPhoneConfig in place (round 8-17C —
    moved out of a Pydantic model_validator; see that class's docstring for
    why). Every endpoint that accepts this schema (PUT
    /plots/{plotId}/access-phones, POST /plots/with-cycle's optional
    accessPhones) MUST call this immediately after receiving the payload,
    before any DB work — mirrors the original validator's fail-fast
    ordering. Raises ValueError (never echoes the raw phone in its message)
    on any violation; the caller converts that to HTTPException(422,
    detail=str(exc)).

    Round 8-17C.1 — both fields are SkipValidation now (see the schema's
    docstring), so shape/type is entirely unverified on entry: check that
    FIRST, before any of the original 8-3A/8-3C business rules below, since
    a str method call (.strip()) or duplicate-check on a non-str/non-list
    value would otherwise raise a raw, unhandled TypeError (a 500) instead
    of a clean 422.
    """
    if config.primary_phone is not None and not isinstance(config.primary_phone, str):
        raise ValueError("primaryPhone must be a string or null")
    if not isinstance(config.additional_phones, list):
        raise ValueError("additionalPhones must be a list")
    if len(config.additional_phones) > _MAX_ADDITIONAL_PHONES:
        raise ValueError(f"additionalPhones must not contain more than {_MAX_ADDITIONAL_PHONES} numbers")
    for item in config.additional_phones:
        if not isinstance(item, str):
            raise ValueError("additionalPhones items must be strings")

    # primaryPhone: blank → None; otherwise canonicalize (raises → 422).
    if config.primary_phone is not None:
        raw = config.primary_phone.strip()
        config.primary_phone = normalize_thai_mobile(raw) if raw else None
    # additionalPhones: reject blank entries, canonicalize each.
    normalized: list[str] = []
    for item in config.additional_phones:
        candidate = (item or "").strip()
        if not candidate:
            raise ValueError("additionalPhones must not contain blank entries")
        normalized.append(normalize_thai_mobile(candidate))
    # Reject duplicates outright (do NOT silently dedupe).
    if len(set(normalized)) != len(normalized):
        raise ValueError("additionalPhones must not contain duplicate numbers")
    if config.primary_phone is not None and config.primary_phone in normalized:
        raise ValueError("primaryPhone must not also appear in additionalPhones")
    # Round 8-3C: additional numbers require a primary — a plot may be
    # entirely empty, but never "additional-only".
    if normalized and config.primary_phone is None:
        raise ValueError("primaryPhone is required when additionalPhones is set")
    config.additional_phones = normalized
    return config


class PlotPhoneSearchRequest(CamelBaseModel):
    """POST /plots/search-by-phone body (round 8-17A.2) — the phone travels
    ONLY in the body, never a GET query string: Uvicorn's access log records
    the full request line (method + path + query string) for every request,
    so a phone in `?q=...` would land in that log verbatim; a POST body never
    does.

    `phone` is `SkipValidation[str]` (round 8-17A.2.1 used bare `Any`; round
    8-17B tightens the OpenAPI/Swagger docs without reopening the echo hole).
    `SkipValidation` makes Pydantic report the declared type (`string`) in
    the generated JSON schema — so `/docs` and the OpenAPI spec correctly
    describe this field as a string — while performing ZERO validation on
    it at runtime: no `min_length`/`max_length`, no type coercion/rejection,
    nothing. Any Pydantic-level rejection (a `field_validator` raising
    ValueError, a length constraint, even a plain type mismatch like sending
    an int) is caught by FastAPI's automatic RequestValidationError handler,
    which echoes the rejected raw value back in each error's `input` key —
    the exact phone-number leak this endpoint must never produce (found in
    the 8-17A.2 review). So `phone` is accepted completely as-is at the
    schema layer regardless of type, and app.core.phone.normalize_thai_mobile
    is called explicitly inside the endpoint after a hand-written
    type/length check — every rejection path there raises a generic 422
    HTTPException with a fixed message, never the value itself (see
    PlotAccessPhoneConfig above — round 8-17C fixed the same class of bug
    there too, moving its validation out of a model_validator the same way).

    `limit`/`offset` carry ordinary Field bounds — they aren't PII, so a
    Pydantic auto-422 (with the out-of-range number echoed) is fine and
    expected for those.
    """

    model_config = ConfigDict(extra="forbid")

    phone: SkipValidation[str] = Field(...)
    supplier_id: UUID | None = None
    province: str | None = None
    crop: str | None = None
    variety: str | None = None
    plot_status: Literal["all", "active", "inactive"] = "all"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    # "รอบปลูกปัจจุบัน" filter — matches ONLY the plot's active PlotCycle.
    # Free text, not PII, so an ordinary `str | None` (no SkipValidation
    # needed) is fine here — a wrong type just 422s normally via Pydantic.
    cycle_label: str | None = None
    # Round 8-18B — "ชื่อแปลงหรือรหัสแปลง" free text, combined with `phone` as
    # an INTERSECTION (plot must match both). Ordinary `str | None` for the
    # same reason as cycle_label: it is plot identity, never PII, so a
    # Pydantic auto-422 echoing it is harmless — unlike `phone` above.
    q: str | None = None
    # Round 8-25K — "วันที่เริ่ม...ถึง", scoped to the plot's ACTIVE
    # PlotCycle.planting_date only (same scope as cycle_label above — see
    # plot_repository._apply_planting_date_filter's docstring). Plain `date`,
    # not PII, ordinary Pydantic auto-422 on a malformed value is fine.
    planting_date_from: date | None = None
    planting_date_to: date | None = None


class PlotAccessPhoneRead(CamelBaseModel):
    """One plot_access_phones row. `phone` is the stored canonical
    phone_normalized (built by the API layer — this is not model_validate'd
    straight off the ORM, whose attribute is phone_normalized)."""

    id: UUID
    phone: str
    access_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PlotAccessPhoneConfigResponse(CamelBaseModel):
    """GET/PUT /plots/{plotId}/access-phones response — the plot's ACTIVE phone
    config. primaryPhone/additionalPhones are the flat convenience view;
    `items` carries the full per-row detail (id/type/timestamps), primary-first
    then deterministic."""

    primary_phone: str | None = None
    additional_phones: list[str] = Field(default_factory=list)
    items: list[PlotAccessPhoneRead] = Field(default_factory=list)


class PlotInspectionCredentialStatus(CamelBaseModel):
    """GET/PUT /plots/{plotId}/inspection-access-credential response — round
    8-9A.

    STATUS ONLY. There is deliberately no password, passwordHash, lookupDigest,
    pepper, or passwordLastDigits field, and there is no endpoint anywhere that
    reveals an existing password: a lost password is replaced, never read back.
    `configured` is false (and the other two null) until the plot has an ACTIVE
    credential.
    """

    configured: bool
    credential_version: int | None = None
    updated_at: datetime | None = None


class PlotCredentialReadinessPlot(CamelBaseModel):
    """One eligible plot that still has NO inspection password (round 8-9C).

    Identity only — deliberately no phone, no credential id/version, no
    password/hash/digest, no qrKey. An operator needs to know WHICH plots to
    go configure, nothing more."""

    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str


class PlotCredentialReadiness(CamelBaseModel):
    """GET /plots/inspection-access-credentials/readiness — is it safe to turn
    PUBLIC_PLOT_PASSWORD_ENFORCEMENT on yet? (round 8-9C)

    ELIGIBLE means: the plot is active, its supplier is active, and it has at
    least one active inspection phone. An active planting cycle is deliberately
    NOT required — a plot between cycles still needs its password before
    enforcement flips, or it is locked out the moment its next cycle opens.

    `ready` is INFORMATION FOR AN OPERATOR, never an automatic trigger: nothing
    in this codebase enables the flag on its own.
    """

    eligible_plots: int
    configured_plots: int
    missing_credential_plots: int
    ready: bool
    missing_plots: list[PlotCredentialReadinessPlot] = Field(default_factory=list)


class PlotInspectionCredentialSet(CamelBaseModel):
    """PUT /plots/{plotId}/inspection-access-credential body — round 8-9A.

    SecretStr so the value never reaches a log, a repr, or a 422 body — a
    length violation reports `**********` as the input, not the code. max_length
    is only a COARSE payload boundary (round 8-9B.0: 64, comfortably above the
    20-digit policy ceiling but still small enough that a huge body is rejected
    before any work). The REAL policy — ASCII digits, 4 to 20 of them, with no
    guessability rule — runs in the endpoint via app.auth.plot_access_password
    and answers one generic message, so Pydantic never has to describe the rule
    (and so can never hint at the submitted value).
    """

    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(max_length=64)


class PlotCreate(CamelBaseModel):
    """Physical-plot fields ONLY (round 8.0.4 ownership lock).

    Round 17/17.1 originally let PlotCreate/PlotUpdate also carry the
    planting-cycle identity (current_crop/current_variety/current_lot_no/
    current_planting_date) and yield-planning base data (plant_count/
    expected_yield_full/expected_yield_unit), writing them straight onto the
    Plot mirror columns. That let this schema and PlotCycle's create/update
    both claim to own the same columns, which could disagree (e.g. editing a
    plot here without going through the active cycle). Ownership is now
    locked: PlotCycle (via plot_cycle_repository.create_cycle/update_cycle +
    sync_plot_mirror_from_cycle) is the ONLY writer of those mirror columns.
    This schema only ever touches the physical-plot columns below.

    extra="forbid" so a client that still sends a planning field (old
    frontend build, stale API doc, accidental copy-paste) gets a clean 422
    instead of it being silently dropped — see POST /plots/with-cycle for
    the atomic plot+initial-cycle create flow that replaces the old
    combined payload.
    """

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    plot_code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=255)
    village: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    province: str | None = Field(None, max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    rai: Decimal | None = Field(None, ge=0)


class PlotUpdate(CamelBaseModel):
    """Physical-plot fields ONLY — see PlotCreate's docstring for the
    round 8.0.4 ownership lock. Planting-cycle/yield-plan data is edited via
    PlotCycleUpdate (PATCH /plots/{plotId}/cycles/{cycleId}) instead.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    village: str | None = Field(None, max_length=255)
    district: str | None = Field(None, max_length=255)
    province: str | None = Field(None, max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    rai: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None


class PlotRead(CamelBaseModel):
    id: UUID
    supplier_id: UUID
    # Denormalised supplier display (round 6.1) — populated by the API layer
    # from the Plot.supplier relationship, read-only. Lets Plot Detail show
    # the supplier code/name and print QR without a separate suppliers fetch.
    # Never client-writable (absent from PlotCreate/PlotUpdate).
    supplier_code: str = ""
    supplier_name: str = ""
    plot_code: str
    name: str
    village: str | None
    district: str | None
    province: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    rai: Decimal | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    assigned_users: list[AssignedUserSummary] = []

    # Opaque QR locator (round 20) — visible to authenticated admins with
    # plots.read (same audience that already sees plot_code and can print
    # QR signs), never client-writable (absent from PlotCreate/PlotUpdate;
    # generated server-side, see plot_repository.create_plot). This is what
    # the frontend embeds in a plot's printed QR deep link instead of
    # supplierCode/plotCode.
    qr_key: str | None = None

    # Yield-planning base/master data (round 17) — see Plot model docstring.
    # A read-only mirror of the active PlotCycle (round 8.0.4 ownership
    # lock) — never client-writable via PlotCreate/PlotUpdate. "Current
    # expected yield" is deliberately not a field here; compute
    # expected_yield_full * current_yield_pct / 100 in the API/frontend.
    plant_count: int | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None

    # current_crop/current_variety/current_lot_no/current_planting_date are
    # plot MASTER data — a read-only mirror of the active PlotCycle (round
    # 8.0.4 ownership lock; PlotCreate/PlotUpdate no longer carry these),
    # never overwritten by inspection-record sync. Everything from
    # current_stage onward below is the true inspection-derived snapshot:
    # read-only, overwritten on every new record (round 12's
    # sync_current_status_from_record).
    current_crop: str | None = None
    current_variety: str | None = None
    current_lot_no: str | None = None
    current_planting_date: date | None = None
    current_stage: str | None = None
    current_yield_pct: Decimal | None = None
    current_field_prep_score: int | None = None
    current_weather_score: int | None = None
    current_care_score: int | None = None
    current_variety_resistance_score: int | None = None
    current_gps_lat: Decimal | None = None
    current_gps_lng: Decimal | None = None
    last_inspected_at: datetime | None = None
    last_inspected_by_code: str | None = None
    last_inspection_record_id: UUID | None = None

    # Active planting cycle read-model (round 7.3.1) — the plot's single
    # 'active' cycle, populated by the API layer from the Plot.active_cycle
    # relationship (all null when the plot has no active cycle). Lets the
    # frontend read the active-cycle truth directly instead of inferring it
    # from the current_* mirror above. Never client-writable. The current_*
    # mirror columns are deliberately kept (the active cycle keeps them in
    # sync) — these are the authoritative counterpart.
    active_cycle_id: UUID | None = None
    active_cycle_no: int | None = None
    active_cycle_status: str | None = None
    active_cycle_crop: str | None = None
    active_cycle_variety: str | None = None
    # User-facing season name of the active cycle (round 8.0), e.g. "jun2026".
    active_cycle_label: str | None = None
    active_cycle_lot_no: str | None = None
    # Active cycle PO / P.Code (round 8-5A) — denormalized read mirror of the
    # active cycle's po_number/p_code, alongside active_cycle_lot_no. Read-only
    # (the source of truth is the PlotCycle); NOT a plot mirror column (§9 keeps
    # PO/P.Code off the plots table). Lets the round-8-5B Plots list/detail show
    # PO/P.Code without a per-plot cycle fetch. None when no active cycle.
    active_cycle_po_number: str | None = None
    active_cycle_p_code: str | None = None
    # Round 8-12A — the active cycle's supplier lot number, mirrored here
    # alongside po_number/p_code so the plots list/detail can show it
    # without a second fetch. None when there is no active cycle or the
    # cycle has none.
    active_cycle_supplier_lot_no: str | None = None
    active_cycle_planting_date: date | None = None
    active_cycle_plant_count: int | None = None
    active_cycle_expected_yield_full: Decimal | None = None
    active_cycle_expected_yield_unit: str | None = None

    # Access phones (round 8-3A) — read-only, populated by the API layer from
    # Plot.access_phones (active rows, primary-first). primary_phone is the
    # plot's single active primary (or None); additional_phones lists the active
    # additional numbers. Never client-writable: phone access is a sub-resource
    # managed via /plots/{plotId}/access-phones, deliberately absent from
    # PlotCreate/PlotUpdate.
    primary_phone: str | None = None
    additional_phones: list[str] = []


class PlotSummary(CamelBaseModel):
    id: UUID
    supplier_id: UUID
    # Denormalised supplier display (round 6.1) — populated by the API layer
    # from Plot.supplier so the list shows supplier code+name and prints QR
    # without depending on the frontend's separate active-suppliers fetch
    # (which is capped and excludes inactive suppliers). Read-only.
    supplier_code: str = ""
    supplier_name: str = ""
    plot_code: str
    name: str
    village: str | None = None
    district: str | None = None
    province: str | None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    is_active: bool
    assigned_count: int = 0

    # Opaque QR locator (round 20) — see PlotRead.qr_key. Included in the
    # list response (not just single-plot PlotRead) so bulk QR printing
    # ("พิมพ์ QR รายการนี้" / "พิมพ์ QR ทั้ง Supplier") doesn't need an N+1
    # per-plot fetch; every viewer of this list already has plots.read,
    # which already gates the existing print buttons for these same plots.
    qr_key: str | None = None

    # Yield summary for the Plots list (round 17) — enough to render
    # "80% → 800 kg / 1,000 kg" per row without an extra per-plot fetch.
    current_yield_pct: Decimal | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None
    # plant_count included alongside (round 18) so the list can flag
    # "ยังไม่ตั้งแผนผลผลิต" without a per-plot fetch — see plant_count/
    # expected_yield_full's "master data" comment on PlotRead above.
    plant_count: int | None = None

    # Compact planting-cycle identity for the Plots list (round 18) — same
    # plot MASTER data as PlotRead's current_crop/current_variety/
    # current_lot_no/current_planting_date, never inspection-synced.
    current_crop: str | None = None
    current_variety: str | None = None
    current_lot_no: str | None = None
    current_planting_date: date | None = None

    # Latest-inspection context alongside current_yield_pct above — lets the
    # new-record form default its Yield % to the plot's last known value and
    # label where that default came from ("ตรวจล่าสุด <date> · ระยะ <stage>")
    # without a per-plot PlotRead fetch. Inspection-synced, read-only, same
    # audience that already sees these on PlotRead/Plot Detail.
    current_stage: str | None = None
    last_inspected_at: datetime | None = None

    # Active planting cycle read-model (round 7.3.1) — see PlotRead. Lets the
    # Plots list show "กำลังปลูก" vs "รอเริ่มรอบปลูก" and gate ตรวจแปลง from
    # the backend truth (activeCycleId != null) instead of inferring an active
    # cycle from the current_* mirror. One IN-query for the whole page (no
    # N+1); all null when the plot has no active cycle.
    active_cycle_id: UUID | None = None
    active_cycle_no: int | None = None
    active_cycle_status: str | None = None
    active_cycle_crop: str | None = None
    active_cycle_variety: str | None = None
    # User-facing season name of the active cycle (round 8.0), e.g. "jun2026".
    active_cycle_label: str | None = None
    active_cycle_lot_no: str | None = None
    # Active cycle PO / P.Code (round 8-5A) — see PlotRead. Denormalized read
    # mirror for the Plots list; None when no active cycle.
    active_cycle_po_number: str | None = None
    active_cycle_p_code: str | None = None
    # Round 8-12A — the active cycle's supplier lot number, mirrored here
    # alongside po_number/p_code so the plots list/detail can show it
    # without a second fetch. None when there is no active cycle or the
    # cycle has none.
    active_cycle_supplier_lot_no: str | None = None
    active_cycle_planting_date: date | None = None
    active_cycle_plant_count: int | None = None
    active_cycle_expected_yield_full: Decimal | None = None
    active_cycle_expected_yield_unit: str | None = None

    # Access phones (round 8-3A) — read-only, same primaryPhone/additionalPhones
    # as PlotRead, included on the list row so the Plots list can show a plot's
    # authorized numbers without a per-plot fetch (one IN-query for the page).
    primary_phone: str | None = None
    additional_phones: list[str] = []


class PlotCycleRead(CamelBaseModel):
    """One planting cycle (รอบปลูก) of a plot (round 7.2A). Read-only view
    behind GET /plots/{plotId}/cycles; lifecycle write schemas (create/close)
    come with the 7.2B endpoints."""

    id: UUID
    plot_id: UUID
    cycle_no: int
    status: str
    crop: str | None
    variety: str | None
    # User-facing season name, e.g. "jun2026" (round 8.0). NULL for cycles that
    # predate the field — the frontend falls back to "รอบที่ <cycle_no>".
    cycle_label: str | None
    lot_no: str | None
    # PO / Auto Lot metadata (round 8-5A). po_number/p_code are cycle-level
    # business identifiers; lot_no_source ('auto'|'manual'|'legacy'|None) and
    # lot_running_no are SERVER-derived — read-only here, deliberately absent
    # from PlotCycleCreate/Update. All None for cycles predating this round.
    po_number: str | None = None
    p_code: str | None = None
    lot_no_source: str | None = None
    lot_running_no: int | None = None
    # Round 8-12A — the SUPPLIER's own lot identifier for this cycle, stored
    # beside (never mixed into) lot_no above. Client-writable; takes no part in
    # the Auto Lot formula or the running number. None for every cycle
    # predating migration 0048 (no backfill).
    #
    # auto_lot_series_key is deliberately NOT exposed here: it is internal
    # server bookkeeping for the V2 running sequence, not business data.
    supplier_lot_no: str | None = None
    # Round 8-21A — three independent, OPTIONAL back-office reference fields.
    # Same "read-only mirror of a stored value" role as supplier_lot_no above:
    # client-writable via PlotCycleCreate/Update, no business logic of their
    # own, None for every cycle predating migration 0050 (no backfill).
    oracle_supplier_code: str | None = None
    oracle_invoice: str | None = None
    ref_account: str | None = None
    planting_date: date | None
    plant_count: int | None
    expected_yield_full: Decimal | None
    expected_yield_unit: str | None
    started_at: datetime
    closed_at: datetime | None
    closed_by_id: UUID | None
    close_reason: str | None
    # Final ESTIMATED-yield snapshot, frozen at close (round 8-2.8A). NOT
    # actual harvested yield. All None for cycles closed before migration 0038
    # and for a cycle closed without any inspection — defaulted so old records
    # and existing test fixtures serialize without error. Read-only: these are
    # deliberately absent from PlotCycleCreate/Update (never client-supplied).
    final_yield_pct: Decimal | None = None
    final_estimated_yield: Decimal | None = None
    final_inspection_record_id: UUID | None = None
    # Actual harvest — the REAL figures recorded when the cycle is finalized
    # (round 8-7A, Excel action final_plot; migration 0043). Distinct from
    # final_estimated_yield above: harvest_yield/final_yield_after_clean are
    # what was actually weighed, never an estimate. All None for a cycle
    # closed by any other path, and for every cycle predating migration 0043.
    # Read-only here — deliberately absent from PlotCycleCreate/Update.
    harvest_yield: Decimal | None = None
    final_yield_after_clean: Decimal | None = None
    final_yield_unit: str | None = None
    harvest_date: date | None = None
    final_note: str | None = None
    created_at: datetime
    updated_at: datetime


class PlotCycleCreate(CamelBaseModel):
    """POST /plots/{plotId}/cycles — start a new planting cycle (round 7.2B).

    Round 8-13A — poNumber is OPTIONAL: omitted, explicit null, and a blank/
    whitespace string all normalize to None (same rule PlotCycleUpdate already
    used); a nonblank value is still trimmed + upper-cased via
    normalize_po_number. PO was dropped from the Auto Lot V2 formula back in
    round 8-12A (see lot_number.py) — this round removes the last place PO was
    still gatekeeping cycle creation. pCode stays REQUIRED, nonblank, on every
    official "create a new cycle" flow (Start / Create-plot-with-cycle /
    Rollover's newCycle all use this schema) — trimmed, case preserved; a
    blank/omitted P.Code is still a 422. The migration-0042 columns stay
    nullable at the DB level (unchanged since round 8-5B) — this requiredness
    is an API contract for NEW cycles only, never a DB constraint change.
    status/cycle_no/started_at/closed_* and lot_no_source/lot_running_no are
    all server-derived, never client-supplied."""

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    # User-facing season name, e.g. "jun2026" (round 8.0) — see PlotCycleRead.
    # Round 8-17A.1 — REQUIRED (nonblank) on every path that uses this schema
    # (Start / Create-plot-with-cycle / Rollover's newCycle / Reactivate-with-
    # cycle — see _require_cycle_label below), independently of whether the
    # lot is Auto or Manual. It is the one human-readable identifier for a
    # cycle (PlotCycleRead falls back to "รอบที่ <cycle_no>" only for cycles
    # that predate this requirement) and feeds the Auto Lot formula, so a
    # blank one silently degrades both.
    cycle_label: str = Field(..., max_length=100)
    lot_no: str | None = Field(None, max_length=100)
    # PO (round 8-13A — OPTIONAL, see class docstring) / P.Code (round 8-5B —
    # still REQUIRED nonblank). PO is upper-cased + trimmed when given;
    # P.Code is trimmed (case kept). lot_no_source/lot_running_no are
    # SERVER-derived and intentionally NOT accepted here.
    po_number: str | None = Field(None, max_length=100)
    p_code: str = Field(..., max_length=100)
    # Round 8-12A — the SUPPLIER's own lot number for this cycle. OPTIONAL and
    # free-form (trimmed, blank -> None): unlike poNumber/pCode it never feeds
    # the Auto Lot formula, so requiring it would block a legitimate cycle for
    # data the system does not need.
    supplier_lot_no: str | None = Field(None, max_length=100)
    # Round 8-21A — three independent, OPTIONAL, free-text back-office
    # reference fields (trim, blank -> None; see
    # app/services/cycle_reference_fields.py). No business logic of their
    # own: none feeds the Auto Lot formula, the running number, or any
    # Manual/Auto decision. A rollover's new_cycle (this same schema) is
    # NEVER auto-filled from the closing cycle — see PlotCycleRollover.
    oracle_supplier_code: str | None = Field(None, max_length=255)
    oracle_invoice: str | None = Field(None, max_length=255)
    ref_account: str | None = Field(None, max_length=255)
    planting_date: date | None = None
    plant_count: int | None = Field(None, ge=0)
    expected_yield_full: Decimal | None = Field(None, ge=0)
    expected_yield_unit: str | None = Field(None, max_length=20)

    @field_validator("cycle_label")
    @classmethod
    def _require_cycle_label(cls, v: str) -> str:
        # Round 8-17A.1 — trim, then reject blank/whitespace/omitted the same
        # way _require_p_code already does below (mirrors that pattern
        # exactly). The Thai message is the exact string this round's
        # contract mandates so every "missing cycleLabel" surface (API,
        # Excel preview) reads identically to the user.
        trimmed = (v or "").strip()
        if not trimmed:
            raise ValueError(
                "กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ"
            )
        return trimmed

    @field_validator("po_number")
    @classmethod
    def _normalize_po_number(cls, v: str | None) -> str | None:
        # Round 8-13A — optional: None/blank/whitespace all normalize to
        # None (never raises); a nonblank value is still trimmed/upper-cased.
        return normalize_po_number(v)

    @field_validator("p_code")
    @classmethod
    def _require_p_code(cls, v: str) -> str:
        normalized = normalize_p_code(v)
        if not normalized:
            raise ValueError("pCode ต้องไม่ว่าง (จำเป็นสำหรับการเริ่มรอบปลูกใหม่)")
        return normalized

    @field_validator("supplier_lot_no")
    @classmethod
    def _normalize_supplier_lot_no(cls, v: str | None) -> str | None:
        return normalize_supplier_lot_no(v)

    @field_validator("oracle_supplier_code", "oracle_invoice", "ref_account")
    @classmethod
    def _normalize_reference_fields(cls, v: str | None) -> str | None:
        return normalize_cycle_reference_text(v)


class PlotCycleUpdate(CamelBaseModel):
    """PATCH /plots/{plotId}/cycles/{cycleId} — edit the ACTIVE cycle's plan
    (round 7.2B). Only the planting/plan fields are editable; status,
    cycle_no, started_at and the closed_* trio are deliberately absent so they
    can never be changed here (status transitions go through /close)."""

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    # User-facing season name, e.g. "jun2026" (round 8.0) — see PlotCycleRead.
    cycle_label: str | None = Field(None, max_length=100)
    lot_no: str | None = Field(None, max_length=100)
    # PO / P.Code (round 8-5A) — see PlotCycleCreate. lot_no_source/
    # lot_running_no are SERVER-derived, never accepted from the client.
    po_number: str | None = Field(None, max_length=100)
    p_code: str | None = Field(None, max_length=100)
    # Round 8-12A — supplier's own lot number. exclude_unset semantics apply
    # (see the repository's update_cycle): ABSENT keeps the stored value;
    # explicit null/blank clears it. Never regenerates the system lot.
    supplier_lot_no: str | None = Field(None, max_length=100)
    # Round 8-21A — three independent, OPTIONAL, free-text back-office
    # reference fields. Same exclude_unset semantics as supplier_lot_no
    # above: ABSENT keeps the stored value; explicit null/blank clears it;
    # nonblank text is trimmed and stored (app/services/
    # cycle_reference_fields.py).
    oracle_supplier_code: str | None = Field(None, max_length=255)
    oracle_invoice: str | None = Field(None, max_length=255)
    ref_account: str | None = Field(None, max_length=255)
    planting_date: date | None = None
    plant_count: int | None = Field(None, ge=0)
    expected_yield_full: Decimal | None = Field(None, ge=0)
    expected_yield_unit: str | None = Field(None, max_length=20)

    @field_validator("cycle_label")
    @classmethod
    def _trim_cycle_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("po_number")
    @classmethod
    def _normalize_po_number(cls, v: str | None) -> str | None:
        return normalize_po_number(v)

    @field_validator("p_code")
    @classmethod
    def _normalize_p_code(cls, v: str | None) -> str | None:
        return normalize_p_code(v)

    @field_validator("supplier_lot_no")
    @classmethod
    def _normalize_supplier_lot_no(cls, v: str | None) -> str | None:
        return normalize_supplier_lot_no(v)

    @field_validator("oracle_supplier_code", "oracle_invoice", "ref_account")
    @classmethod
    def _normalize_reference_fields(cls, v: str | None) -> str | None:
        return normalize_cycle_reference_text(v)


class PlotCycleClose(CamelBaseModel):
    """POST /plots/{plotId}/cycles/{cycleId}/close — close the active cycle
    (round 7.2B). status is constrained to the two terminal states — 'active'
    (or anything else) is a 422, so the close endpoint can never re-activate
    or mis-set a status."""

    status: Literal["harvested", "cancelled"]
    close_reason: str | None = None

    @field_validator("close_reason")
    @classmethod
    def _trim_close_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class PlotCycleRollover(CamelBaseModel):
    """POST /plots/{plotId}/cycles/{cycleId}/rollover — atomically close the
    active cycle and open a fresh one on the same plot (round 7.9B). The
    single-plot equivalent of the Excel close_and_start_new_cycle import action.

    close_status is constrained to the two terminal states (a rollover that
    left the cycle 'active' is meaningless → 422). The nested new_cycle reuses
    PlotCycleCreate; the expectedYieldUnit-required-with-expectedYieldFull rule
    (matching the import/UI validation) is enforced here since PlotCycleCreate
    itself doesn't carry it.
    """

    close_status: Literal["harvested", "cancelled"]
    close_reason: str | None = None
    new_cycle: PlotCycleCreate

    @field_validator("close_reason")
    @classmethod
    def _trim_close_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @model_validator(mode="after")
    def _unit_required_with_yield(self) -> "PlotCycleRollover":
        nc = self.new_cycle
        if nc.expected_yield_full is not None and not (
            nc.expected_yield_unit and nc.expected_yield_unit.strip()
        ):
            raise ValueError(
                "expectedYieldUnit is required when expectedYieldFull is set"
            )
        return self


class PlotCycleRolloverResult(CamelBaseModel):
    """Response for a rollover — carries BOTH the just-closed cycle and the new
    active cycle so the Plot Detail UI can update its history + current-cycle
    view without a re-fetch. active_cycle_id/no mirror the plot's new active
    cycle for convenience."""

    plot_id: UUID
    active_cycle_id: UUID
    active_cycle_no: int
    closed_cycle: PlotCycleRead
    new_cycle: PlotCycleRead


class PlotWithCycleCreate(CamelBaseModel):
    """POST /plots/with-cycle — atomically create a physical Plot AND its
    first active PlotCycle in one request/transaction (round 8.0.4).

    Replaces the old combined PlotCreate that carried both physical and
    planning fields: a client that only wants to reserve a plot with no
    cycle yet should keep using plain POST /plots (still physical-only);
    this endpoint is for the common "create a plot that's ready to inspect
    immediately" case, so the plot never sits in an unusable
    "รอเริ่มรอบปลูก" state right after creation.
    """

    model_config = ConfigDict(extra="forbid")

    plot: PlotCreate
    cycle: PlotCycleCreate
    # Optional access phones (round 8-3A) — when provided, the plot's initial
    # access-phone config is created in the SAME transaction as the plot + first
    # cycle (all-or-nothing). Omitted/null → behavior is exactly as before. Phone
    # access stays a sub-resource: it lives here on the wrapper, NOT on PlotCreate.
    access_phones: PlotAccessPhoneConfig | None = None


class PlotWithCycleCreateResult(CamelBaseModel):
    """Response for POST /plots/with-cycle — the created Plot (with its
    active_cycle_* read-model already populated) and the created PlotCycle,
    so the frontend never needs a second fetch to show either."""

    plot: PlotRead
    cycle: PlotCycleRead


class PlotAssignRequest(CamelBaseModel):
    user_ids: list[UUID]


class PlotLookupRead(CamelBaseModel):
    """QR-scan lookup result — supplier + plot resolved from a field-sign QR code."""

    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str


# Round 8-3G: InspectionCodeVerifyRequest/Result and
    # PublicInspectionCodeVerifyRequest/Response (the legacy plot/supplier
    # gate-code verify schemas) are retired along with the two endpoints
    # that used them (plots.py's verify_plot_inspection_code and
    # public_plots.py's verify_inspection_code_public). The public
    # inspection flow is phone-access-only (see app/schemas/phone_access.py);
    # the logged-in flow never needed a second gate beyond login+permission+
    # RLS.
