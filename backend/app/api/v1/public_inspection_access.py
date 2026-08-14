"""Public (unauthenticated) phone-access inspection flow — round 8-3B.

Enter a phone → get the plots that phone may inspect → pick one + an inspector
type → receive an inspection_session_token (phone-bound) that record creation
consumes. NO inspection code anywhere in this flow.

Security posture (see docs/security.md §9 + the round brief):
  * The phone is accepted ONLY in a POST body, never a URL/query string.
  * Lookup is an EXACT match on the canonical phone — no partial search.
  * No response ever contains a phone, phoneLast4, or qrKey; nothing is logged
    with the raw phone. The phone-access token carries only access-row ids.
  * Every endpoint is rate-limited and runs under the public RLS context.
  * Assignment membership is re-checked from the token on every call and again,
    under a row lock, at record-create time.

Note: this module deliberately does NOT use `from __future__ import
annotations`, same reason as public_plots.py / public_records.py — slowapi's
@limiter.limit wraps the route with functools.wraps and would break FastAPI's
forward-ref resolution of the payload/response models at app-boot.
"""
import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio

from app.api.deps.scope import get_public_plot_rls_context
from app.auth.inspection_session import encode_inspection_session_token
from app.auth.phone_access_session import (
    MAX_ACCESS_PHONE_IDS,
    PhoneAccessTokenError,
    decode_phone_access_session_token,
    encode_phone_access_session_token,
)
from app.auth.phone_password_session import (
    CredentialGrant,
    PhonePasswordTokenError,
    decode_phone_password_session_token,
    encode_phone_password_session_token,
)
from app.auth.plot_access_password import (
    PLOT_ACCESS_PASSWORD_MAX_LENGTH,
    PLOT_ACCESS_PASSWORD_MIN_LENGTH,
    PlotAccessPasswordPolicyError,
    PlotAccessPepperMissingError,
    build_phone_lockout_fingerprint,
    build_plot_access_password_lookup_digest,
    validate_plot_access_password,
    verify_plot_access_password,
)
from app.core import public_access_lockout
from app.core.config import get_settings
from app.core.phone import normalize_thai_mobile
from app.core.rate_limit import get_client_ip, limiter
from app.db.session import get_db
from app.repositories import plot_access_credential_repository as credential_repo
from app.repositories import plot_access_phone_repository as phone_repo
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.repositories import plot_repository as plot_repo
from app.schemas.phone_access import (
    PublicInspectionAccessConfigResponse,
    PublicPhoneAccessListRequest,
    PublicPhoneAccessListResponse,
    PublicPhoneAccessLookupRequest,
    PublicPhoneAccessLookupResponse,
    PublicPhoneAccessPlotItem,
    PublicPhoneAccessSelectPlotRequest,
    PublicPhoneAccessSelectPlotResponse,
)

router = APIRouter(tags=["public"])

# One generic message for every phone-access token failure and every
# unusable-plot case — never leaks whether a phone/plot exists.
_INVALID_TOKEN_DETAIL = "Invalid or expired phone access session token"
_NOT_FOUND_DETAIL = "Not found"


# Round 8-19.1 — "วันนี้" means the Thai business day, not the UTC one. Thailand
# is UTC+7 with no DST, so a UTC date is wrong for the first seven hours of
# every Thai day (00:00–06:59 ICT is still the PREVIOUS UTC date): field work
# recorded at 01:00 ICT looked like yesterday's, so its plot card never flipped
# to "ตรวจแล้ววันนี้". stdlib zoneinfo only — no new dependency (the runtime
# image ships the system IANA tz database, and the Windows dev environment gets
# `tzdata` transitively via psycopg/tzlocal).
_BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def _today() -> datetime.date:
    """Today's date in Asia/Bangkok. Compared against Record.record_date, which
    is a plain calendar DATE reported from the field — so it must be resolved
    against the field's own calendar, never UTC's. Nothing about how timestamps
    are STORED changes: created_at stays UTC, the DB's timezone is untouched."""
    return datetime.datetime.now(_BANGKOK_TZ).date()


