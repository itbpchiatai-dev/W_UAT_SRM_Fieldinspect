"""Public (unauthenticated) record creation — round 8. Consumes an
inspection_session_token minted by
POST /api/v1/public/inspection-access/select-plot (the phone-access flow;
round 8-3G retired the legacy inspection-code verify endpoint that used to
mint this same token type) to create exactly one record for exactly the
plot/supplier that token names. No plotId/supplierId accepted from the
client (PublicRecordCreate forbids extra fields — a client trying to
smuggle either in gets 422, never a silent override). No public
read/list/update/delete here or anywhere else in this module.

Round 13.1: no public photo *download* route exists (create-with-photos,
i.e. upload, is unaffected). Deliberately deferred rather than silently
dropped — the inspection_session_token is scoped to a single plot/supplier
and expires in 30 minutes (app/auth/inspection_session.py), so reusing it
post-submission for viewing would need its own decision about whether that
still-valid window is an acceptable download-auth mechanism, or whether the
PublicRecordCreateResult should mint a separate narrower token. Not needed
yet since there's no public UI consuming it this round.

Note: this module deliberately does NOT use `from __future__ import
annotations`, same reason as app/api/v1/auth.py and
app/api/v1/public_plots.py — slowapi's @limiter.limit wraps the route with
functools.wraps, which sets the wrapper's __globals__ to slowapi's module;
with string annotations FastAPI's forward-ref resolver would fail to find
the payload/response models at app-boot. Concrete annotations sidestep it.
"""
import datetime
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from jose import JWTError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.scope import set_public_record_rls_context
from app.auth.inspection_session import decode_inspection_session_token
from app.core.config import get_settings
from app.core.rate_limit import get_client_ip, limiter
from app.db.models.record import INSPECTOR_TYPES
from app.db.session import get_db
from app.repositories import plot_access_credential_repository as credential_repo
from app.repositories import plot_access_phone_repository as phone_repo
from app.repositories import plot_cycle_repository as plot_cycle_repo
from app.repositories import plot_repository as plot_repo
from app.repositories import record_repository as record_repo
from app.repositories import supplier_repository as supplier_repo
from app.schemas.record import PublicRecordCreate, PublicRecordCreateResult, RecordCreate
from app.services.external_submission import get_external_submission_user
from app.services.inspection_photos import cleanup_photos, get_photo_storage, validate_and_save_photos
from app.services import inspection_protocols as protocol_service
from app.services.inspection_protocols import ProtocolValidationError
from app.services import yield_calculation
from app.services.yield_calculation import YieldValidationError

router = APIRouter(tags=["public"])

_INVALID_TOKEN_DETAIL = "Invalid or expired inspection session token"

# Structured error codes the offline flow returns (round 8-4A, Part H) — the
# frontend (round 8-4B) branches on these. Always inside a generic detail
# object, never leaking which phone/plot/supplier/record they concern.
_ERR_PLANTING_CYCLE_CHANGED = "planting_cycle_changed"
_ERR_OFFLINE_DRAFT_EXPIRED = "offline_draft_expired"
_ERR_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
_ERR_CAPTURED_AT_INVALID = "offline_captured_at_invalid"

# captured_at bounds (round 8-4A). A little future skew is allowed for clock
# drift; older than the max draft age is a stale/expired draft.
_MAX_CAPTURE_CLOCK_SKEW = datetime.timedelta(minutes=5)
_MAX_DRAFT_AGE = datetime.timedelta(days=7)

# The partial-unique index name from migration 0041 — the ONLY constraint an
# idempotency-key collision may trip. Matched exactly (never a broad message
# parse) so a different integrity failure is never misread as a duplicate key.
_CLIENT_SUBMISSION_UNIQUE_INDEX = "uq_records_client_submission_id"


