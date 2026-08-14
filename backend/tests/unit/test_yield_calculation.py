"""app/services/yield_calculation.py — round 8-8A. Pure function, no DB, no
mocking needed: exercises every branch of the kg-vs-target contract directly.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.yield_calculation import (
    YieldDerivation,
    YieldValidationError,
    derive_yield,
)


# --- legacy client (no yield_quantity_kg) — pass-through, untouched --------

def test_legacy_client_yield_pct_passes_through_unchanged() -> None:
    result = derive_yield(
        yield_quantity_kg=None,
        client_yield_pct=Decimal("42.5"),
        expected_yield_full=Decimal("1000"),
        expected_yield_unit="kg",
    )
    assert result == YieldDerivation(
        yield_pct=Decimal("42.5"), yield_quantity_kg=None, yield_target_kg_snapshot=None,
    )


def test_legacy_client_none_yield_pct_stays_none() -> None:
    result = derive_yield(
        yield_quantity_kg=None, client_yield_pct=None,
        expected_yield_full=None, expected_yield_unit=None,
    )
    assert result.yield_pct is None


# --- kg-first client: unit conversion --------------------------------------

def test_kg_target_800_of_1000_is_80_percent() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("800"), client_yield_pct=Decimal("999"),
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("80.0")
    assert result.yield_quantity_kg == Decimal("800.00")
    assert result.yield_target_kg_snapshot == Decimal("1000.00")


def test_gram_target_is_converted_to_kg() -> None:
    # 500 g target == 0.5 kg; quantity 0.25 kg → 50%.
    result = derive_yield(
        yield_quantity_kg=Decimal("0.25"), client_yield_pct=None,
        expected_yield_full=Decimal("500"), expected_yield_unit="g",
    )
    assert result.yield_target_kg_snapshot == Decimal("0.50")
    assert result.yield_pct == Decimal("50.0")


def test_ton_target_is_converted_to_kg() -> None:
    # 1 ตัน target == 1000 kg; quantity 250 kg → 25%.
    result = derive_yield(
        yield_quantity_kg=Decimal("250"), client_yield_pct=None,
        expected_yield_full=Decimal("1"), expected_yield_unit="ตัน",
    )
    assert result.yield_target_kg_snapshot == Decimal("1000.00")
    assert result.yield_pct == Decimal("25.0")


# --- boundary values ---------------------------------------------------

def test_quantity_zero_is_zero_percent_not_rejected() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("0"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("0.0")
    assert result.yield_quantity_kg == Decimal("0.00")


def test_quantity_equals_target_is_100_percent() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1000"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("100.0")


def test_quantity_at_exactly_150_percent_passes() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1500"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("150.0")


# Round 8-8B.1 — rewritten (not deleted): 150% used to be a hard reject here;
# real growers reported genuine harvests over plan, so it's now a
# non-blocking WARNING the FRONTEND shows (lib/yield-planning.ts's
# YIELD_WARNING_PCT) — derive_yield itself must never raise for this case
# anymore. Kept the original scenario (1510 kg / 1000 kg target = 151%) so
# this is a direct behavior-change regression test, not a deleted one.
def test_quantity_over_150_percent_no_longer_raises_it_is_a_real_result() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1510"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("151.0")
    assert result.yield_quantity_kg == Decimal("1510")
    assert result.yield_target_kg_snapshot == Decimal("1000.00")


def test_150_point_1_percent_passes() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1501"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("150.1")


def test_200_percent_passes_and_returns_pct_200() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("2000"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("200.0")


def test_500_percent_passes() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("5000"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("500.0")


def test_9999_point_9_percent_passes_at_exactly_the_storage_ceiling() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("99999"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("9999.9")


def test_over_9999_point_9_percent_raises_clean_yield_validation_error() -> None:
    with pytest.raises(YieldValidationError) as exc_info:
        derive_yield(
            yield_quantity_kg=Decimal("100000"), client_yield_pct=None,
            expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
        )
    message = str(exc_info.value)
    # Thai text present — a clear, non-empty message, not a bare code, and
    # never a raw number/DB error echoed back.
    assert any("฀" <= ch <= "๿" for ch in message)
    assert "9999" not in message
    assert "Decimal" not in message
    assert "psycopg" not in message.lower()


# --- non-comparable targets: quantity kept, pct/snapshot null, never fake 100% --

@pytest.mark.parametrize(
    "expected_yield_full,expected_yield_unit",
    [
        (None, "kg"),               # no target at all
        (Decimal("0"), "kg"),       # target resolves to <= 0
        (Decimal("-5"), "kg"),      # negative target
        (Decimal("1000"), "ผล"),    # count unit, not weight
        (Decimal("1000"), "ลัง"),   # container unit, not weight
        (Decimal("1000"), "unknown_unit"),
        (Decimal("1000"), None),    # no unit at all
    ],
)
def test_non_comparable_target_keeps_quantity_but_nulls_pct_and_snapshot(
    expected_yield_full, expected_yield_unit,
) -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("123.45"), client_yield_pct=Decimal("999"),
        expected_yield_full=expected_yield_full, expected_yield_unit=expected_yield_unit,
    )
    assert result.yield_quantity_kg == Decimal("123.45")  # stored exactly as given
    assert result.yield_pct is None  # never a faked 100%
    assert result.yield_target_kg_snapshot is None


# --- server overwrites the client's yieldPct whenever quantity is given ----

def test_fake_client_yield_pct_is_overwritten_when_quantity_given() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("500"), client_yield_pct=Decimal("1"),
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_pct == Decimal("50.0")
    assert result.yield_pct != Decimal("1")


def test_fake_client_yield_pct_overwritten_to_none_when_not_comparable() -> None:
    """Even when the target isn't comparable, a supplied quantity still wins
    over the client's yieldPct — the server never falls back to trusting it."""
    result = derive_yield(
        yield_quantity_kg=Decimal("500"), client_yield_pct=Decimal("77"),
        expected_yield_full=None, expected_yield_unit=None,
    )
    assert result.yield_pct is None