def _plot_item(access_row, plot, supplier, latest_dates) -> PublicPhoneAccessPlotItem:
    """Build one list item from an (access_row, plot, supplier) tuple. Reads the
    plot's eager-loaded active cycle; carries NO phone/qrKey/GPS/address/token.

    Round 8-4C.1 Part E: EVERY cycle/yield-plan/inspection-derived field below
    is null whenever the plot has no active cycle right now — not just the
    active-cycle-owned ones (crop/variety/lot_no/planting_date/plant_count/
    expected_yield_*), but ALSO the Plot-owned inspection snapshot
    (current_yield_pct/current_stage/last_inspected_at). Round 8-4C originally
    left the snapshot trio unconditional (matching the pre-existing
    last_inspected_at behavior) — reversed here: once a cycle closes, its last
    inspection's yield/stage/timestamp must not keep leaking into the
    authorized plot list looking like current information. A plot between
    cycles only ever needs to say "no active cycle" (unavailable_reason),
    never a stale snapshot from the cycle that just ended.

    Round 8-19: lastInspectionDate/inspectedToday come from `latest_dates` —
    the ACTIVE CYCLE's own latest active record date, keyed by PlotCycle.id
    (see _build_items). Both are plot+cycle level, so every number authorized
    on a plot sees the same status; neither is derived from access_row now."""
    cycle = getattr(plot, "active_cycle", None)
    can_inspect = cycle is not None
    # None whenever there's no active cycle — a single guard so the snapshot
    # trio below can never accidentally read a stale Plot column.
    current_snapshot = plot if can_inspect else None
    # No active cycle → no date at all (never the previous cycle's).
    last_inspection_date = latest_dates.get(cycle.id) if cycle is not None else None
    return PublicPhoneAccessPlotItem(
        plot_id=plot.id,
        plot_code=plot.plot_code,
        plot_name=plot.name,
        supplier_id=supplier.id,
        supplier_code=supplier.code,
        supplier_name=supplier.name,
        access_type=access_row.access_type,
        can_inspect=can_inspect,
        unavailable_reason=None if can_inspect else "no_active_cycle",
        plot_cycle_id=cycle.id if cycle is not None else None,
        cycle_no=cycle.cycle_no if cycle is not None else None,
        cycle_label=cycle.cycle_label if cycle is not None else None,
        crop=cycle.crop if cycle is not None else None,
        variety=cycle.variety if cycle is not None else None,
        inspected_today=last_inspection_date == _today(),
        last_inspection_date=last_inspection_date,
        last_inspected_at=getattr(current_snapshot, "last_inspected_at", None),
        lot_no=cycle.lot_no if cycle is not None else None,
        planting_date=cycle.planting_date if cycle is not None else None,
        plant_count=cycle.plant_count if cycle is not None else None,
        expected_yield_full=cycle.expected_yield_full if cycle is not None else None,
        expected_yield_unit=cycle.expected_yield_unit if cycle is not None else None,
        current_yield_pct=getattr(current_snapshot, "current_yield_pct", None),
        current_stage=getattr(current_snapshot, "current_stage", None),
    )


async def _build_items(db, rows) -> list[PublicPhoneAccessPlotItem]:
    """Round 8-19 — ONE batch query for every listed plot's active-cycle
    latest record date (never per-plot), keyed by PlotCycle.id.

    Replaces the previous access_phone_ids_inspected_today lookup, which was
    keyed by plot_access_phone_id: field work done from a plot's เบอร์หลัก did
    not show up for a เบอร์เสริม on that same plot, so two authorized numbers
    saw two different "ตรวจแล้ววันนี้" answers for the same plot. Status is
    plot+cycle level now, so they agree. (The repository helper itself is
    left in place — it is still exercised by its own tests and is not this
    endpoint's to delete.)

    Plots with no active cycle contribute no cycle id, so they can never pick
    up a date from a closed cycle."""
    cycle_ids = [
        cycle.id
        for (_access_row, plot, _sup) in rows
        if (cycle := getattr(plot, "active_cycle", None)) is not None
    ]
    latest_dates = await plot_cycle_repo.get_latest_active_record_dates_for_cycles(
        db, cycle_ids
    )
    return [_plot_item(a, p, s, latest_dates) for (a, p, s) in rows]