def _matches_replay_identity(existing: Any, plot: Any, phone_id: UUID, inspector_type: str) -> bool:
    """Round 8-4A.1 — the ONE place the offline replay identity is defined, so
    the pre-insert idempotency lookup and the post-IntegrityError race recovery
    can't drift. An existing record may stand in for THIS request only when its
    plot, access phone, AND inspector type all match. The client_submission_id
    (the key) is NOT part of identity — the caller already looked up by it; this
    guards against a key that resolves to a record of a different inspection."""
    return (
        existing.plot_id == plot.id
        and existing.plot_access_phone_id == phone_id
        and existing.inspector_type == inspector_type
    )


def _is_client_submission_unique_violation(exc: BaseException) -> bool:
    """Round 8-4A.1 — True iff `exc` is (or wraps, anywhere in its cause/
    context/orig chain) a unique violation on uq_records_client_submission_id.

    Verified against the REAL runtime shape (a live partial-unique-index
    violation on this DB): a SQLAlchemy IntegrityError whose `.orig.__cause__`
    is an ``asyncpg.exceptions.UniqueViolationError`` carrying
    ``constraint_name``. The walk is defensive — it follows __cause__,
    __context__ AND .orig from every node, bounded by an id-set to avoid
    cycles — and matches ONLY the exact index name, so a DIFFERENT constraint
    (an FK, a CHECK, another unique index) is never misclassified as an
    idempotency conflict."""
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "constraint_name", None) == _CLIENT_SUBMISSION_UNIQUE_INDEX:
            return True
        stack.append(getattr(current, "__cause__", None))
        stack.append(getattr(current, "__context__", None))
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            stack.append(orig)
    return False


class _DuplicateSubmission(Exception):
    """Raised inside _finish_creating_record when the offline idempotency key's
    partial-unique index rejects the insert — i.e. a concurrent request won the
    race for the same client_submission_id between our idempotency lookup and
    our insert. Carries the winner's receipt so the endpoint can return it (200)
    instead of a 500, and (for the with-photos flow) clean up the loser's
    just-saved orphan photos first."""

    def __init__(self, receipt: "Any") -> None:
        super().__init__("duplicate offline submission")
        self.receipt = receipt


def _receipt_from_record(record: Any, plot: Any, supplier: Any) -> PublicRecordCreateResult:
    """Build the public receipt from an ALREADY-created record (idempotent
    replay / race loser). plot & supplier are the freshly-resolved rows, whose
    id/code/name match this record by construction (its plot_id/supplier were
    verified against the same token)."""
    return PublicRecordCreateResult(
        id=record.id,
        plot_id=plot.id,
        plot_code=plot.plot_code,
        plot_name=plot.name,
        supplier_id=supplier.id,
        supplier_code=supplier.code,
        supplier_name=supplier.name,
        record_date=record.record_date,
        submitted_by_name=record.submitted_by_name,
        created_at=record.created_at,
        client_submission_id=record.client_submission_id,
        captured_at=record.captured_at,
    )