# --- quantization -----------------------------------------------------------

def test_quantity_used_as_is_never_re_rounded_by_the_service() -> None:
    """Round 8-8A.1 bug #2 fix, service side: the API boundary (RecordCreate/
    PublicRecordCreate's YieldQuantityKg) is the ONLY place that rejects a
    >2-decimal quantity now — the service must trust and use exactly what it
    is given, never silently re-round it. (A 3-decimal value can only reach
    this pure function via a direct unit-test call, as here — the real
    endpoints never let one past the schema.)"""
    result = derive_yield(
        yield_quantity_kg=Decimal("123.456"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert result.yield_quantity_kg == Decimal("123.456")


def test_target_snapshot_is_quantized_to_two_decimal_places() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1"), client_yield_pct=None,
        expected_yield_full=Decimal("333.333"), expected_yield_unit="kg",
    )
    assert result.yield_target_kg_snapshot.as_tuple().exponent == -2


def test_pct_is_quantized_to_one_decimal_place() -> None:
    result = derive_yield(
        yield_quantity_kg=Decimal("1"), client_yield_pct=None,
        expected_yield_full=Decimal("3"), expected_yield_unit="kg",
    )
    # 1/3 * 100 = 33.333... -> 33.3
    assert result.yield_pct == Decimal("33.3")
    assert result.yield_pct.as_tuple().exponent == -1


# --- cross-check: the derived pct still composes correctly with the -------
# --- pre-existing final-estimate snapshot logic (plot_cycle_repository) ---

async def test_derived_pct_still_composes_with_final_estimate_snapshot() -> None:
    """Round 8-8A must not change what plot_cycle_repository._apply_final_
    estimate_snapshot does with record.yield_pct — it has no idea whether the
    pct came from a legacy client or a kg derivation, and that's the point:
    a kg-derived record closing its cycle produces the exact same
    final_estimated_yield a legacy yieldPct-only record would for the same
    percentage."""
    from types import SimpleNamespace

    from app.repositories.plot_cycle_repository import _apply_final_estimate_snapshot

    derivation = derive_yield(
        yield_quantity_kg=Decimal("800"), client_yield_pct=None,
        expected_yield_full=Decimal("1000"), expected_yield_unit="kg",
    )
    assert derivation.yield_pct == Decimal("80.0")

    record = SimpleNamespace(id="rec-1", yield_pct=derivation.yield_pct)
    cycle = SimpleNamespace(
        final_inspection_record_id=None, final_yield_pct=None,
        final_estimated_yield=None, expected_yield_full=Decimal("1000"),
    )
    _apply_final_estimate_snapshot(cycle, record)

    assert cycle.final_yield_pct == Decimal("80.0")
    # expected_yield_full(1000) * pct(80.0) / 100 = 800.00 — the ESTIMATE
    # column, computed independently of yield_quantity_kg/yield_target_kg_
    # snapshot (which this test never even sets on `record`).
    assert cycle.final_estimated_yield == Decimal("800.00")


# --- round 8-8A.1: precision/boundary hardening regression tests ----------

def test_bug_repro_7g_target_with_0_01kg_quantity_now_gives_100_percent() -> None:
    """The exact bug report: expected_yield_full=7, unit=g -> raw target
    0.007 kg, which quantizes to a stored snapshot of 0.01 kg. Before the
    round 8-8A.1 fix, pct was computed against the RAW 0.007 (142.9%) while
    0.01 was what got stored/displayed as the target — irreproducible from
    the two stored numbers. Now pct is computed from the SAME quantized 0.01
    that gets stored, so quantity(0.01) / target(0.01) * 100 = 100.0%."""
    result = derive_yield(
        yield_quantity_kg=Decimal("0.01"), client_yield_pct=None,
        expected_yield_full=Decimal("7"), expected_yield_unit="g",
    )
    assert result.yield_target_kg_snapshot == Decimal("0.01")
    assert result.yield_quantity_kg == Decimal("0.01")
    assert result.yield_pct == Decimal("100.0")


def test_target_that_rounds_to_zero_kg_is_non_comparable() -> None:
    """1 g target -> raw 0.001 kg, quantizes to 0.00 -> non-comparable
    (round 8-8A.1): quantity is still stored, pct/snapshot both null."""
    result = derive_yield(
        yield_quantity_kg=Decimal("5.00"), client_yield_pct=Decimal("999"),
        expected_yield_full=Decimal("1"), expected_yield_unit="g",
    )
    assert result.yield_quantity_kg == Decimal("5.00")
    assert result.yield_target_kg_snapshot is None
    assert result.yield_pct is None


def test_gram_target_500_with_0_25kg_quantity_still_50_percent() -> None:
    """Regression: the 500 g / 0.25 kg example from round 8-8A must still
    give the same answer after the quantize-before-compare fix (0.5 kg target
    needs no rounding, so it's numerically identical to before)."""
    result = derive_yield(
        yield_quantity_kg=Decimal("0.25"), client_yield_pct=None,
        expected_yield_full=Decimal("500"), expected_yield_unit="g",
    )
    assert result.yield_target_kg_snapshot == Decimal("0.50")
    assert result.yield_pct == Decimal("50.0")


def test_ton_target_1_with_250kg_quantity_still_25_percent() -> None:
    """Regression: the 1 ตัน / 250 kg example from round 8-8A is unaffected
    (1000 kg target needs no rounding either)."""
    result = derive_yield(
        yield_quantity_kg=Decimal("250"), client_yield_pct=None,
        expected_yield_full=Decimal("1"), expected_yield_unit="ตัน",
    )
    assert result.yield_target_kg_snapshot == Decimal("1000.00")
    assert result.yield_pct == Decimal("25.0")


def test_converted_target_over_numeric_capacity_raises_422_before_computing_pct() -> None:
    """An expected_yield_full so large that converting to kg overflows
    NUMERIC(12,2) (e.g. a ตัน plan near the ceiling) must reject with a clear
    Thai message BEFORE any percentage is computed or quantity/target are
    returned for storage — never crash at the DB layer instead."""
    with pytest.raises(YieldValidationError) as exc_info:
        derive_yield(
            yield_quantity_kg=Decimal("100"), client_yield_pct=None,
            expected_yield_full=Decimal("9999999999.99"), expected_yield_unit="ตัน",
        )
    message = str(exc_info.value)
    assert "kg" in message
    assert any("฀" <= ch <= "๿" for ch in message)


def test_converted_target_at_exactly_numeric_capacity_is_accepted() -> None:
    """The boundary itself (9,999,999,999.99 kg, achieved here directly in
    kg so no unit conversion rounding is involved) must NOT raise."""
    result = derive_yield(
        yield_quantity_kg=Decimal("9999999999.99"), client_yield_pct=None,
        expected_yield_full=Decimal("9999999999.99"), expected_yield_unit="kg",
    )
    assert result.yield_target_kg_snapshot == Decimal("9999999999.99")
    assert result.yield_pct == Decimal("100.0")
