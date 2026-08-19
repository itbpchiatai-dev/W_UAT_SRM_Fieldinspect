import { describe, it, expect } from 'vitest';
import {
  computeCurrentExpectedYield,
  computeInitialYieldValue,
  describeFinalEstimate,
  describeYieldPlanGap,
  formatYieldFormula,
  formatYieldQuantity,
  isYieldPlanComplete,
  MAX_STORABLE_YIELD_PCT,
  pctToQuantityKg,
  quantityKgToPct,
  targetToKg,
  validateYieldQuantityKg,
  YIELD_WARNING_PCT,
} from './yield-planning';

describe('YIELD_WARNING_PCT / MAX_STORABLE_YIELD_PCT (round 8-8B.1)', () => {
  it('warning threshold is 150', () => {
    expect(YIELD_WARNING_PCT).toBe(150);
  });

  it('storage ceiling matches the NUMERIC(5,1) column capacity (migration 0045)', () => {
    expect(MAX_STORABLE_YIELD_PCT).toBe(9999.9);
  });
});

describe('computeCurrentExpectedYield', () => {
  it('computes base 1 kg + 50% = 0.5', () => {
    expect(computeCurrentExpectedYield(1, 50)).toBe(0.5);
  });

  it('computes base 1000 kg + 80% = 800', () => {
    expect(computeCurrentExpectedYield(1000, 80)).toBe(800);
  });

  it('handles Decimal-serialized-as-string inputs the same as numbers', () => {
    expect(computeCurrentExpectedYield('1000.00', '80.0')).toBe(800);
  });

  it('returns null when either input is missing', () => {
    expect(computeCurrentExpectedYield(null, 80)).toBeNull();
    expect(computeCurrentExpectedYield(1000, null)).toBeNull();
    expect(computeCurrentExpectedYield(undefined, undefined)).toBeNull();
  });

  it('returns null instead of NaN for unparseable input', () => {
    expect(computeCurrentExpectedYield('not-a-number', 80)).toBeNull();
  });
});

describe('formatYieldQuantity', () => {
  it('formats a value with its unit', () => {
    expect(formatYieldQuantity(800, 'kg')).toBe('800 kg');
  });

  it('omits the unit suffix when unit is null', () => {
    expect(formatYieldQuantity(800, null)).toBe('800');
  });

  it('rounds to at most 2 decimals', () => {
    expect(formatYieldQuantity(0.5, 'kg')).toBe('0.5 kg');
  });

  it('returns null (not "NaN") for null/invalid input', () => {
    expect(formatYieldQuantity(null, 'kg')).toBeNull();
    expect(formatYieldQuantity('garbage', 'kg')).toBeNull();
  });
});

describe('isYieldPlanComplete', () => {
  it('is true when both plantCount and expectedYieldFull are positive', () => {
    expect(isYieldPlanComplete(500, 1000)).toBe(true);
    expect(isYieldPlanComplete(500, '1000.00')).toBe(true);
  });

  it('is false when either is missing', () => {
    expect(isYieldPlanComplete(null, 1000)).toBe(false);
    expect(isYieldPlanComplete(500, null)).toBe(false);
    expect(isYieldPlanComplete(null, null)).toBe(false);
  });

  it('treats zero the same as missing', () => {
    expect(isYieldPlanComplete(0, 1000)).toBe(false);
    expect(isYieldPlanComplete(500, 0)).toBe(false);
  });
});

describe('describeYieldPlanGap', () => {
  it('returns null when the base plan is complete', () => {
    expect(describeYieldPlanGap(500, 1000)).toBeNull();
  });

  it('names plant count when only that is missing', () => {
    expect(describeYieldPlanGap(null, 1000)).toBe('ยังไม่ระบุจำนวนต้น/จำนวนปลูก');
    expect(describeYieldPlanGap(0, 1000)).toBe('ยังไม่ระบุจำนวนต้น/จำนวนปลูก');
  });

  it('names expected yield when only that is missing', () => {
    expect(describeYieldPlanGap(500, null)).toBe('ยังไม่ระบุ Expected Yield ที่ 100%');
    expect(describeYieldPlanGap(500, 0)).toBe('ยังไม่ระบุ Expected Yield ที่ 100%');
  });

  it('gives a generic message when both are missing', () => {
    expect(describeYieldPlanGap(null, null)).toBe('ยังไม่ตั้งแผนผลผลิต');
  });
});

