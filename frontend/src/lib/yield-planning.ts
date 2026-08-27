/**
 * Yield-planning calculations (round 17) — "current expected yield" is
 * deliberately not a stored/API field (see backend Plot model docstring);
 * it's computed here from two values the API does send:
 *   expectedYieldFull (plots.expected_yield_full, Decimal-as-string) and
 *   currentYieldPct (plots.current_yield_pct, Decimal-as-string, synced
 *   from the latest inspection record — round 12).
 *
 * Formula: currentExpectedYield = expectedYieldFull * currentYieldPct / 100
 * e.g. 1000 kg base * 80% = 800 kg; 1 kg base * 50% = 0.5 kg.
 */
import { toNumberOrNull } from './numeric';

// Round 19 — a fixed controlled set rather than a new master-data category:
// units rarely change, unlike crop/variety which genuinely grow over time and
// already have an established master-data category. Being a plain <select>
// (not free text/datalist) means no kg/KG/กก. drift is possible by
// construction. Shared by the Plot create/edit form and the cycle
// start/edit modals (round 7.3) — one canonical list.
export const YIELD_UNIT_OPTIONS = ['kg', 'g', 'ตัน', 'ผล', 'ลัง'];

export function computeCurrentExpectedYield(
  expectedYieldFull: string | number | null | undefined,
  currentYieldPct: string | number | null | undefined,
): number | null {
  const full = toNumberOrNull(expectedYieldFull);
  const pct = toNumberOrNull(currentYieldPct);
  if (full == null || pct == null) return null;
  return (full * pct) / 100;
}

/** Formats a yield quantity for display, e.g. `800 kg` — rounds to at most
 * 2 decimals and drops the unit suffix entirely when unit is unset, rather
 * than showing a dangling number with no unit context. */
export function formatYieldQuantity(
  value: number | string | null | undefined,
  unit: string | null | undefined,
): string | null {
  const n = toNumberOrNull(value);
  if (n == null) return null;
  const formatted = n.toLocaleString('th-TH', { maximumFractionDigits: 2 });
  return unit ? `${formatted} ${unit}` : formatted;
}

/**
 * Whether a plot's yield-planning BASE data (plant count + expected yield
 * at 100%) is complete enough to mean something (round 18). Zero counts as
 * "not filled" — a base yield or plant count of 0 has no planning value —
 * same as blank/null.
 */
export function isYieldPlanComplete(
  plantCount: number | null | undefined,
  expectedYieldFull: string | number | null | undefined,
): boolean {
  const full = toNumberOrNull(expectedYieldFull);
  return plantCount != null && plantCount > 0 && full != null && full > 0;
}

/**
 * Single source of truth for "what's missing" messaging across Plot List /
 * Detail / Create-Edit (round 18) — returns null when the base plan is
 * complete, otherwise a short Thai message naming what's missing.
 */
export function describeYieldPlanGap(
  plantCount: number | null | undefined,
  expectedYieldFull: string | number | null | undefined,
): string | null {
  const hasPlantCount = plantCount != null && plantCount > 0;
  const hasFull = toNumberOrNull(expectedYieldFull) != null && (toNumberOrNull(expectedYieldFull) as number) > 0;
  if (hasPlantCount && hasFull) return null;
  if (!hasPlantCount && !hasFull) return 'ยังไม่ตั้งแผนผลผลิต';
  if (!hasPlantCount) return 'ยังไม่ระบุจำนวนต้น/จำนวนปลูก';
  return 'ยังไม่ระบุเป้าผลิต';
}

/**
 * Human-readable formula for the current expected yield, e.g.
 * "80% ของ 1,000 kg = 800 kg" (round 18) — returns null if either input is
 * missing (no base plan, or no inspection yet to source currentYieldPct).
 */
export function formatYieldFormula(
  expectedYieldFull: string | number | null | undefined,
  currentYieldPct: string | number | null | undefined,
  unit: string | null | undefined,
): string | null {
  const full = toNumberOrNull(expectedYieldFull);
  const pct = toNumberOrNull(currentYieldPct);
  if (full == null || pct == null) return null;
  const current = computeCurrentExpectedYield(expectedYieldFull, currentYieldPct);
  return `${pct}% ของ ${formatYieldQuantity(full, unit)} = ${formatYieldQuantity(current, unit)}`;
}

// --- Final ESTIMATED-yield snapshot display (round 8-2.8B) ------------------
// Shared by the Cycle Yield report and Plot Detail's cycle history so the two
// never drift. The value is the frozen close-time ESTIMATE (round 8-2.8A) read
// VERBATIM from the cycle — this helper NEVER recomputes it, only formats it.
// It is never actual harvested yield and is never called "ผลผลิตจริง".

