import { describe, it, expect } from 'vitest';
import { normalizeThaiMobile, isValidThaiMobile, formatThaiMobile, looksLikePhoneAttempt } from './phone';

describe('normalizeThaiMobile', () => {
  it.each([
    ['0845552162', '0845552162'],
    ['084-555-2162', '0845552162'],
    ['084 555 2162', '0845552162'],
    [' 0845552162 ', '0845552162'],
    ['(084) 555-2162', '0845552162'],
    ['+66845552162', '0845552162'],
    ['66845552162', '0845552162'],
    ['+66 84-555-2162', '0845552162'],
  ])('accepts %s -> %s', (raw, expected) => {
    expect(normalizeThaiMobile(raw)).toBe(expected);
  });

  it.each(['06', '08', '09'])('accepts the %s prefix', (prefix) => {
    const number = `${prefix}12345678`;
    expect(normalizeThaiMobile(number)).toBe(number);
  });

  it.each([
    '0712345678', // invalid prefix
    '084555216', // too short
    '08455521620', // too long
    '084abc2162', // letters
    '', // blank
    '   ', // whitespace only
  ])('rejects %s', (bad) => {
    expect(() => normalizeThaiMobile(bad)).toThrow();
  });

  it('rejects blank with a Thai message', () => {
    expect(() => normalizeThaiMobile('')).toThrow('กรุณากรอกเบอร์โทรศัพท์');
  });

  it('rejects invalid format with a Thai message', () => {
    expect(() => normalizeThaiMobile('0712345678')).toThrow(/เบอร์โทรศัพท์ไม่ถูกต้อง/);
  });

  it('does not silently truncate an over-long number', () => {
    expect(() => normalizeThaiMobile('08455521620')).toThrow();
  });
});

describe('isValidThaiMobile', () => {
  it('returns true for a valid number', () => {
    expect(isValidThaiMobile('084-555-2162')).toBe(true);
  });

  it('returns false for an invalid number, never throws', () => {
    expect(isValidThaiMobile('not-a-phone')).toBe(false);
  });
});

describe('formatThaiMobile', () => {
  it('formats canonical digits as 3-3-4', () => {
    expect(formatThaiMobile('0845552162')).toBe('084-555-2162');
  });

  it('returns non-canonical input unchanged rather than throwing', () => {
    expect(formatThaiMobile('084-555-2162')).toBe('084-555-2162');
    expect(formatThaiMobile('abc')).toBe('abc');
  });
});

// Round 8-17A.2 Part D — decides whether the Plots search box's applied
// search should go through the secure POST phone endpoint or the plain GET
// q text-search path.
describe('looksLikePhoneAttempt', () => {
  it.each([
    '0845552162',
    '084-555-2162',
    '084 555 2162',
    '+66845552162',
    '66845552162',
    '099123', // too short — still phone-shaped, must be caught as an attempt
    '08999999999999', // too long — same
  ])('treats %s as a phone attempt', (value) => {
    expect(looksLikePhoneAttempt(value)).toBe(true);
  });

  it.each([
    'SUP001-P001',
    'แปลงทดสอบ',
    'เชียงใหม่',
    'P001',
    '',
    '   ',
  ])('does not treat %s as a phone attempt', (value) => {
    expect(looksLikePhoneAttempt(value)).toBe(false);
  });
});
