import { describe, it, expect } from 'vitest';
import { toNumberOrNull, formatFixed } from './numeric';

describe('toNumberOrNull', () => {
  it('passes numbers through unchanged', () => {
    expect(toNumberOrNull(13.7563)).toBe(13.7563);
    expect(toNumberOrNull(0)).toBe(0);
  });

  it('parses well-formed numeric strings (Decimal-serialized-as-string case)', () => {
    expect(toNumberOrNull('13.7563')).toBe(13.7563);
    expect(toNumberOrNull('-100.5018')).toBe(-100.5018);
  });

  it('returns null for null/undefined', () => {
    expect(toNumberOrNull(null)).toBeNull();
    expect(toNumberOrNull(undefined)).toBeNull();
  });

  it('returns null for unparseable strings instead of NaN', () => {
    expect(toNumberOrNull('not-a-number')).toBeNull();
    expect(toNumberOrNull('')).toBeNull();
  });
});

describe('formatFixed', () => {
  it('formats numbers and numeric strings identically', () => {
    expect(formatFixed(13.7563, 6)).toBe('13.756300');
    expect(formatFixed('13.7563', 6)).toBe('13.756300');
  });

  it('returns null (not "NaN") for null or invalid input', () => {
    expect(formatFixed(null, 6)).toBeNull();
    expect(formatFixed('garbage', 6)).toBeNull();
  });
});