export interface FinalEstimateInput {
  cycleStatus: 'active' | 'harvested' | 'cancelled';
  finalEstimatedYield: string | number | null | undefined;
  finalYieldPct: string | number | null | undefined;
  expectedYieldUnit: string | null | undefined;
}

export type FinalEstimateDisplay =
  // still open — the report points the user to the current-yield tab; Plot
  // Detail renders nothing for an active cycle's history entry.
  | { kind: 'active'; hint: string }
  // closed but no numeric snapshot (pre-8-2.8A close, or no closing inspection).
  | { kind: 'none'; label: string; message: string }
  // closed with a frozen estimate to show.
  | { kind: 'value'; label: string; text: string };

// --- Yield-in-kg / percentage two-way conversion (round 8-8B) --------------
// Mirrors backend/app/services/yield_calculation.py (round 8-8A/8-8A.1)
// EXACTLY: same unit factors, same "non-comparable" rules, same rounding
// (2dp for kg amounts, 1dp for percentages). Plain `number` here — this is a
// UI PREVIEW only; the Backend (Decimal, see yield_calculation.py) is always
// the source of truth for what actually gets stored (contract #10). Every
// function here is null-safe and never returns NaN/Infinity.

const KG_FACTOR: Record<string, number> = { kg: 1, g: 0.001, 'ตัน': 1000 };

/** Trim + lowercase before the KG_FACTOR lookup — the mirror of
 * `_normalize_unit` in backend/app/services/yield_calculation.py (see that
 * function's docstring for the UAT bug this fixes: a cycle whose unit was
 * typed "KG" missed the all-lowercase dict and silently read as
 * "no comparable target"). `toLowerCase()` matches Python's `.lower()`;
 * Thai units have no case and round-trip unchanged. */