def _validate_capture_window(captured_at: datetime.datetime) -> None:
    """Reject a captured_at that's too far in the future (clock abuse) or older
    than the max draft age (an expired draft). captured_at is already tz-aware
    (PublicRecordCreate rejects a naive value). Both are 422 with a structured
    code so the frontend can tell them apart; neither leaks anything."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if captured_at > now + _MAX_CAPTURE_CLOCK_SKEW:
        raise HTTPException(status_code=422, detail={"code": _ERR_CAPTURED_AT_INVALID})
    if captured_at < now - _MAX_DRAFT_AGE:
        raise HTTPException(status_code=422, detail={"code": _ERR_OFFLINE_DRAFT_EXPIRED})


def _extract_phone_binding(claims: dict) -> tuple[UUID, str]:
    """Read the REQUIRED phone binding off an inspection token. Round 8-3G
    retired the legacy inspection-code public flow entirely — every
    inspection_session_token now originates from
    POST /public/inspection-access/select-plot and always carries both
    plot_access_phone_id and inspector_type. No legacy fallback: a token
    missing either claim, an inspector_type off the allowlist, or a
    malformed id is rejected with the same generic 401 as any other bad
    token, never distinguished from "expired"/"garbage"."""
    raw_phone_id = claims.get("plot_access_phone_id")
    raw_inspector = claims.get("inspector_type")
    if raw_phone_id is None or raw_inspector is None:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
    if raw_inspector not in INSPECTOR_TYPES:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
    try:
        phone_id = UUID(str(raw_phone_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)
    return phone_id, raw_inspector


async def _decode_and_resolve_plot(
    db: AsyncSession, payload: PublicRecordCreate
) -> tuple[Any, Any, UUID, Any]:
    """Decode + validate the inspection_session_token and resolve its (plot,
    supplier) — WITHOUT resolving the active cycle yet. Returns
    (plot, supplier, token_plot_cycle_id, phone_binding). Split out of
    _verify_and_resolve (round 8-4A) so the offline flow can run the
    idempotency lookup BETWEEN this and the cycle resolution/guard — a replay
    of an already-committed record must succeed even after a rollover, which
    the cycle guard below would otherwise reject.

    401 for any token problem (malformed, expired, wrong type, malformed
    claims) — doesn't distinguish which, same generic-failure principle as
    elsewhere. 404 "Plot not found" if the token's plot/supplier no longer
    exist, aren't active, or the plot isn't owned by the token's supplier —
    the same generic message throughout.
    """
    try:
        claims = decode_inspection_session_token(payload.inspection_session_token)
    except JWTError:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    if claims.get("type") != "inspection_session":
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    try:
        token_plot_id = UUID(str(claims["plot_id"]))
        token_supplier_id = UUID(str(claims["supplier_id"]))
        # Round 8-0.6: fail closed on a token with no plot_cycle_id claim
        # (pre-8-0.6 token) or a malformed one — same generic 401 as any
        # other bad-claim case, KeyError included.
        token_plot_cycle_id = UUID(str(claims["plot_cycle_id"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL)

    # Round 8-3B (made REQUIRED in round 8-3G): every token must carry
    # plot_access_phone_id + inspector_type — the legacy inspection-code flow
    # that used to mint tokens without them is retired. Fail closed on either
    # missing, off-allowlist, or malformed. The raw phone is NEVER a claim;
    # only the access-row id is.
    phone_binding = _extract_phone_binding(claims)

    # Tightest scope the existing RLS vocabulary supports for a caller with
    # no user — see set_public_record_rls_context's docstring for why this
    # alone isn't sufficient and what closes the gap below.
    await set_public_record_rls_context(db, token_supplier_id)

    supplier = await supplier_repo.get_supplier(db, token_supplier_id)
    plot = await plot_repo.get_plot(db, token_plot_id)
    if (
        supplier is None or not supplier.is_active
        or plot is None or not plot.is_active
        or plot.supplier_id != token_supplier_id
    ):
        raise HTTPException(status_code=404, detail="Plot not found")

    return plot, supplier, token_plot_cycle_id, phone_binding


async def _resolve_active_cycle_bound(
    db: AsyncSession, plot: Any, token_plot_cycle_id: UUID
) -> Any:
    """Resolve the plot's active planting cycle and enforce the round-8-0.6
    token↔cycle binding. Generic 404 (never leaks "between cycles" vs
    "rolled over") in both cases:
      * no active cycle at all (round 7.1), or
      * the active cycle's id no longer matches the token's plot_cycle_id
        claim (a rollover/close happened between mint and submit) — never
        silently retargeted at the new cycle; the caller must re-verify.
    Resolved BEFORE any photo write in the with-photos flow (disk-oracle
    protection — see the endpoints below)."""
    active_cycle = await plot_cycle_repo.get_active_cycle_for_plot(db, plot.id)
    if active_cycle is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    if active_cycle.id != token_plot_cycle_id:
        raise HTTPException(status_code=404, detail="Plot not found")
    return active_cycle


async def _verify_and_resolve(db: AsyncSession, payload: PublicRecordCreate) -> tuple[Any, Any, Any, Any]:
    """Online path (unchanged): decode the token and resolve it to a live
    (plot, supplier, active_cycle, phone_binding). Composition of
    _decode_and_resolve_plot + _resolve_active_cycle_bound — kept as one call
    so the online create flow and its many existing tests behave identically.
    The offline flow does NOT call this; it interleaves the idempotency lookup
    between the two helpers (see _resolve_or_replay)."""
    plot, supplier, token_plot_cycle_id, phone_binding = await _decode_and_resolve_plot(db, payload)
    active_cycle = await _resolve_active_cycle_bound(db, plot, token_plot_cycle_id)
    return plot, supplier, active_cycle, phone_binding


async def _resolve_or_replay(db: AsyncSession, payload: PublicRecordCreate) -> tuple:
    """Front door for both endpoints (round 8-4A). Returns either:
      ("replay", receipt)                       → an idempotent replay; the
                                                   caller returns it with 200.
      ("create", plot, supplier, cycle, phone_binding) → proceed to create (201).

    ONLINE (no client_submission_id): behaves exactly as before —
    _verify_and_resolve, then always "create". No idempotency, no extra query.

    OFFLINE (client_submission_id present) security order (Part D):
      1. decode token + phone binding + plot/supplier active
      2. re-check the access row is still active
      3. if a record already exists for this key:
           - identity (plot/access-phone/inspector) matches → replay receipt
           - identity mismatch → 409 idempotency_conflict (generic)
      4. validate the captured_at window (422)
      5. resolve the active cycle + round-8-0.6 token guard (404)
      6. captured_plot_cycle_id must equal the resolved active cycle
         (else 409 planting_cycle_changed) — a NEW submission after a rollover
         is blocked, but a replay (step 3) already short-circuited above.
    """
    if payload.client_submission_id is None:
        plot, supplier, active_cycle, phone_binding = await _verify_and_resolve(db, payload)
        return ("create", plot, supplier, active_cycle, phone_binding)

    plot, supplier, token_plot_cycle_id, phone_binding = await _decode_and_resolve_plot(db, payload)
    phone_id, inspector_type = phone_binding

    # 2. Re-check the access row is still active before doing anything else with
    #    the key (a revoked phone can't even replay). Generic 404 if gone.
    access_row = await phone_repo.get_access_row_for_plot_from_ids(db, [phone_id], plot.id)
    if access_row is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    # 3. Idempotency: does a record already exist for this key?
    existing = await record_repo.get_record_by_client_submission_id(
        db, payload.client_submission_id
    )
    if existing is not None:
        # Identity must match — same plot, same access phone, same inspector
        # type (central helper, round 8-4A.1). A key reused with a different
        # identity is a generic 409 that never reveals which plot/phone the key
        # actually belongs to (Part D.5).
        if not _matches_replay_identity(existing, plot, phone_id, inspector_type):
            raise HTTPException(status_code=409, detail={"code": _ERR_IDEMPOTENCY_CONFLICT})
        # Matching replay — return the original receipt. NO cycle check (works
        # even after a later rollover, Part D.5), NO snapshot sync, NO photos.
        return ("replay", _receipt_from_record(existing, plot, supplier))

    # 4. New offline submission — the captured time must be within the window.
    _validate_capture_window(payload.captured_at)

    # 5. Resolve the active cycle + round-8-0.6 token guard.
    active_cycle = await _resolve_active_cycle_bound(db, plot, token_plot_cycle_id)

    # 6. Consistency guard: the cycle the draft was captured under must still be
    #    the plot's active cycle. If the plot rolled over since capture, block
    #    with a structured 409 — never move the draft into the new cycle.
    if payload.captured_plot_cycle_id != active_cycle.id:
        raise HTTPException(status_code=409, detail={"code": _ERR_PLANTING_CYCLE_CHANGED})

    return ("create", plot, supplier, active_cycle, phone_binding)


async def _recheck_plot_credential(db: AsyncSession, locked_plot, token: str) -> None:
    """Round 8-9C — under the Plot lock, confirm the token's credential binding
    still matches the plot's live ACTIVE credential.

    No-op unless PUBLIC_PLOT_PASSWORD_ENFORCEMENT is on, so the round-8-3B
    record path is byte-for-byte unchanged while the flag is false.

    Fails closed with the SAME generic 404 the rest of this module uses when:
      * the token carries no credential binding (minted before enforcement, or
        by a path that never verified a password)
      * the binding is malformed
      * the plot has no active credential any more
      * the credential row is a different one, or its version moved (i.e. the
        password was changed after select-plot)
    """
    if not get_settings().PUBLIC_PLOT_PASSWORD_ENFORCEMENT:
        return
    try:
        claims = decode_inspection_session_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN_DETAIL) from None

    raw_id = claims.get("plot_access_credential_id")
    raw_version = claims.get("plot_access_credential_version")
    if raw_id is None or not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise HTTPException(status_code=404, detail="Plot not found")
    try:
        credential_id = UUID(str(raw_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Plot not found") from None

    credential = await credential_repo.get_active_credential_by_plot_id(db, locked_plot.id)
    if (
        credential is None
        or credential.id != credential_id
        or credential.credential_version != raw_version
    ):
        raise HTTPException(status_code=404, detail="Plot not found")


async def _finish_creating_record(
    db: AsyncSession,
    payload: PublicRecordCreate,
    plot: Any,
    supplier: Any,
    cycle: Any,
    *,
    photo_urls: list[str] | None = None,
    submitted_ip: str | None = None,
    phone_binding: tuple[UUID, str],
) -> PublicRecordCreateResult:
    """Shared by both the JSON and multipart (with-photos) public create
    endpoints, once the token has already been verified by
    _verify_and_resolve (which also resolved the plot's active `cycle`).
    `photo_urls`, when given, overrides whatever `payload` itself carried —
    same "server-derived wins" principle as plot_id/supplier_id, which also
    never come from the client body.

    Round 8.0.5 — re-locks the active cycle here, immediately before the
    insert: _verify_and_resolve resolved `cycle` earlier, deliberately BEFORE
    any photo was saved to disk (see its docstring), which leaves a window
    where the plot's active cycle could close/rollover while a multipart
    upload is in flight. Re-checking under a row lock (same transaction as
    the record insert + plot snapshot sync below) closes that window; a
    caller with a now-stale `cycle` gets the same generic 404 the rest of
    this flow uses. HTTPException is an Exception, so the with-photos
    endpoint's `except Exception: cleanup_photos(...)` wrapper still deletes
    any photos already written to disk when this rejects.

    Round 8.0.7 — re-locks the PLOT first (get_plot_for_update), before the
    cycle lock above ever runs, and revalidates it's still active and still
    owned by the token's supplier — the same TOCTOU window (photo upload in
    flight) could otherwise let a plot get deactivated between
    _verify_and_resolve and this insert. Plot-before-PlotCycle, same order as
    every other write path this round.
    """
    locked_plot = await plot_repo.get_plot_for_update(db, plot.id)
    if (
        locked_plot is None or not locked_plot.is_active
        or locked_plot.supplier_id != supplier.id
    ):
        raise HTTPException(status_code=404, detail="Plot not found")

    locked_cycle = await plot_cycle_repo.get_active_cycle_for_plot_for_update(db, locked_plot.id)
    if locked_cycle is None or locked_cycle.id != cycle.id:
        raise HTTPException(status_code=404, detail="Plot not found")

    # Round 8-8A — same derivation as the logged-in flow (records._create_
    # record), using this SAME just-relocked cycle (no extra query). A
    # legacy client that sends only yieldPct is untouched. Round 8-8B.1 —
    # over 150% is a real, storable result (raises nothing); only a result
    # over MAX_STORABLE_YIELD_PCT (9999.9%, the column's own NUMERIC(5,1)
    # capacity) or a target/quantity that breaks the technical contract
    # raises BEFORE the phone lock below or any insert.
    try:
        yield_derivation = yield_calculation.derive_yield(
            yield_quantity_kg=payload.yield_quantity_kg,
            client_yield_pct=payload.yield_pct,
            expected_yield_full=locked_cycle.expected_yield_full,
            expected_yield_unit=locked_cycle.expected_yield_unit,
        )
    except YieldValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Phone binding (required, round 8-3B; no legacy fallback since round
    # 8-3G) — re-resolve + LOCK the access phone LAST in the Plot →
    # PlotCycle → PlotAccessPhone order, immediately before the insert. The
    # row must still be active AND belong to this plot; if the access phone
    # was deactivated (assignment revoked) between select-plot and submit,
    # the same generic 404. The phone is NEVER read from the client — the
    # snapshot comes from this server-side row's phone_normalized/access_type.
    phone_id, inspector_type = phone_binding
    access_row = await phone_repo.get_access_row_for_plot_from_ids(
        db, [phone_id], locked_plot.id, for_update=True,
    )
    if access_row is None:
        raise HTTPException(status_code=404, detail="Plot not found")

    # Round 8-9C — plot-password recheck, LAST, still under the Plot lock taken
    # above. Credential writes acquire that same Plot lock first (see
    # plot_access_credential_repository.set_or_replace_plot_credential), so
    # holding it here means the row we read cannot change underneath us; no
    # extra lock is needed and the Plot → PlotCycle → PlotAccessPhone order is
    # unchanged.
    #
    # If the plot's password was changed or disabled between select-plot and
    # this submit, the token's credential binding no longer matches and the
    # record is refused with the SAME generic 404 as a revoked phone — never
    # "the password was changed", which would confirm the plot exists.
    await _recheck_plot_credential(db, locked_plot, payload.inspection_session_token)

    # Offline-only fields (round 8-4A) are NOT RecordCreate fields — they're
    # passed to create_record as separate server-validated kwargs below, never
    # folded into the inspection payload.
    body = payload.model_dump(exclude={
        "inspection_session_token",
        "client_submission_id", "captured_at", "captured_plot_cycle_id",
    })
    if photo_urls is not None:
        body["photo_urls"] = photo_urls

    # crop/variety/planting_date are planting-cycle MASTER data (round 20.2,
    # now sourced from the ACTIVE CYCLE round 7.1) — PublicRecordCreate has no
    # such fields for a client to supply at all; snapshot them here from the
    # freshly re-locked active cycle (not the earlier-resolved `cycle` — the
    # cycle's own plan could have been edited in the gap), never from user
    # input.
    body["crop"] = locked_cycle.crop
    body["variety"] = locked_cycle.variety
    body["planting_date"] = locked_cycle.planting_date
    # Round 8-8A — overwrite with the server-derived values (yield_
    # target_kg_snapshot is NOT a PublicRecordCreate field; passed to
    # record_repo.create_record as its own keyword-only arg below).
    body["yield_pct"] = yield_derivation.yield_pct
    body["yield_quantity_kg"] = yield_derivation.yield_quantity_kg

    try:
        record_payload = RecordCreate(plot_id=locked_plot.id, supplier_id=supplier.id, **body)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # Same server-side protocol snapshot + score contract as the logged-in
    # create path (records._create_record) — one source of truth for the
    # rule, reading the same admin-editable config. A client-supplied snapshot
    # in custom_fields is stripped, not trusted.
    protocol_map = await protocol_service.get_protocol_map(db)
    try:
        record_payload = protocol_service.apply_protocol_snapshot(record_payload, protocol_map)
    except ProtocolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    system_user = await get_external_submission_user(db)
    if system_user is None:
        raise HTTPException(status_code=500, detail="Public submission is not configured")

    create_kwargs = dict(
        recorded_by_id=system_user.id, submitted_ip=submitted_ip,
        plot_cycle_id=locked_cycle.id,
        # Round 8-3B server-derived phone snapshot — always present now that
        # every token is phone-bound (round 8-3G).
        plot_access_phone_id=access_row.id,
        submitted_phone_snapshot=access_row.phone_normalized,
        submitted_phone_type=access_row.access_type,
        inspector_type=phone_binding[1],
        # Offline idempotency key + capture time (round 8-4A). Both NULL for an
        # online submission (client_submission_id is None), so create_record
        # behaves exactly as before for the online path.
        client_submission_id=payload.client_submission_id,
        captured_at=payload.captured_at,
        # Round 8-8A — server-derived comparison target, never a client field.
        yield_target_kg_snapshot=yield_derivation.yield_target_kg_snapshot,
    )

    if payload.client_submission_id is not None:
        # Offline insert — the partial-unique index on client_submission_id is
        # the final race backstop (Part D.8): a concurrent request that won the
        # race between our idempotency lookup above and this insert makes this
        # one raise IntegrityError. The savepoint keeps the outer transaction
        # usable so we can re-read the winner afterwards.
        try:
            async with db.begin_nested():
                record = await record_repo.create_record(db, record_payload, **create_kwargs)
        except IntegrityError as exc:
            # Round 8-4A.1 — classify BEFORE assuming a duplicate key. A failure
            # on any OTHER constraint (FK, CHECK, a different unique index) is a
            # genuine integrity error and must propagate unchanged so get_db
            # rolls the whole request back — never masqueraded as a 409/200.
            if not _is_client_submission_unique_violation(exc):
                raise
            # Our idempotency index rejected the insert — a concurrent request
            # won the race for this key. Re-read the winner UNDER THE SAME RLS
            # context (never bypass RLS, never widen scope to 'all').
            winner = await record_repo.get_record_by_client_submission_id(
                db, payload.client_submission_id
            )
            # The winner must exist AND share this request's identity before it
            # can stand in as an idempotent replay. If RLS hides it (winner is
            # None) or its identity differs, that's a generic 409 — NEVER a 500,
            # and never a receipt mixing the winner's id with this request's
            # plot/supplier (round 8-4A.1 Part 6).
            if winner is None or not _matches_replay_identity(
                winner, locked_plot, phone_id, inspector_type
            ):
                raise HTTPException(
                    status_code=409, detail={"code": _ERR_IDEMPOTENCY_CONFLICT}
                ) from exc
            raise _DuplicateSubmission(
                _receipt_from_record(winner, locked_plot, supplier)
            ) from exc
    else:
        record = await record_repo.create_record(db, record_payload, **create_kwargs)

    await db.refresh(record, attribute_names=["created_at"])
    await plot_repo.sync_current_status_from_record(db, record)

    return PublicRecordCreateResult(
        id=record.id,
        plot_id=locked_plot.id,
        plot_code=locked_plot.plot_code,
        plot_name=locked_plot.name,
        supplier_id=supplier.id,
        supplier_code=supplier.code,
        supplier_name=supplier.name,
        record_date=record.record_date,
        submitted_by_name=record.submitted_by_name,
        created_at=record.created_at,
        client_submission_id=record.client_submission_id,
        captured_at=record.captured_at,
    )


@router.post(
    "/records",
    response_model=PublicRecordCreateResult,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_record_public(
    payload: PublicRecordCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    response: Response = None,
) -> PublicRecordCreateResult:
    outcome = await _resolve_or_replay(db, payload)
    if outcome[0] == "replay":
        # Idempotent replay — the record already existed (round 8-4A, Part D.3).
        if response is not None:
            response.status_code = status.HTTP_200_OK
        return outcome[1]

    _, plot, supplier, cycle, phone_binding = outcome
    try:
        return await _finish_creating_record(
            db, payload, plot, supplier, cycle,
            submitted_ip=get_client_ip(request), phone_binding=phone_binding,
        )
    except _DuplicateSubmission as dup:
        # Lost the concurrent-insert race for this idempotency key — return the
        # winner's receipt with 200, not a 500 (Part D.8/D.10).
        if response is not None:
            response.status_code = status.HTTP_200_OK
        return dup.receipt


@router.post(
    "/records/with-photos",
    response_model=PublicRecordCreateResult,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def create_record_with_photos_public(
    request: Request,
    payload: str = Form(..., description="PublicRecordCreate fields, JSON-encoded"),
    photos: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    response: Response = None,
) -> PublicRecordCreateResult:
    """Multipart variant of POST /api/v1/public/records for the real
    field-inspection flow, carrying 1-5 photos (photos became optional
    later - a zero-photo submit uses the plain JSON endpoint instead). Still gated by
    inspection_session_token exactly as before — the token now travels
    inside the JSON-encoded `payload` Form field (same PublicRecordCreate
    shape, including `inspectionSessionToken`) rather than as a separate
    field, so this stays a single source of truth for that schema.

    Token verification + offline idempotency/cycle validation all run BEFORE
    photos are read/saved (round 8-4A, Part G) — an invalid token, an
    idempotent replay, or a cycle conflict never causes a disk write, and a
    replay in particular never re-saves the draft's photos. Round 8-14A makes
    that ordering matter more, not less: image decoding/re-encoding is the
    most expensive work either endpoint does, and a replay skips all of it.

    Round 8-14A — each photo may be up to 15 MiB on the wire, but what gets
    stored is a normalized JPEG of at most 2 MiB: EXIF-rotated, downscaled to
    2560px on its longest edge, and stripped of all metadata (EXIF/GPS/ICC).
    This is the SAME `validate_and_save_photos` the logged-in endpoint uses —
    a field worker's photo is sanitized exactly like an admin's.
    """
    try:
        body = PublicRecordCreate.model_validate_json(payload)
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    # Idempotency/token/access/cycle validation BEFORE any photo touches disk.
    outcome = await _resolve_or_replay(db, body)
    if outcome[0] == "replay":
        # A replay never saves photos (Part G) — no storage call was made.
        if response is not None:
            response.status_code = status.HTTP_200_OK
        return outcome[1]

    _, plot, supplier, cycle, phone_binding = outcome
    storage = get_photo_storage(plot_code=plot.plot_code)
    urls = await validate_and_save_photos(photos, storage)

    try:
        return await _finish_creating_record(
            db, body, plot, supplier, cycle,
            photo_urls=urls, submitted_ip=get_client_ip(request),
            phone_binding=phone_binding,
        )
    except _DuplicateSubmission as dup:
        # Lost the concurrent-insert race for this key AFTER photos were saved —
        # those photos are now orphans (the winner has its own). Clean them up
        # (same best-effort double guard as below) and return the winner's
        # receipt with 200, never a 500.
        try:
            await cleanup_photos(urls, storage)
        except Exception:
            pass
        if response is not None:
            response.status_code = status.HTTP_200_OK
        return dup.receipt
    except Exception:
        # round 13.1: photos are already on disk at this point — if the DB
        # step fails, remove them rather than leaving orphans. Best-effort in
        # two layers: cleanup_photos already swallows its own per-file
        # errors, and this outer guard additionally ensures that even a bug
        # inside cleanup_photos itself can never replace the original error
        # below — that's always what gets raised.
        try:
            await cleanup_photos(urls, storage)
        except Exception:
            pass
        raise
