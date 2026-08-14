"""Yield-in-kg calculation — round 8-8A, precision-hardened round 8-8A.1.
Backend is the source of truth for Record.yield_pct once a client sends a kg
quantity: this module is the ONE place both the logged-in
(records._create_record) and public (public_records._finish_creating_record)
create flows call to derive it, comparing the entered quantity against the
active PlotCycle's expected_yield_full/expected_yield_unit target.

Pure — no DB I/O, no commit. Callers pass in an already-resolved (and, on the
write paths, already row-locked) PlotCycle's own expected_yield_full/
expected_yield_unit; this module never queries anything itself.

Decimal only, never float — mirrors plot_cycle_repository's
_apply_final_estimate_snapshot rounding convention (ROUND_HALF_UP, explicit
quantize) so the two "expected × pct" computations in this codebase stay
consistent.

Round 8-8A.1 — two precision bugs fixed:

1. The percentage now ALWAYS divides the two numbers that get STORED
   (yield_quantity_kg, yield_target_kg_snapshot), never a raw/unquantized
   intermediate. Previously the target was quantized only for display/storage
   while the pct math used the raw (unquantized) value — e.g. a 7 g target
   (0.007 kg raw) stored as a 0.01 kg snapshot but computed its percentage
   against 0.007, so "quantity=0.01, target=0.01" implied 100% while the
   stored pct read 142.9% — a snapshot that couldn't be reproduced from its
   own stored numbers. Quantizing the target FIRST (inside _target_kg, before
   the <= 0 / overflow checks) fixes this at the source.
2. yield_quantity_kg is assumed ALREADY boundary-validated by the caller's
   Pydantic schema (RecordCreate/PublicRecordCreate's YieldQuantityKg type:
   ge=0, max_digits=12, decimal_places=2 — app/schemas/record.py) before it
   ever reaches this module: used as-is, never re-rounded here (rounding a
   user-entered quantity here would silently contradict a "reject, don't
   round" input contract). A converted TARGET that overflows the same
   NUMERIC(12,2) capacity (e.g. an enormous expected_yield_full in ตัน) is
   still this module's own responsibility to catch — see _MAX_KG_VALUE below
   — since the target is server-derived, not a client field a schema could
   bound.

Round 8-8B.1 — real growers reported genuine harvests over 150% of plan, so
150% is now a WARNING threshold only (the frontend shows a non-blocking
amber notice at that point — lib/yield-planning.ts) — this module no longer
raises there at all. What it DOES still enforce is MAX_STORABLE_YIELD_PCT
(9999.9%), the actual NUMERIC(5,1) capacity of records.yield_pct/
plots.current_yield_pct/plot_cycles.final_yield_pct (migration 0045) — a
value that can't fit that column must 422 here, before it ever reaches
Postgres as a numeric-overflow error.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_TWO_PLACES = Decimal("0.01")
_ONE_PLACE = Decimal("0.1")

# Round 8-8B.1 — 150% is a WARNING threshold only now (non-blocking; the
# frontend shows an amber notice past this point). NOT enforced by this
# module anymore — kept here, unused in any comparison below, purely so
# both layers document the SAME number in one place a reader can find via a
# repo-wide search for "150".
YIELD_WARNING_PCT = Decimal("150")

# NUMERIC(5,1) storage capacity for yield_pct/current_yield_pct/
# final_yield_pct (migration 0045: 4 integer digits + 1 decimal). This IS
# enforced — see derive_yield below.
MAX_STORABLE_YIELD_PCT = Decimal("9999.9")

# NUMERIC(12,2) capacity (migration 0044) — 10 integer digits + 2 decimal.
# Applies to the TARGET side only (the quantity side is bounded by the
# Pydantic schema itself, round 8-8A.1 Part B).
_MAX_KG_VALUE = Decimal("9999999999.99")

# Units convertible to kg — the weight subset of plot_import's
# _FINAL_YIELD_UNIT_ALLOWLIST ({"kg", "g", "ตัน", "ผล", "ลัง"}). "ผล" (pieces)
# and "ลัง" (crates) are count/container units, not weight — never comparable
# to a kg quantity, same as an unrecognised or missing unit.
_KG_FACTOR: dict[str, Decimal] = {
    "kg": Decimal("1"),
    "g": Decimal("0.001"),
    "ตัน": Decimal("1000"),
}


class YieldValidationError(ValueError):
    """Either the derived percentage exceeds MAX_STORABLE_YIELD_PCT
    (9999.9% — the column's own NUMERIC(5,1) capacity, round 8-8B.1), or the
    target itself (after unit conversion) doesn't fit the kg storage column.
    150% is NOT one of these cases anymore — it's a non-blocking warning the
    frontend shows, never a reason for this module to raise. Callers
    (records._create_record / public_records._finish_creating_record) turn
    this into a 422 with this exception's Thai message verbatim, BEFORE any
    record is inserted."""


@dataclass(frozen=True)
class YieldDerivation:
    """What to write to the record: yield_pct (existing column, headline %),
    yield_quantity_kg and yield_target_kg_snapshot (round 8-8A columns)."""

    yield_pct: Decimal | None
    yield_quantity_kg: Decimal | None
    yield_target_kg_snapshot: Decimal | None


def _target_kg(
    expected_yield_full: Decimal | None, expected_yield_unit: str | None
) -> Decimal | None:
    """The cycle's expected_yield_full converted to kg AND quantized to 2dp
    — the EXACT value that will be stored as yield_target_kg_snapshot and
    used as the divisor below, so the percentage always matches what a human
    could recompute from the two stored numbers later (round 8-8A.1 bug #1).

    None (not comparable) when: no target/unit at all, an unrecognised or
    non-weight unit, or the QUANTIZED result rounds to <= 0.00 kg (e.g. a 1 g
    target: 0.001 raw quantizes to 0.00) — quantizing BEFORE this check, not
    after, is what makes that edge case land here instead of leaking a
    near-zero target into the division below.
    """
    if expected_yield_full is None or expected_yield_unit is None:
        return None
    factor = _KG_FACTOR.get(expected_yield_unit)
    if factor is None:
        return None
    target = (expected_yield_full * factor).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if target <= 0:
        return None
    return target


def derive_yield(
    *,
    yield_quantity_kg: Decimal | None,
    client_yield_pct: Decimal | None,
    expected_yield_full: Decimal | None,
    expected_yield_unit: str | None,
) -> YieldDerivation:
    """The single derivation point for both create flows.

    yield_quantity_kg is None (legacy client — sends only yieldPct) →
    client_yield_pct passes through UNCHANGED; quantity/target snapshot stay
    None. This is the ONLY branch where client_yield_pct survives — legacy
    behavior is untouched.

    yield_quantity_kg is not None (kg-first client, already boundary-
    validated by the caller's schema — see module docstring) →
    client_yield_pct is ALWAYS discarded, even when nothing is comparable
    (server is the source of truth, never a passthrough of a client-computed
    %). If the cycle's target doesn't resolve to a positive, storable kg
    value, yield_pct and the snapshot are both None — the quantity is still
    stored as entered, never papered over with a fake 100%. A target that
    converts to more than NUMERIC(12,2) can hold raises YieldValidationError
    (422) before anything is computed or returned — that plan can't be
    expressed in kg at all, not even as "non-comparable". Otherwise:
        yield_pct = storedQuantityKg / storedTargetKgSnapshot * 100
    quantized to 1dp (ROUND_HALF_UP) — both operands are the exact values
    this function returns for storage, never a raw/unquantized intermediate.
    A genuine harvest over 150% of plan is a REAL, storable result now
    (round 8-8B.1) — never rejected here. Only a result over
    MAX_STORABLE_YIELD_PCT (9999.9%, the column's own capacity) raises
    YieldValidationError before anything is returned — the caller must not
    write a half-derived record, or one Postgres itself would reject as a
    numeric overflow.
    """
    if yield_quantity_kg is None:
        return YieldDerivation(
            yield_pct=client_yield_pct,
            yield_quantity_kg=None,
            yield_target_kg_snapshot=None,
        )

    # Already ge=0/max_digits=12/decimal_places=2 at the schema boundary —
    # used as-is, never re-rounded (round 8-8A.1 bug #2).
    quantity = yield_quantity_kg

    target = _target_kg(expected_yield_full, expected_yield_unit)
    if target is None:
        return YieldDerivation(
            yield_pct=None,
            yield_quantity_kg=quantity,
            yield_target_kg_snapshot=None,
        )

    if target > _MAX_KG_VALUE:
        raise YieldValidationError(
            "แผนผลผลิตไม่สามารถคำนวณเป็น kg ได้ กรุณาตรวจสอบหน่วยและปริมาณที่คาดการณ์ไว้"
        )

    pct = (quantity / target * Decimal("100")).quantize(_ONE_PLACE, rounding=ROUND_HALF_UP)
    # Round 8-8B.1 — 150% is a non-blocking warning (frontend only); a real
    # harvest over plan is a genuine, storable result. Only the column's own
    # NUMERIC(5,1) capacity is a hard limit.
    if pct > MAX_STORABLE_YIELD_PCT:
        raise YieldValidationError(
            "เปอร์เซ็นต์ผลผลิตสูงเกินขอบเขตที่ระบบรองรับ "
            "กรุณาตรวจสอบปริมาณผลผลิตและเป้าหมาย"
        )

    return YieldDerivation(
        yield_pct=pct,
        yield_quantity_kg=quantity,
        yield_target_kg_snapshot=target,
    )
