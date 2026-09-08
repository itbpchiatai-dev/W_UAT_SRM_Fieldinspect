"""Public phone-access flow schemas (round 8-3B).

The unauthenticated "enter a phone → pick a plot → inspect" flow. NONE of
these response models ever carry a phone number, a qrKey, GPS, address, or
yield/plan data — a public caller only sees enough to choose a plot.

The lookup request's credential fields carry NO Pydantic constraints at all
(round L): every check runs in the ENDPOINT
(app/api/v1/public_inspection_access.py), because any Pydantic-level rejection
— a type error, a min_length, a max_length — echoes the offending value in the
422 body's `input` key (docs/security.md §9 — never return/log raw phone).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, SkipValidation

from app.schemas.base import CamelBaseModel

# Round 8-11A — canonical API values; "extension" was renamed to "chiatai"
# (migration 0047). Mirrors app.db.models.record.INSPECTOR_TYPES exactly, so a
# value FastAPI accepts here is always one the DB CHECK constraint allows.
# The Thai display labels live in the frontend only.
InspectorType = Literal["farmer", "supplier", "chiatai"]


class PublicInspectionAccessConfigResponse(CamelBaseModel):
    """GET /public/inspection-access/config — the ONLY thing a public client is
    told about the plot-password feature (round 8-9D).

    Deliberately three booleans/ints and nothing else. The frontend cannot infer
    whether a password is required from a build-time env var: frontend and
    backend are deployed independently, so a stale bundle would either send no
    password to a backend that now demands one (every user locked out) or show a
    password field a backend still ignores. The BACKEND RUNTIME is the source of
    truth, and this is how it says so.

    Carries no pepper, no credential status, no per-plot/per-phone information
    and no count — knowing that passwords are switched on system-wide reveals
    nothing about any particular plot, phone or supplier.
    """

    password_required: bool
    password_min_length: int
    password_max_length: int


class PublicPhoneAccessLookupRequest(CamelBaseModel):
    """POST /public/inspection-access/lookup body — UNAUTHENTICATED, and every
    field on it is a farmer's credential.

    Round L — all three fields are `SkipValidation`, and the shape checks that
    used to be `Field` constraints run by hand in the endpoint.

    The previous shape carried two assumptions, both of which were WRONG when
    tested:

      * "phone is normalized in the endpoint (NOT here) so a bad value is
        never echoed in a validation error" — true of normalize_thai_mobile,
        but `Field(min_length=1, max_length=32)` is itself a Pydantic-level
        rejection, and FastAPI's RequestValidationError handler serialises the
        offending value into the 422 body. A 40-character phone came straight
        back to the caller.
      * "SecretStr so a length violation reports `**********` as the offending
        input" — it does not. `errors()[i]["input"]` holds the RAW string on a
        max_length violation, so an over-length password was echoed verbatim.

    Verified before the change:

        M.model_validate({"phone": "081" + "0"*37})
        -> errors()[0]["input"] == "0810000000000000000000000000000000000000"
        M.model_validate({"phone": "0812345678", "password": "p"*80})
        -> errors()[0]["input"] == "pppp…"   (not "**********")

    Same class of bug, and same fix, as PlotAccessPhoneConfig (round 8-17C.1)
    and AdminPasswordResetRequest.
    """

    # `repr=False` on all three replaces what SecretStr was doing well: keeping
    # the value out of `repr(payload)`, so an accidental log of the model — or
    # a traceback frame that renders locals — cannot print a credential. Unlike
    # SecretStr it is not a type to coerce to, so it cannot cause a rejection;
    # the two protections are independent and this schema now has both.
    phone: SkipValidation[str] = Field(..., repr=False)
    # Round 8-9C — additive. Optional on the wire so a pre-8-9C client keeps
    # working while PUBLIC_PLOT_PASSWORD_ENFORCEMENT is false; the ENDPOINT
    # decides whether it is required, never this schema. The real
    # 4-20-ASCII-digit policy runs there too via the SHARED
    # validate_plot_access_password, and its failure is folded into the one
    # generic public error (never "your password is the wrong length").
    password: SkipValidation[str | None] = Field(None, repr=False)
    qr_key: SkipValidation[str | None] = Field(None, repr=False)


class PublicPhoneAccessPlotItem(CamelBaseModel):
    """One plot a phone may inspect. Deliberately minimal — no phone, no qrKey,
    no GPS/address, no yield plan, no access-phone id, no inspection code."""

    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    access_type: str
    # canInspect is false when the plot has no active planting cycle; the
    # reason is a stable machine code, not free text.
    can_inspect: bool
    unavailable_reason: Literal["no_active_cycle"] | None = None
    plot_cycle_id: UUID | None = None
    cycle_no: int | None = None
    cycle_label: str | None = None
    crop: str | None = None
    variety: str | None = None
    # Round 8-19 — inspectedToday is now PLOT+CYCLE level, not per-access-row:
    # it is true when the plot's ACTIVE cycle has an active record dated today,
    # no matter which of the plot's authorized numbers made it. It used to be
    # keyed by plot_access_phone_id, which meant the เบอร์หลัก and a เบอร์เสริม
    # on the same plot saw different statuses for the same field work.
    inspected_today: bool = False
    # Round 8-19 — the latest active record date WITHIN the active cycle
    # (date only, never a time). Null when the plot has no active cycle, or
    # when the active cycle has no active record yet — a previous cycle's
    # inspection must never surface here.
    last_inspection_date: date | None = None
    # Pre-8-19 field, kept for compatibility: the Plot's denormalized
    # last-inspection TIMESTAMP, which is not cycle-scoped the way
    # lastInspectionDate above is. Prefer lastInspectionDate for anything
    # cycle-related.
    last_inspected_at: datetime | None = None
    # Round 8-3K — a phone with several plots needs enough to pick the right
    # one before selecting. Sourced from the SAME active cycle as crop/variety
    # above (never the plot's current_* mirror) — null with no active cycle,
    # same rule, same reason (a closed cycle's stale lot/planting-date must
    # not leak here either).
    lot_no: str | None = None
    planting_date: date | None = None
    # Round 8-4C Part B — lets the offline flow open a full inspection form for
    # a plot picked from this cached list without another round-trip. Same
    # active-cycle-or-null rule as crop/variety/lot_no/planting_date above.
    plant_count: int | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None
    # currentYieldPct/currentStage — the Plot's inspection-derived snapshot
    # (same source as PublicPhoneAccessSelectPlotResponse below), unconditional
    # on active-cycle presence (mirrors lastInspectedAt above, which has always
    # been sourced from the Plot regardless of cycle state).
    current_yield_pct: Decimal | None = None
    current_stage: str | None = None


class PublicPhoneAccessLookupResponse(CamelBaseModel):
    phone_access_session_token: str
    expires_in: int
    # When a qrKey was supplied AND it resolves to one of the phone's plots,
    # its id is echoed so the client can pre-select it. Never leaks the qrKey.
    qr_matched_plot_id: UUID | None = None
    plots: list[PublicPhoneAccessPlotItem] = Field(default_factory=list)


class PublicPhoneAccessListRequest(CamelBaseModel):
    phone_access_session_token: str = Field(..., min_length=1)


class PublicPhoneAccessListResponse(CamelBaseModel):
    plots: list[PublicPhoneAccessPlotItem] = Field(default_factory=list)


class PublicPhoneAccessSelectPlotRequest(CamelBaseModel):
    phone_access_session_token: str = Field(..., min_length=1)
    plot_id: UUID
    inspector_type: InspectorType


class PublicPhoneAccessSelectPlotResponse(CamelBaseModel):
    """Exchange result — the same shape a QR/inspection-code verify returns
    (so the public form is identical), minus anything phone-related. The
    inspection_session_token it carries is what actually gates record creation;
    the phone binding lives INSIDE that token (server-side), never here."""

    inspection_session_token: str
    expires_in: int
    plot_id: UUID
    plot_code: str
    plot_name: str
    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    plot_cycle_id: UUID
    cycle_no: int
    cycle_label: str | None = None
    current_crop: str | None = None
    current_variety: str | None = None
    current_lot_no: str | None = None
    current_planting_date: date | None = None
    plant_count: int | None = None
    expected_yield_full: Decimal | None = None
    expected_yield_unit: str | None = None
    # Inspection-derived current-status snapshot (round 8-3J) — verbatim from
    # plot.current_yield_pct/current_stage/last_inspected_at, the SAME columns
    # the logged-in RecordForm reads (plot_repository.sync_current_status_from_record).
    # Never derived from records/PlotCycle here — expected_yield_full above stays
    # the 100%-target from PlotCycle; current_yield_pct is the latest inspection's
    # percentage of that target. Deliberately absent from the lookup/plots list
    # responses (PublicPhoneAccessPlotItem) — only revealed once a plot is picked.
    current_yield_pct: Decimal | None = None
    current_stage: str | None = None
    last_inspected_at: datetime | None = None
    # Round C — the EXACT kg the cycle's most recent inspection reported
    # (records.yield_quantity_kg), so the form can pre-fill "ผลผลิตที่เก็บได้"
    # on a follow-up "ผลผลิตสุดท้าย" inspection with the number that was
    # actually entered.
    #
    # Deliberately not derived from current_yield_pct above: recovering kg from
    # a stored percentage is a round-trip through NUMERIC(5,1) and comes back
    # off by a rounding step (enter 1,250 and the form would suggest 1,248),
    # which is exactly the kind of quiet drift a "carried forward" number must
    # not have. None when the cycle has no record yet, or when its latest one
    # predates the kg-first input (round 8-8A).
    last_yield_quantity_kg: Decimal | None = None