# --- Round 8-9C: phone + plot-password enforcement -------------------------
#
# Everything below is INERT while PUBLIC_PLOT_PASSWORD_ENFORCEMENT is false:
# _enforcement_on() gates every branch, so the phone-only flow above runs
# byte-for-byte as it did in round 8-3B.

# How many candidate rows may be bcrypt-verified concurrently. Sequential
# would be simplest, but a phone+password legitimately matching a dozen plots
# would then cost a dozen serial ~250ms rounds on a public endpoint. An
# UNBOUNDED gather is the other extreme and is exactly the amplification an
# attacker wants: one crafted request turning into N concurrent bcrypt rounds.
# A small fixed ceiling keeps the worst case proportional and predictable.
_MAX_VERIFY_CONCURRENCY = 4


def _password_token_expires_in() -> int:
    from app.auth.phone_password_session import EXPIRE_HOURS

    return EXPIRE_HOURS * 3600


def _enforcement_on() -> bool:
    """Read the flag fresh on every call (never cached at import time) so a
    test can flip it with monkeypatch and so an operator restart is the only
    thing needed in production."""
    return bool(get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT)


def _generic_not_found() -> HTTPException:
    """THE public failure. Unknown phone, malformed phone, missing password,
    malformed password, wrong password, no credential, inactive credential/
    plot/supplier, and "nothing bcrypt-verified" all return this identical
    response — otherwise the endpoint becomes a phone/plot enumeration oracle
    (docs/security.md §9)."""
    return HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)


def _generic_locked_out() -> HTTPException:
    """Generic 429. Deliberately does NOT reveal which tier tripped or how many
    attempts remain — that would tell an attacker exactly how to pace."""
    return HTTPException(
        status_code=429, detail="Too many attempts. Please try again later."
    )


async def _verify_candidates(rows, password: str) -> list[tuple]:
    """bcrypt-verify EVERY candidate the blind index narrowed to, and return
    only the rows that actually pass.

    The digest is an INDEX, never a proof: it is unsalted by construction, so
    treating a digest hit as authorization would let anyone who learned one
    plot's password walk into every plot sharing it without the password ever
    being checked. Each row's own bcrypt hash is verified here.

    bcrypt runs in a worker thread (never on the event loop — ~250ms of
    blocking CPU on a public endpoint is a self-inflicted DoS), with a bounded
    number in flight at once.
    """
    if not rows:
        return []
    semaphore = asyncio.Semaphore(_MAX_VERIFY_CONCURRENCY)

    async def _check(row):
        _access, _plot, _supplier, credential = row
        async with semaphore:
            ok = await asyncio.to_thread(
                verify_plot_access_password, password, credential.password_hash
            )
        return row if ok else None

    results = await asyncio.gather(*(_check(r) for r in rows))
    return [r for r in results if r is not None]


async def _authorize_phone_password(
    db: AsyncSession, request: Request, phone_raw: str, password_raw: str | None
) -> tuple[list[tuple], str]:
    """The enforcement path of lookup. Returns (verified_rows, token).

    Order is load-bearing:
      1. normalize the phone (malformed → the SAME generic failure, never 422)
      2. check the failure lockout BEFORE any digest, query or bcrypt — the
         whole point of the counter is to stop work, not to record it
      3. validate the password with the SHARED policy
      4. build the blind-index digest and NARROW candidates in one query
      5. bcrypt-verify every candidate
      6. success clears the counters; every failure increments them

    Every failure raises the identical generic 404.
    """
    try:
        phone_normalized = normalize_thai_mobile(phone_raw)
    except ValueError:
        # A malformed phone can't even be fingerprinted — nothing to count,
        # and the caller learns nothing either way.
        raise _generic_not_found() from None

    try:
        fingerprint = build_phone_lockout_fingerprint(phone_normalized)
    except PlotAccessPepperMissingError as exc:
        # Enforcement without a pepper is a deployment fault, not a caller
        # fault. config.py refuses to boot in this state; this is the
        # belt-and-suspenders path, and it must NOT leak the reason.
        raise _generic_not_found() from exc

    client_ip = get_client_ip(request)
    # (2) BEFORE the digest, the query and the bcrypt round.
    if public_access_lockout.is_locked_out(client_ip, fingerprint):
        raise _generic_locked_out()

    def _fail() -> HTTPException:
        public_access_lockout.register_failure(client_ip, fingerprint)
        return _generic_not_found()

    if password_raw is None:
        raise _fail()
    try:
        password = validate_plot_access_password(password_raw)
    except PlotAccessPasswordPolicyError:
        raise _fail() from None

    try:
        digest = build_plot_access_password_lookup_digest(password)
    except PlotAccessPepperMissingError as exc:
        raise _generic_not_found() from exc

    candidates = await credential_repo.lookup_active_access_rows_by_phone_and_digest(
        db, phone_normalized, digest
    )
    # Cap before bcrypt: a crafted-but-plausible digest must not be able to
    # buy an unbounded number of hash rounds.
    candidates = candidates[:MAX_ACCESS_PHONE_IDS]
    verified = await _verify_candidates(candidates, password)
    if not verified:
        raise _fail()

    public_access_lockout.clear_failures(client_ip, fingerprint)
    token, _expires = encode_phone_password_session_token(
        grants=[
            CredentialGrant(
                access_phone_id=access.id,
                credential_id=credential.id,
                credential_version=credential.credential_version,
            )
            for (access, _plot, _supplier, credential) in verified
        ]
    )
    return verified, token