describe('formatYieldFormula', () => {
  it('describes 80% of 1,000 kg = 800 kg', () => {
    expect(formatYieldFormula(1000, 80, 'kg')).toBe('80% ของ 1,000 kg = 800 kg');
  });

  it('describes 50% of 1 kg = 0.5 kg', () => {
    expect(formatYieldFormula(1, 50, 'kg')).toBe('50% ของ 1 kg = 0.5 kg');
  });

  it('returns null when there is no current yield % yet (no inspection)', () => {
    expect(formatYieldFormula(1000, null, 'kg')).toBeNull();
  });

  it('returns null when there is no base expected yield', () => {
    expect(formatYieldFormula(null, 80, 'kg')).toBeNull();
  });
});

describe('describeFinalEstimate (round 8-2.8B)', () => {
  it('harvested with a snapshot → "ผลผลิตประมาณการสุดท้าย" + quantity (%)', () => {
    const d = describeFinalEstimate({
      cycleStatus: 'harvested', finalEstimatedYield: '800.00', finalYieldPct: '80.0', expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('value');
    if (d.kind === 'value') {
      expect(d.label).toBe('ผลผลิตประมาณการสุดท้าย');
      expect(d.text).toBe('800 kg (80%)');
    }
  });

  it('cancelled with a snapshot → "ประมาณการล่าสุดก่อนยกเลิก"', () => {
    const d = describeFinalEstimate({
      cycleStatus: 'cancelled', finalEstimatedYield: '405.00', finalYieldPct: '45.0', expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('value');
    if (d.kind === 'value') {
      expect(d.label).toBe('ประมาณการล่าสุดก่อนยกเลิก');
      expect(d.text).toBe('405 kg (45%)');
    }
  });

  it('active → hint pointing to the current-yield tab, never a final value', () => {
    const d = describeFinalEstimate({
      cycleStatus: 'active', finalEstimatedYield: null, finalYieldPct: null, expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('active');
    if (d.kind === 'active') expect(d.hint).toContain('ยังไม่ปิดรอบ');
  });

  it('closed with a NULL snapshot → "ไม่มีข้อมูลประมาณการตอนปิดรอบ"', () => {
    const d = describeFinalEstimate({
      cycleStatus: 'harvested', finalEstimatedYield: null, finalYieldPct: null, expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('none');
    if (d.kind === 'none') expect(d.message).toBe('ไม่มีข้อมูลประมาณการตอนปิดรอบ');
  });

  it('reads the estimate verbatim — never recomputes from expected × pct', () => {
    // finalEstimatedYield deliberately does NOT equal expected×pct; the helper
    // must echo the stored 999, not compute 800.
    const d = describeFinalEstimate({
      cycleStatus: 'harvested', finalEstimatedYield: '999.00', finalYieldPct: '80.0', expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('value');
    if (d.kind === 'value') expect(d.text).toBe('999 kg (80%)');
  });

  it('estimate present but pct null → shows only the quantity', () => {
    const d = describeFinalEstimate({
      cycleStatus: 'harvested', finalEstimatedYield: '500.00', finalYieldPct: null, expectedYieldUnit: 'kg',
    });
    expect(d.kind).toBe('value');
    if (d.kind === 'value') expect(d.text).toBe('500 kg');
  });

  it('never emits the phrase "ผลผลิตจริง" (actual yield) for any state', () => {
    const states = ['active', 'harvested', 'cancelled'] as const;
    for (const s of states) {
      const d = describeFinalEstimate({
        cycleStatus: s, finalEstimatedYield: '800.00', finalYieldPct: '80.0', expectedYieldUnit: 'kg',
      });
      expect(JSON.stringify(d)).not.toContain('ผลผลิตจริง');
    }
  });
});

// --- round 8-8B: kg <-> percentage conversion (mirrors backend 8-8A/8-8A.1) -

describe('targetToKg', () => {
  it('kg unit: value unchanged', () => {
    expect(targetToKg(1000, 'kg')).toBe(1000);
  });

  it('g unit: divided by 1000', () => {
    expect(targetToKg(500, 'g')).toBe(0.5);
  });

  it('ตัน unit: multiplied by 1000', () => {
    expect(targetToKg(1, 'ตัน')).toBe(1000);
  });

  it('7 g target rounds to 0.01 kg (round 8-8A.1 precision fix)', () => {
    expect(targetToKg(7, 'g')).toBe(0.01);
  });

  it('1 g target rounds to 0.00 -> non-comparable (null)', () => {
    expect(targetToKg(1, 'g')).toBeNull();
  });

  it('null expectedYieldFull -> null', () => {
    expect(targetToKg(null, 'kg')).toBeNull();
  });

  it('null/undefined unit -> null', () => {
    expect(targetToKg(1000, null)).toBeNull();
    expect(targetToKg(1000, undefined)).toBeNull();
  });

  it('ผล (count unit) -> null, never comparable', () => {
    expect(targetToKg(1000, 'ผล')).toBeNull();
  });

  it('ลัง (container unit) -> null, never comparable', () => {
    expect(targetToKg(1000, 'ลัง')).toBeNull();
  });

  it('unrecognised unit string -> null', () => {
    expect(targetToKg(1000, 'unknown_unit')).toBeNull();
  });

  it('zero expectedYieldFull -> null (target resolves to 0)', () => {
    expect(targetToKg(0, 'kg')).toBeNull();
  });

  it('negative expectedYieldFull -> null (target resolves to <= 0)', () => {
    expect(targetToKg(-5, 'kg')).toBeNull();
  });

  it('accepts Decimal-serialized-as-string input, same as numbers', () => {
    expect(targetToKg('1000.00', 'kg')).toBe(1000);
  });

  // Round 8-25B — the unit is free text on every write path, so "KG" is as
  // likely as "kg". Before the fix the all-lowercase KG_FACTOR lookup missed
  // it and the target silently read as non-comparable (no error, no target,
  // no percentage) even though the plot page still displayed "150 KG".
  // Mirrors backend test_yield_calculation.py's case/whitespace tests.
  it.each(['KG', 'Kg', 'kG', ' kg ', '\tKG\n'])(
    'matches the kg unit regardless of case/space: %j',
    (unit) => {
      expect(targetToKg(1000, unit)).toBe(1000);
    },
  );

  it.each([' g', 'G', 'ตัน ', ' ตัน'])(
    'normalizes the other weight units the same way: %j',
    (unit) => {
      expect(targetToKg(1000, unit)).not.toBeNull();
    },
  );

  it.each(['ผล', 'ลัง', ' ผล ', 'PIECES', 'unknown_unit'])(
    'never turns a non-weight unit into a comparable one: %j',
    (unit) => {
      expect(targetToKg(1000, unit)).toBeNull();
    },
  );
});

describe('quantityKgToPct', () => {
  it('800 / 1000 kg target = 80%', () => {
    expect(quantityKgToPct(800, 1000)).toBe(80);
  });

  it('0 kg = 0%', () => {
    expect(quantityKgToPct(0, 1000)).toBe(0);
  });

  it('quantity equals target = 100%', () => {
    expect(quantityKgToPct(1000, 1000)).toBe(100);
  });

  it('quantity 1.5x target = 150%', () => {
    expect(quantityKgToPct(1500, 1000)).toBe(150);
  });

  it('rounds to 1 decimal place', () => {
    // 1/3 * 100 = 33.333... -> 33.3
    expect(quantityKgToPct(1, 3)).toBe(33.3);
  });

  it('null quantity -> null', () => {
    expect(quantityKgToPct(null, 1000)).toBeNull();
  });

  it('null target -> null (never divides by nothing)', () => {
    expect(quantityKgToPct(800, null)).toBeNull();
  });

  it('target <= 0 -> null (never divides by zero/negative)', () => {
    expect(quantityKgToPct(800, 0)).toBeNull();
    expect(quantityKgToPct(800, -5)).toBeNull();
  });

  it('never returns NaN/Infinity for a non-finite quantity', () => {
    expect(quantityKgToPct(Infinity, 1000)).toBeNull();
    expect(quantityKgToPct(NaN, 1000)).toBeNull();
  });
});

describe('pctToQuantityKg', () => {
  it('80% of a 1000 kg target = 800 kg', () => {
    expect(pctToQuantityKg(80, 1000)).toBe(800);
  });

  it('0% = 0 kg', () => {
    expect(pctToQuantityKg(0, 1000)).toBe(0);
  });

  it('100% = the full target', () => {
    expect(pctToQuantityKg(100, 1000)).toBe(1000);
  });

  it('150% = 1.5x the target', () => {
    expect(pctToQuantityKg(150, 1000)).toBe(1500);
  });

  it('rounds to 2 decimal places', () => {
    // 33.33% of 3 kg = 0.9999 -> 1.00
    expect(pctToQuantityKg(33.33, 3)).toBe(1);
  });

  it('null pct -> null', () => {
    expect(pctToQuantityKg(null, 1000)).toBeNull();
  });

  it('null/non-positive target -> null', () => {
    expect(pctToQuantityKg(80, null)).toBeNull();
    expect(pctToQuantityKg(80, 0)).toBeNull();
  });

  it('is the inverse of quantityKgToPct for a round-trip value', () => {
    const target = 1000;
    const pct = quantityKgToPct(800, target);
    expect(pctToQuantityKg(pct, target)).toBe(800);
  });
});

describe('validateYieldQuantityKg', () => {
  const TARGET = 1000;

  it('null quantity -> no error (field is optional)', () => {
    expect(validateYieldQuantityKg(null, TARGET)).toBeNull();
  });

  it('a normal in-range value -> no error', () => {
    expect(validateYieldQuantityKg(800, TARGET)).toBeNull();
  });

  it('negative quantity -> Thai error', () => {
    expect(validateYieldQuantityKg(-1, TARGET)).toMatch(/ติดลบ/);
  });

  it('more than 2 decimal places -> Thai error, never silently rounded', () => {
    expect(validateYieldQuantityKg(123.456, TARGET)).toMatch(/ทศนิยม/);
  });

  it('exactly 2 decimal places -> no error', () => {
    expect(validateYieldQuantityKg(123.45, TARGET)).toBeNull();
  });

  it('over the NUMERIC(12,2) ceiling -> Thai error', () => {
    expect(validateYieldQuantityKg(99999999999.99, TARGET)).toMatch(/มากเกินไป/);
  });

  it('at exactly the NUMERIC(12,2) ceiling -> no error (if not over the % storage ceiling)', () => {
    // A huge target so 9,999,999,999.99 doesn't also trip the 9999.9% check.
    expect(validateYieldQuantityKg(9999999999.99, 99999999999)).toBeNull();
  });

  // --- round 8-8B.1: 150% relaxed to a non-blocking warning threshold -----

  it('quantity resolving to over 150% (151%) of target -> NO error anymore (real, storable result)', () => {
    expect(validateYieldQuantityKg(1510, TARGET)).toBeNull();
  });

  it('quantity resolving to 150.1% -> no error', () => {
    expect(validateYieldQuantityKg(1501, TARGET)).toBeNull();
  });

  it('quantity resolving to exactly 150% -> no error (unchanged)', () => {
    expect(validateYieldQuantityKg(1500, TARGET)).toBeNull();
  });

  it('quantity resolving to 200%/500% -> no error', () => {
    expect(validateYieldQuantityKg(2000, TARGET)).toBeNull();
    expect(validateYieldQuantityKg(5000, TARGET)).toBeNull();
  });

  it('quantity resolving to exactly 9999.9% (the storage ceiling) -> no error', () => {
    expect(validateYieldQuantityKg(99999, TARGET)).toBeNull();
  });

  it('quantity resolving to over 9999.9% -> Thai blocking error', () => {
    expect(validateYieldQuantityKg(100000, TARGET)).toMatch(/เกินขอบเขต/);
  });

  it('no comparable target -> the %-ceiling check never applies (only decimals/negative/kg-overflow still do)', () => {
    expect(validateYieldQuantityKg(999999, null)).toBeNull();
    expect(validateYieldQuantityKg(-1, null)).toMatch(/ติดลบ/);
  });
});

describe('computeInitialYieldValue', () => {
  it('comparable target + a latest pct -> kg = target * pct / 100 (Part D.1)', () => {
    const result = computeInitialYieldValue(1000, 'kg', 80);
    expect(result).toEqual({ quantityKg: 800, yieldPct: 80 });
  });

  it('comparable target, no latest pct -> defaults to the full target / 100% (Part D.2)', () => {
    const result = computeInitialYieldValue(1000, 'kg', null);
    expect(result).toEqual({ quantityKg: 1000, yieldPct: 100 });
  });

  it('currentYieldPct=0 is a real value, not "no history" -> 0 kg / 0%', () => {
    const result = computeInitialYieldValue(1000, 'kg', 0);
    expect(result).toEqual({ quantityKg: 0, yieldPct: 0 });
  });

  it('no comparable target at all -> both null, never a faked 100% (Part D.3 / contract #12)', () => {
    expect(computeInitialYieldValue(null, null, 80)).toEqual({ quantityKg: null, yieldPct: null });
    expect(computeInitialYieldValue(1000, 'ผล', 80)).toEqual({ quantityKg: null, yieldPct: null });
    expect(computeInitialYieldValue(1000, 'ผล', null)).toEqual({ quantityKg: null, yieldPct: null });
  });

  it('accepts Decimal-serialized-as-string inputs, same as numbers', () => {
    expect(computeInitialYieldValue('1000.00', 'kg', '80.0')).toEqual({ quantityKg: 800, yieldPct: 80 });
  });
});
