"""Record request/response schemas (Step 12.5: yield/list-driven)."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.base import CamelBaseModel

# Round 8-8A.1 — the ONE shared boundary for a client-supplied kg quantity,
# used verbatim by both RecordCreate and PublicRecordCreate so the two create
# flows can never drift on what counts as valid input. Mirrors records.
# yield_quantity_kg's DB type (NUMERIC(12,2), migration 0044) exactly:
# max_digits=12/decimal_places=2 reject BOTH >2 decimal places (never
# silently rounded — a client typo like 123.456 is a 422, not a quiet 123.46)
# AND a value too large for that column (max 9,999,999,999.99) BEFORE it ever
# reaches yield_calculation.derive_yield or the DB, closing the "huge quantity
# + non-comparable target" numeric-overflow path (round 8-8A.1 bug #2).
YieldQuantityKg = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class RecordCreate(CamelBaseModel):
    plot_id: UUID
    supplier_id: UUID
    record_date: datetime.date

    # Field attribution — who filled the form on-site, not tied to login and
    # never used for auth. recorded_by_id (audit) comes from current_user.
    # submitted_by_code is retired (round 8-3G) — no client sends it anymore;
    # submitted_by_name (optional) is the sole remaining attribution input.
    submitted_by_name: str | None = Field(None, max_length=255)

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    planting_date: datetime.date | None = None

    # Yield % — 0-9999.9 (default 100; round 8-8B.1 widened the ceiling from
    # 150 to the column's own NUMERIC(5,1) storage capacity — 150 is now only
    # a non-blocking warning threshold the frontend shows, never enforced
    # here). Round 8-8A: when yield_quantity_kg is also sent, the Backend
    # OVERWRITES this with a server-derived value (see app/services/
    # yield_calculation.py) — this field only survives as-is for a legacy
    # client that omits yield_quantity_kg entirely.
    yield_pct: Decimal | None = Field(Decimal("100"), ge=0, le=Decimal("9999.9"))

    # Round 8-8A — kg-first Yield input. Optional so a legacy client that only
    # ever sends yield_pct is completely unaffected. NOT the DB target
    # snapshot (yield_target_kg_snapshot is server-derived only — never a
    # client field, see Record model). Bounded by YieldQuantityKg (round
    # 8-8A.1) — see its own comment above.
    yield_quantity_kg: YieldQuantityKg | None = None

    weather_condition: str | None = Field(None, max_length=255)
    field_prep_score: int | None = Field(None, ge=1, le=10)
    weather_score: int | None = Field(None, ge=1, le=10)
    care_score: int | None = Field(None, ge=1, le=10)
    variety_resistance_score: int | None = Field(None, ge=1, le=10)

    recommendation: str | None = None
    notes: str | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] = Field(default_factory=list, max_length=5)

    # Dynamic custom fields (Step 12) — keyed by FieldDefinition.key (slug).
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("submitted_by_name")
    @classmethod
    def _validate_submitted_by_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class RecordUpdate(CamelBaseModel):
    """Internal/deprecated (round 8.0.5 append-only lock) — no API route
    accepts this as a request body anymore (PATCH /records/{recordId} was
    removed). Kept only because record_repository.update_record and this
    schema's own validation rules are still covered by unit tests exercising
    the schema directly; do not wire this to a new endpoint. Deactivating a
    record uses the narrower record_repository.deactivate_record instead,
    which can only ever set is_active=False. submitted_by_code is retired
    (round 8-3G) — dropped here too, same as RecordCreate.
    """

    record_date: datetime.date | None = None

    submitted_by_name: str | None = Field(None, max_length=255)

    crop: str | None = Field(None, max_length=100)
    variety: str | None = Field(None, max_length=100)
    growth_stage: str | None = Field(None, max_length=100)
    planting_date: datetime.date | None = None

    # Round 8-8B.1 — same widened ceiling as RecordCreate (150 is a warning
    # threshold only now, not enforced at this schema level).
    yield_pct: Decimal | None = Field(None, ge=0, le=Decimal("9999.9"))

    weather_condition: str | None = Field(None, max_length=255)
    field_prep_score: int | None = Field(None, ge=1, le=10)
    weather_score: int | None = Field(None, ge=1, le=10)
    care_score: int | None = Field(None, ge=1, le=10)
    variety_resistance_score: int | None = Field(None, ge=1, le=10)

    recommendation: str | None = None
    notes: str | None = None
    is_active: bool | None = None

    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    photo_urls: list[str] | None = Field(None, max_length=5)
    custom_fields: dict[str, Any] | None = None

    @field_validator("submitted_by_name")
    @classmethod
    def _validate_submitted_by_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class RecordRead(CamelBaseModel):
    id: UUID
    plot_id: UUID
    # Planting cycle this record belongs to (round 7.1) — server-derived at
    # create time from the plot's active cycle, never client-supplied.
    # plot_cycle_id comes straight off the record; cycle_no is denormalised by
    # the API layer from the Record.plot_cycle relationship.
    plot_cycle_id: UUID | None = None
    cycle_no: int | None = None
    # Planting-cycle detail (round 7.4) — denormalised by the API layer from
    # the Record.plot_cycle relationship (bound at create time), NOT from the
    # plot's current mirror. A record from a closed/older cycle therefore shows
    # THAT cycle's crop/variety/plan even after the plot starts a newer active
    # cycle. All None if the record has no cycle (broken/absent relationship).
    cycle_status: str | None = None
    # User-facing season name of the record's OWN cycle (round 8.0.5), e.g.
    # "jun2026" — same field as PlotCycleRead.cycle_label, denormalised here
    # from Record.plot_cycle so a record keeps showing the label of the
    # cycle it was actually captured under, not the plot's CURRENT active
    # cycle's label. None for cycles that predate the field or have no
    # label set — the frontend falls back to "รอบที่ <cycle_no>".
    cycle_label: str | None = None
    cycle_crop: str | None = None
    cycle_variety: str | None = None
    cycle_lot_no: str | None = None
    cycle_planting_date: datetime.date | None = None
    cycle_plant_count: int | None = None
    cycle_expected_yield_full: Decimal | None = None
    cycle_expected_yield_unit: str | None = None
    supplier_id: UUID
    recorded_by_id: UUID
    recorded_by_email: str = ""
    recorded_by_name: str = ""
    # Retired (round 8-3G) — nullable. Historical records keep their code;
    # new records are always null (no create flow collects it anymore).
    submitted_by_code: str | None
    submitted_by_name: str | None
    # Client IP captured at creation (migration 0031) — server-resolved
    # audit field, read-only; deliberately absent from RecordCreate/
    # RecordUpdate/PublicRecordCreate so it can never be client-supplied.
    submitted_ip: str | None = None
    plot_code: str = ""
    plot_name: str = ""
    supplier_name: str = ""

    record_date: datetime.date
    crop: str | None
    variety: str | None
    growth_stage: str | None
    planting_date: datetime.date | None
    yield_pct: Decimal | None
    # Round 8-8A — kg-first Yield input + the server-derived comparison
    # target, both read-only here (yield_target_kg_snapshot is NEVER a
    # RecordCreate/PublicRecordCreate field — see app/services/
    # yield_calculation.py). Both None for a legacy record / legacy client.
    yield_quantity_kg: Decimal | None = None
    yield_target_kg_snapshot: Decimal | None = None

    weather_condition: str | None
    field_prep_score: int | None
    weather_score: int | None
    care_score: int | None
    variety_resistance_score: int | None

    recommendation: str | None
    notes: str | None

    latitude: Decimal | None
    longitude: Decimal | None
    photo_urls: list[str]

    custom_fields: dict[str, Any]
    is_active: bool

    # Phone-access attribution (round 8-3A) — all read-only and nullable. Bound
    # server-side (from the verified access phone) by the round 8-3B public flow;
    # NULL for existing records and for the logged-in flow. Deliberately NOT on
    # RecordCreate/PublicRecordCreate/RecordUpdate so a client can't forge them.
    plot_access_phone_id: UUID | None = None
    submitted_phone_snapshot: str | None = None
    submitted_phone_type: str | None = None
    inspector_type: str | None = None

    # When the form was actually filled on-site for an offline draft (round
    # 8-4A) — read-only, NULL for online records. Deliberately NOT ordering
    # input: plot.current_* snapshots still order by created_at, never this.
    captured_at: datetime.datetime | None = None

    created_at: datetime.datetime
    updated_at: datetime.datetime


class RecordSummary(CamelBaseModel):
    id: UUID
    plot_id: UUID
    # Planting cycle (round 7.1) — see RecordRead. plot_cycle_id is free (off
    # the record); cycle_no/cycle_label are populated by the API layer from
    # Record.plot_cycle (round 8.0.5 added cycle_label).
    plot_cycle_id: UUID | None = None
    cycle_no: int | None = None
    cycle_label: str | None = None
    supplier_id: UUID
    recorded_by_id: UUID
    # Retired (round 8-3G) — nullable, same as RecordRead.
    submitted_by_code: str | None
    submitted_by_name: str | None
    record_date: datetime.date
    crop: str | None
    variety: str | None
    growth_stage: str | None
    yield_pct: Decimal | None
    # Round 8-8C — same read-only, server-derived fields as RecordRead (see
    # its own comment above); sourced verbatim from the Record row by
    # model_validate(record) below, never recomputed from the active cycle.
    # Both None for a legacy record / legacy client, same as RecordRead.
    yield_quantity_kg: Decimal | None = None
    yield_target_kg_snapshot: Decimal | None = None
    field_prep_score: int | None
    weather_score: int | None
    care_score: int | None
    variety_resistance_score: int | None
    is_active: bool
    created_at: datetime.datetime
    # Offline capture time (round 8-4A) — read-only, NULL for online records.
    captured_at: datetime.datetime | None = None
    # Denormalised display fields (populated by API layer from relationships)
    plot_code: str = ""
    plot_name: str = ""
    supplier_name: str = ""


class PublicRecordCreate(CamelBaseModel):
    """POST /api/v1/public/records — unauthenticated, gated by
    inspection_session_token (minted by
    POST /api/v1/public/inspection-access/select-plot; round 8-3G retired
    the legacy inspection-code verify endpoint that used to mint this same
    token type — the public flow is phone-access-only now).

    plot_id/supplier_id are deliberately NOT fields here — they're derived
    server-side from the token, never from the client. extra="forbid" so a
    client sending plotId/supplierId (or anything else unrecognized) in
    the body gets a 422 rather than having it silently ignored — reject,
    not ignore, per the round 8 brief. submitted_by_code is retired (round
    8-3G) — dropped here too, same as RecordCreate; submitted_by_name
    (optional) is the sole remaining attribution input. inspectorType /
    phone attribution are never client fields either — both are derived
    server-side from the signed inspection_session_token.

    Field-level constraints (trim, max_length, score ranges, etc.) aren't
    duplicated from RecordCreate here — the endpoint re-validates through
    RecordCreate's own validators when it builds the internal payload with
    the token-derived plot_id/supplier_id, so there's exactly one place
    those rules live.
    """

    model_config = ConfigDict(extra="forbid")

    inspection_session_token: str = Field(..., min_length=1)

    record_date: datetime.date
    submitted_by_name: str | None = None

    # crop/variety/planting_date are deliberately NOT fields here (round
    # 20.2) — they're plot MASTER data, set only via Plot Create/Edit by an
    # admin, never by whoever is filling in an inspection. A client sending
    # them gets 422 (extra="forbid"), same "reject, not ignore" treatment as
    # plot_id/supplier_id above. The endpoint copies the verified plot's
    # current_crop/current_variety/current_planting_date onto the record as
    # a snapshot — see public_records.py's _finish_creating_record.
    growth_stage: str | None = None

    # Round 8-8A: same kg-first/server-overwrite contract as RecordCreate
    # (see app/services/yield_calculation.py) — yield_target_kg_snapshot is
    # NEVER a field here either, same "reject, not ignore" treatment as
    # plot_id/supplier_id above would give it if a client tried (extra=
    # "forbid" on this model). Bounded by the SAME YieldQuantityKg (round
    # 8-8A.1) as RecordCreate — one boundary, never duplicated/drifted.
    # Round 8-8B.1 — le=9999.9 (was unbounded here, unlike RecordCreate):
    # closes a latent gap where a legacy client sending only yieldPct (no
    # kg — the one branch derive_yield never re-validates) could overflow
    # the NUMERIC(5,1) column all the way to a raw Postgres error. 150 is
    # only a non-blocking warning threshold now (see yield_calculation.py's
    # YIELD_WARNING_PCT) — never enforced here.
    yield_pct: Decimal | None = Field(Decimal("100"), ge=0, le=Decimal("9999.9"))
    yield_quantity_kg: YieldQuantityKg | None = None

    weather_condition: str | None = None
    field_prep_score: int | None = None
    weather_score: int | None = None
    care_score: int | None = None
    variety_resistance_score: int | None = None

    recommendation: str | None = None
    notes: str | None = None

    latitude: Decimal | None = None
    longitude: Decimal | None = None
    photo_urls: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # Offline submission (round 8-4A) — all THREE optional for backward
    # compatibility: an online client omits them entirely and is unchanged.
    # When one is present all three must be (validated below), signalling an
    # offline draft being submitted after reconnecting.
    #   client_submission_id      — idempotency key for one offline draft; a
    #                               retry with the same key + same identity
    #                               returns the already-created record (200)
    #                               instead of a duplicate.
    #   captured_at               — when the form was filled on-site (offline).
    #                               Must be timezone-aware; the endpoint also
    #                               bounds it (not >5min future, not >7d old).
    #   captured_plot_cycle_id    — the plot's active cycle AT CAPTURE time, a
    #                               consistency GUARD only: the server still
    #                               resolves the cycle from the verified token /
    #                               current state and rejects (409) if the plot
    #                               rolled over since. A client can NEVER use
    #                               this field to choose a cycle itself.
    client_submission_id: UUID | None = None
    captured_at: datetime.datetime | None = None
    captured_plot_cycle_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_offline_submission_fields(self) -> "PublicRecordCreate":
        offline = (
            self.client_submission_id,
            self.captured_at,
            self.captured_plot_cycle_id,
        )
        provided = [value is not None for value in offline]
        if any(provided) and not all(provided):
            raise ValueError(
                "clientSubmissionId, capturedAt and capturedPlotCycleId must be "
                "provided together for an offline submission"
            )
        # A naive datetime is ambiguous — reject rather than guess a zone.
        if self.captured_at is not None and self.captured_at.utcoffset() is None:
            raise ValueError("capturedAt must be timezone-aware")
        return self


class PublicRecordCreateResult(CamelBaseModel):
    """Deliberately minimal — no recorded_by_id/email (that's always the
    internal system account here, not meaningful to the public caller) and
    no inspection_session_token/claims. Just enough for a confirmation
    screen: what was submitted, for which plot/supplier, and when."""

    id: UUID
    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    record_date: datetime.date
    submitted_by_name: str | None
    created_at: datetime.datetime
    # Offline receipt (round 8-4A) — echoed back so the frontend can match the
    # response to the draft it sent (and confirm the accepted captured time).
    # Both NULL for an online submission.
    client_submission_id: UUID | None = None
    captured_at: datetime.datetime | None = None