async def _resolve_password_session(
    db: AsyncSession, token: str
) -> list[tuple]:
    """Re-authorize a password-verified session on /plots and /select-plot.

    ONE set-based re-query, then a grant-by-grant comparison of credential id
    AND version. A password CHANGE bumps the version, so every grant minted
    against the old one silently stops matching — which is exactly how
    "changing the password ends existing sessions" is implemented. A
    deactivated credential/phone/plot/supplier simply doesn't come back from
    the query at all.

    A legacy phone-only token is rejected here (wrong `type`), so it can never
    be replayed as if it had been password-verified.
    """
    try:
        grants = decode_phone_password_session_token(token)
    except PhonePasswordTokenError as exc:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL) from exc

    live = await credential_repo.list_active_access_rows_by_grants(
        db, [g.access_phone_id for g in grants]
    )
    by_access_id = {access.id: row for row in live for access in (row[0],)}

    still_valid: list[tuple] = []
    for grant in grants:
        row = by_access_id.get(grant.access_phone_id)
        if row is None:
            continue
        credential = row[3]
        if (
            credential.id != grant.credential_id
            or credential.credential_version != grant.credential_version
        ):
            continue
        still_valid.append(row)
    return still_valid


@router.get(
    "/inspection-access/config",
    response_model=PublicInspectionAccessConfigResponse,
)
@limiter.limit("30/minute")
async def public_inspection_access_config(
    request: Request,
    response: Response,
) -> PublicInspectionAccessConfigResponse:
    """What the public /public/inspect page must know BEFORE it renders a form:
    is a plot password required right now, and what length is legal.

    Deliberately has NO database dependency at all — not even get_db. It answers
    from settings + the shared policy constants, so it cannot be turned into a
    probe for whether any particular plot/phone/credential exists, and it stays
    answerable during a database incident (a page that can't tell whether a
    password is required must fail closed, and it can only do that if this
    endpoint is the one thing still up).

    min/max come from app.auth.plot_access_password — the SAME constants the
    validator, the admin PUT and the Excel importer use. Never re-typed here:
    a policy change must reach the public UI by changing one number in one file.

    No login (this is the unauthenticated flow's own bootstrap), and no RLS
    context is set up because nothing is queried.

    Round 8-9E — `Cache-Control: no-store`. This answer changes the instant an
    operator flips the flag, and a copy cached anywhere in between (browser
    cache, a CDN, a corporate proxy) would keep telling field users "no password
    required" after enforcement went live — every one of them locked out by a
    404 they cannot act on. no-store (not merely no-cache) because the value is
    a security posture, not a resource: there is no version of it worth keeping
    on disk to revalidate later.
    """
    response.headers["Cache-Control"] = "no-store"
    return PublicInspectionAccessConfigResponse(
        password_required=_enforcement_on(),
        password_min_length=PLOT_ACCESS_PASSWORD_MIN_LENGTH,
        password_max_length=PLOT_ACCESS_PASSWORD_MAX_LENGTH,
    )