function normalizeUnit(unit: string): string {
  return unit.trim().toLowerCase();
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** The cycle's expectedYieldFull converted to kg and rounded to 2dp — the
 * SAME value the backend derives/stores as yieldTargetKgSnapshot (round
 * 8-8A.1 fix: quantize BEFORE the <= 0 check, never divide by a raw
 * unrounded value elsewhere). null when there's nothing comparable: no
 * target/unit at all, a non-weight/unrecognised unit (ผล/ลัง/anything else),
 * or the rounded result is <= 0 (e.g. a 1 g target rounds to 0.00 kg). */
export function targetToKg(
  expectedYieldFull: string | number | null | undefined,
  expectedYieldUnit: string | null | undefined,
): number | null {
  const full = toNumberOrNull(expectedYieldFull);
  if (full == null || !expectedYieldUnit) return null;
  const factor = KG_FACTOR[normalizeUnit(expectedYieldUnit)];
  if (factor == null) return null;
  const target = round2(full * factor);
  return target > 0 ? target : null;
}

/** quantity/target * 100, rounded to 1dp — null-safe (missing quantity, or
 * a target that isn't comparable/positive, both yield null; never NaN). */
export function quantityKgToPct(
  quantityKg: number | null | undefined,
  targetKg: number | null | undefined,
): number | null {
  if (quantityKg == null || targetKg == null || targetKg <= 0) return null;
  if (!Number.isFinite(quantityKg)) return null;
  return round1((quantityKg / targetKg) * 100);
}

/** pct/100 * target, rounded to 2dp — the inverse of quantityKgToPct, used
 * when the user drags the percentage slider instead of typing kg directly. */
export function pctToQuantityKg(
  pct: number | null | undefined,
  targetKg: number | null | undefined,
): number | null {
  if (pct == null || targetKg == null || targetKg <= 0) return null;
  if (!Number.isFinite(pct)) return null;
  return round2((pct / 100) * targetKg);
}

/** The initial (quantityKg, yieldPct) pair a new-record form should open
 * with (round 8-8B Part D), given the active cycle's target and the plot's
 * latest inspection-derived yieldPct (if any):
 *   1. Comparable target + a latest pct → kg = target * latestPct / 100,
 *      pct = latestPct (rounded to 1dp) — the value came from the most
 *      recent real inspection, not a guess.
 *   2. Comparable target, no latest pct (never inspected yet) → quantity
 *      defaults to the FULL target (100%) — same legacy-compatible starting
 *      point the old flat-100% slider used.
 *   3. No comparable target at all → BOTH null. Never a faked 100% — the
 *      caller must show the slider disabled with the "no kg target" note. */
export interface InitialYieldValue {
  quantityKg: number | null;
  yieldPct: number | null;
}

// NUMERIC(12,2) ceiling (backend migration 0044 / round 8-8A.1) — the same
// boundary RecordCreate/PublicRecordCreate's YieldQuantityKg type enforces
// server-side; checked here too so the client can block BEFORE a round trip.
const MAX_KG_VALUE = 9999999999.99;

// Round 8-8B.1 — real growers reported genuine harvests over 150% of plan,
// so 150% is now a non-blocking WARNING threshold only (YieldQuantityInput
// shows an amber notice past this point, never a form error) — mirrors
// backend/app/services/yield_calculation.py's YIELD_WARNING_PCT exactly.
export const YIELD_WARNING_PCT = 150;

// The real hard limit: plot_cycles.final_yield_pct / records.yield_pct /
// plots.current_yield_pct are all NUMERIC(5,1) (4 integer digits + 1
// decimal) — migration 0045 widened the CHECK constraint to this exact
// ceiling. Mirrors backend's MAX_STORABLE_YIELD_PCT exactly.
export const MAX_STORABLE_YIELD_PCT = 9999.9;

/** Client-side mirror of the Backend's yieldQuantityKg contract (round
 * 8-8A.1's RecordCreate/PublicRecordCreate boundary + derive_yield's
 * MAX_STORABLE_YIELD_PCT check, round 8-8B.1) — returns a Thai error
 * message, or null when the value is fine to submit. The Backend still
 * re-validates everything itself (contract #10); this only lets the UI
 * block obviously-invalid input immediately instead of waiting for a 422
 * round trip. null quantityKg (nothing typed yet) is never an error — the
 * field is optional. A result over 150% is NEVER an error here anymore —
 * only YieldQuantityInput's own non-blocking warning reacts to it. */
export function validateYieldQuantityKg(
  quantityKg: number | null | undefined,
  targetKg: number | null,
): string | null {
  if (quantityKg == null) return null;
  if (!Number.isFinite(quantityKg)) return 'ปริมาณผลผลิตไม่ถูกต้อง';
  if (quantityKg < 0) return 'ปริมาณผลผลิตต้องไม่ติดลบ';
  if (quantityKg > MAX_KG_VALUE) return 'ปริมาณผลผลิตมากเกินไป กรุณาตรวจสอบตัวเลข';
  const cents = Math.round(quantityKg * 100);
  if (Math.abs(quantityKg * 100 - cents) > 1e-6) {
    return 'ปริมาณผลผลิตกรอกทศนิยมได้ไม่เกิน 2 ตำแหน่ง';
  }
  const pct = quantityKgToPct(quantityKg, targetKg);
  if (pct != null && pct > MAX_STORABLE_YIELD_PCT) {
    return 'เปอร์เซ็นต์ผลผลิตสูงเกินขอบเขตที่ระบบรองรับ กรุณาตรวจสอบปริมาณผลผลิตและเป้าหมาย';
  }
  return null;
}

export function computeInitialYieldValue(
  expectedYieldFull: string | number | null | undefined,
  expectedYieldUnit: string | null | undefined,
  latestYieldPct: string | number | null | undefined,
): InitialYieldValue {
  const targetKg = targetToKg(expectedYieldFull, expectedYieldUnit);
  if (targetKg == null) {
    return { quantityKg: null, yieldPct: null };
  }
  const latest = toNumberOrNull(latestYieldPct);
  if (latest != null) {
    return { quantityKg: pctToQuantityKg(latest, targetKg), yieldPct: round1(latest) };
  }
  return { quantityKg: targetKg, yieldPct: 100 };
}

export function describeFinalEstimate(input: FinalEstimateInput): FinalEstimateDisplay {
  if (input.cycleStatus === 'active') {
    return { kind: 'active', hint: 'ยังไม่ปิดรอบ — ดูผลผลิตที่คาดว่าจะได้ที่แท็บสถานะแปลง' };
  }
  // harvested → final estimate; cancelled → last estimate before cancel.
  const label = input.cycleStatus === 'cancelled'
    ? 'ประมาณการล่าสุดก่อนยกเลิก'
    : 'ผลผลิตประมาณการสุดท้าย';
  const est = toNumberOrNull(input.finalEstimatedYield);
  const pct = toNumberOrNull(input.finalYieldPct);
  if (est == null && pct == null) {
    return { kind: 'none', label, message: 'ไม่มีข้อมูลประมาณการตอนปิดรอบ' };
  }
  const qty = est != null ? formatYieldQuantity(est, input.expectedYieldUnit) : null;
  const pctText = pct != null ? `${pct}%` : null;
  let text: string;
  if (qty != null && pctText != null) text = `${qty} (${pctText})`;
  else if (qty != null) text = qty;
  else text = pctText as string; // est null but pct present (guarded above)
  return { kind: 'value', label, text };
}
