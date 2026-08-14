import { describe, it, expect } from 'vitest';
import {
  projectLngLat,
  isWithinThailand,
  VIEW_WIDTH,
  VIEW_HEIGHT,
  PROVINCE_SHAPES,
} from './thailandGeo';
import { cropColor, UNSPECIFIED_CROP_COLOR } from './cropColor';

describe('thailandGeo projection', () => {
  it('bundles all 77 province shapes with non-empty paths', () => {
    expect(PROVINCE_SHAPES).toHaveLength(77);
    for (const p of PROVINCE_SHAPES) {
      expect(p.d.startsWith('M')).toBe(true);
      expect(p.name.length).toBeGreaterThan(0);
    }
  });

  it('projects a point in Thailand into the viewBox bounds', () => {
    // Chiang Mai-ish (north) sits near the top; Bangkok lower-middle.
    const north = projectLngLat(98.98, 18.79);
    const bangkok = projectLngLat(100.5, 13.75);
    for (const p of [north, bangkok]) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(VIEW_WIDTH);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(VIEW_HEIGHT);
    }
    // Higher latitude → smaller y (SVG y grows downward).
    expect(north.y).toBeLessThan(bangkok.y);
  });

  it('flags out-of-country coordinates', () => {
    expect(isWithinThailand(100.5, 13.75)).toBe(true); // Bangkok
    expect(isWithinThailand(-74, 40.7)).toBe(false); // New York
    expect(isWithinThailand(139.7, 35.7)).toBe(false); // Tokyo
  });
});

describe('cropColor', () => {
  it('is deterministic per crop label', () => {
    expect(cropColor('พริก')).toBe(cropColor('พริก'));
    expect(cropColor('ข้าวโพด')).toBe(cropColor('ข้าวโพด'));
  });

  it('maps null/blank to the neutral unspecified color', () => {
    expect(cropColor(null)).toBe(UNSPECIFIED_CROP_COLOR);
    expect(cropColor('')).toBe(UNSPECIFIED_CROP_COLOR);
    expect(cropColor('   ')).toBe(UNSPECIFIED_CROP_COLOR);
  });

  it('gives different crops (usually) different colors and never the unspecified gray', () => {
    expect(cropColor('พริก')).not.toBe(UNSPECIFIED_CROP_COLOR);
    expect(cropColor('ข้าวโพด')).not.toBe(UNSPECIFIED_CROP_COLOR);
  });
});