@router.post(
    "/inspection-access/lookup",
    response_model=PublicPhoneAccessLookupResponse,
    dependencies=[Depends(get_public_plot_rls_context)],
)
@limiter.limit("5/minute")
async def phone_access_lookup(
    payload: PublicPhoneAccessLookupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PublicPhoneAccessLookupResponse:
    """Enter a phone → mint a phone-access session token + list the plots it may
    inspect. Generic 404 when the phone matches no usable plot (never says
    whether the phone "exists"). The raw phone is normalized here (not in the
    schema) so a bad value is never echoed in a 422, and is never logged."""
    if _enforcement_on():
        # Round 8-9C — phone AND password. Every failure here is the identical
        # generic 404 (or a generic 429 when locked out); there is deliberately
        # NO fallback to the phone-only path below.
        verified, token = await _authorize_phone_password(
            db, request, payload.phone,
            payload.password.get_secret_value() if payload.password is not None else None,
        )
        expires_in = _password_token_expires_in()
        rows = [(access, plot, supplier) for (access, plot, supplier, _c) in verified]
    else:
        try:
            phone_normalized = normalize_thai_mobile(payload.phone)
        except ValueError:
            # Generic — do NOT include the raw phone (PII).
            raise HTTPException(status_code=422, detail="รูปแบบเบอร์โทรไม่ถูกต้อง")

        rows = await phone_repo.lookup_active_access_rows_by_phone(db, phone_normalized)
        if not rows:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)

        # Cap to the token's max (a phone realistically maps to few plots); keep
        # the list and the token in agreement.
        rows = rows[:MAX_ACCESS_PHONE_IDS]
        access_phone_ids = [access_row.id for (access_row, _p, _s) in rows]
        token, expires_in = encode_phone_access_session_token(
            access_phone_ids=access_phone_ids
        )

    plot_ids = {plot.id for (_a, plot, _s) in rows}
    qr_matched_plot_id: UUID | None = None
    if payload.qr_key:
        qr_plot = await plot_repo.get_plot_by_qr_key(db, payload.qr_key)
        # The QR must resolve to one of THIS phone's plots — otherwise the same
        # generic 404 (a valid phone token is not a QR oracle for other plots).
        #
        # Round 8-9C: under enforcement `plot_ids` is the PASSWORD-VERIFIED set,
        # so a QR pointing at a plot the caller didn't prove the password for is
        # the same 404. QR is matched AFTER authorization and can never bypass
        # it — a wrong password fails above, before this code is reached at all.
        if qr_plot is None or qr_plot.id not in plot_ids:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
        qr_matched_plot_id = qr_plot.id

    return PublicPhoneAccessLookupResponse(
        phone_access_session_token=token,
        expires_in=expires_in,
        qr_matched_plot_id=qr_matched_plot_id,
        plots=await _build_items(db, rows),
    )


