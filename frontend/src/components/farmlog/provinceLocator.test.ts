import { describe, it, expect, beforeEach } from 'vitest';
import { PROVINCE_SHAPES, VIEW_WIDTH, VIEW_HEIGHT } from './thailandGeo';
import {
  PROVINCE_THAI_TO_SHAPE,
  locateProvince,
  normalizeProvinceName,
  resetProvinceAnchorCacheForTests,
} from './provinceLocator';

beforeEach(() => {
  resetProvinceAnchorCacheForTests();
});

// --- The name table ----------------------------------------------------------
//
// The map and master_data name the same 77 provinces in two different
// languages, and nothing in the repo related them before this round. A missing
// or misspelled entry does not throw — the province simply never appears on
// the map — so the table is held to an exact-set contract here instead.

describe('PROVINCE_THAI_TO_SHAPE', () => {
  it('covers all 77 provinces', () => {
    expect(PROVINCE_THAI_TO_SHAPE.size).toBe(77);
  });

  it('maps onto the map shapes exactly — none missing, none invented', () => {
    const shapeNames = new Set(PROVINCE_SHAPES.map((s) => s.name));
    const mapped = new Set(PROVINCE_THAI_TO_SHAPE.values());
    expect(shapeNames.size).toBe(77);
    expect([...mapped].filter((n) => !shapeNames.has(n))).toEqual([]);
    expect([...shapeNames].filter((n) => !mapped.has(n))).toEqual([]);
  });

  it('never points two provinces at the same shape', () => {
    expect(new Set(PROVINCE_THAI_TO_SHAPE.values()).size).toBe(77);
  });
});

// --- Normalisation -----------------------------------------------------------

describe('normalizeProvinceName', () => {
  it('strips the จ. prefix that the Excel import let through', () => {
    // The exact pair that splits Khon Kaen into two filter entries on UAT.
    expect(normalizeProvinceName('จ.ขอนแก่น')).toBe('ขอนแก่น');
    expect(normalizeProvinceName('ขอนแก่น')).toBe('ขอนแก่น');
  });

  it('strips the spelled-out จังหวัด prefix', () => {
    expect(normalizeProvinceName('จังหวัดเลย')).toBe('เลย');
  });

  it('trims and collapses whitespace', () => {
    expect(normalizeProvinceName('  จ.ขอนแก่น  ')).toBe('ขอนแก่น');
    expect(normalizeProvinceName('ขอน  แก่น')).toBe('ขอน แก่น');
  });

  it('resolves the Bangkok short forms', () => {
    for (const written of ['กรุงเทพ', 'กรุงเทพฯ', 'กทม', 'กทม.']) {
      expect(normalizeProvinceName(written)).toBe('กรุงเทพมหานคร');
    }
  });

  it('returns an empty string for null/undefined/blank', () => {
    expect(normalizeProvinceName(null)).toBe('');
    expect(normalizeProvinceName(undefined)).toBe('');
    expect(normalizeProvinceName('   ')).toBe('');
  });

  it('never guesses at a misspelling', () => {
    // ขอนแกน is missing its tone mark. Correcting it would mean placing a plot
    // on a province nobody typed; leaving it unresolved is the safe outcome.
    expect(normalizeProvinceName('ขอนแกน')).toBe('ขอนแกน');
    expect(locateProvince('ขอนแกน')).toBeNull();
  });
});

// --- Resolution --------------------------------------------------------------

describe('locateProvince', () => {
  it('puts both spellings of one province at the same point', () => {
    const withPrefix = locateProvince('จ.ขอนแก่น');
    const without = locateProvince('ขอนแก่น');
    expect(withPrefix).not.toBeNull();
    expect(withPrefix).toEqual(without);
    expect(withPrefix!.thaiName).toBe('ขอนแก่น');
    expect(withPrefix!.shapeName).toBe('Khon Kaen');
  });

  it('is null for anything it cannot resolve, never a default point', () => {
    expect(locateProvince(null)).toBeNull();
    expect(locateProvince('')).toBeNull();
    expect(locateProvince('Nowhere')).toBeNull();
  });

  it('resolves every province in the table', () => {
    const unresolved = [...PROVINCE_THAI_TO_SHAPE.keys()].filter((th) => locateProvince(th) === null);
    expect(unresolved).toEqual([]);
  });
});

// --- The geometric guarantee -------------------------------------------------
//
// This is the test that matters. A marker placed at the AREA CENTROID of a
// concave province can land outside it — in a neighbouring province, where it
// reads as a data error rather than a geometry one. Re-implemented here from
// the baked paths so the check is independent of the implementation.

function ringsOf(d: string): { x: number; y: number }[][] {
  const rings: { x: number; y: number }[][] = [];
  for (const chunk of d.split('M').slice(1)) {
    const nums = chunk.match(/-?\d+(?:\.\d+)?/g);
    if (!nums || nums.length < 6) continue;
    const ring: { x: number; y: number }[] = [];
    for (let i = 0; i + 1 < nums.length; i += 2) {
      ring.push({ x: Number(nums[i]), y: Number(nums[i + 1]) });
    }
    if (ring.length >= 3) rings.push(ring);
  }
  return rings;
}

function inRing(ring: { x: number; y: number }[], px: number, py: number): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i];
    const b = ring[j];
    if ((a.y > py) !== (b.y > py) && px < ((b.x - a.x) * (py - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

describe('province anchors', () => {
  it('lands inside the province it belongs to — all 77', () => {
    const outside: string[] = [];
    for (const [thai, shapeName] of PROVINCE_THAI_TO_SHAPE) {
      const point = locateProvince(thai)!;
      const shape = PROVINCE_SHAPES.find((s) => s.name === shapeName)!;
      const hit = ringsOf(shape.d).some((ring) => inRing(ring, point.x, point.y));
      if (!hit) outside.push(`${thai} (${shapeName})`);
    }
    expect(outside).toEqual([]);
  });

  it('stays within the drawn viewBox', () => {
    for (const thai of PROVINCE_THAI_TO_SHAPE.keys()) {
      const point = locateProvince(thai)!;
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(VIEW_WIDTH);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(VIEW_HEIGHT);
    }
  });

  it('gives every province its own distinct point', () => {
    const seen = new Set<string>();
    for (const thai of PROVINCE_THAI_TO_SHAPE.keys()) {
      const point = locateProvince(thai)!;
      seen.add(`${point.x.toFixed(3)},${point.y.toFixed(3)}`);
    }
    expect(seen.size).toBe(77);
  });
});