@router.post(
    "/inspection-access/plots",
    response_model=PublicPhoneAccessListResponse,
    dependencies=[Depends(get_public_plot_rls_context)],
)
@limiter.limit("30/minute")
async def phone_access_plots(
    payload: PublicPhoneAccessListRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PublicPhoneAccessListResponse:
    """Re-list a phone-access session's plots from the token — no new token
    minted. A row (or its plot/supplier) deactivated since mint disappears; a
    NEW assignment made after mint does NOT appear (the token's id set is
    fixed). Refreshes inspectedToday. Never returns a phone."""
    if _enforcement_on():
        # Round 8-9C — the token must be a PASSWORD-VERIFIED one (a legacy
        # phone-only token has a different `type` and is rejected inside
        # _resolve_password_session), and every grant is re-checked against the
        # live credential's id AND version. A password changed since the token
        # was minted drops that grant; no valid grants left is the same generic
        # 401 as any other bad session.
        verified = await _resolve_password_session(
            db, payload.phone_access_session_token
        )
        if not verified:
            raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
        rows = [(access, plot, supplier) for (access, plot, supplier, _c) in verified]
        return PublicPhoneAccessListResponse(plots=await _build_items(db, rows))

    try:
        access_phone_ids = decode_phone_access_session_token(
            payload.phone_access_session_token
        )
    except PhoneAccessTokenError:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    rows = await phone_repo.list_active_access_rows_by_ids(db, access_phone_ids)
    return PublicPhoneAccessListResponse(plots=await _build_items(db, rows))


@router.post(
    "/inspection-access/select-plot",
    response_model=PublicPhoneAccessSelectPlotResponse,
    dependencies=[Depends(get_public_plot_rls_context)],
)
@limiter.limit("30/minute")
async def phone_access_select_plot(
    payload: PublicPhoneAccessSelectPlotRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PublicPhoneAccessSelectPlotResponse:
    """Pick one plot + inspector type → mint an inspection_session_token bound
    to plot/supplier/active-cycle/access-phone/inspectorType. inspectorType is
    validated by the request schema (invalid → 422). The plot must still be one
    of the token's assignments and still active with an active cycle:
      - assignment revoked / plot not in token / inactive → generic 404
      - plot between cycles (no active cycle) → 409 {code: no_active_cycle}
    Never returns a phone; the phone binding lives inside the minted token."""
    credential_binding: tuple[UUID, int] | None = None
    if _enforcement_on():
        # Round 8-9C — re-authorize the whole session (credential id + version)
        # and require the requested plot to be inside the still-valid set. A
        # password changed since the token was minted means no valid grant for
        # that plot, which is the same generic 404 as an unknown plot.
        verified = await _resolve_password_session(
            db, payload.phone_access_session_token
        )
        if not verified:
            raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
        match = next(
            (row for row in verified if row[1].id == payload.plot_id), None
        )
        if match is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
        access_row, _plot_row, _supplier_row, credential_row = match
        credential_binding = (credential_row.id, credential_row.credential_version)
    else:
        try:
            access_phone_ids = decode_phone_access_session_token(
                payload.phone_access_session_token
            )
        except PhoneAccessTokenError:
            raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

        # Assignment recheck: the row must still be active AND belong to this
        # plot, and its id must be one the token carries.
        access_row = await phone_repo.get_access_row_for_plot_from_ids(
            db, access_phone_ids, payload.plot_id
        )
        if access_row is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)

    plot = await plot_repo.get_plot(db, payload.plot_id)
    supplier = getattr(plot, "supplier", None) if plot is not None else None
    if (
        plot is None or not plot.is_active
        or supplier is None or not supplier.is_active
    ):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)

    cycle = getattr(plot, "active_cycle", None)
    if cycle is None:
        # Public-safe, distinct from the 404s: the caller legitimately owns this
        # plot (it's in their token), so telling them it's between cycles leaks
        # nothing. Stable machine code, no PII.
        raise HTTPException(status_code=409, detail={"code": "no_active_cycle"})

    token, expires_in = encode_inspection_session_token(
        plot_id=plot.id,
        supplier_id=supplier.id,
        plot_cycle_id=cycle.id,
        plot_access_phone_id=access_row.id,
        inspector_type=payload.inspector_type,
        # Round 8-9C — additive: None (claims omitted) when enforcement is off,
        # so a legacy token shape is unchanged; the credential the caller
        # actually proved when enforcement is on, so record-create can re-check
        # it under the Plot lock.
        plot_access_credential_id=credential_binding[0] if credential_binding else None,
        plot_access_credential_version=credential_binding[1] if credential_binding else None,
    )
    return PublicPhoneAccessSelectPlotResponse(
        inspection_session_token=token,
        expires_in=expires_in,
        plot_id=plot.id,
        plot_code=plot.plot_code,
        plot_name=plot.name,
        supplier_id=supplier.id,
        supplier_code=supplier.code,
        supplier_name=supplier.name,
        plot_cycle_id=cycle.id,
        cycle_no=cycle.cycle_no,
        cycle_label=cycle.cycle_label,
        current_crop=cycle.crop,
        current_variety=cycle.variety,
        current_lot_no=cycle.lot_no,
        current_planting_date=cycle.planting_date,
        plant_count=cycle.plant_count,
        expected_yield_full=cycle.expected_yield_full,
        expected_yield_unit=cycle.expected_yield_unit,
        current_yield_pct=plot.current_yield_pct,
        current_stage=plot.current_stage,
        last_inspected_at=plot.last_inspected_at,
    )
