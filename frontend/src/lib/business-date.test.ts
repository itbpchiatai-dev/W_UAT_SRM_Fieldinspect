import { describe, it, expect } from 'vitest';
import { bangkokToday } from './business-date';

/**
 * Round 8-19.1 — the app's calendar day is Thailand's. Thailand is UTC+7 with
 * no DST, so a UTC date is wrong for the first seven hours of every Thai day
 * (00:00-06:59 ICT is still the previous UTC date). These pin the boundary the
 * old `new Date().toISOString().slice(0, 10)` got wrong.
 */
describe('bangkokToday', () => {
  it('returns YYYY-MM-DD', () => {
    expect(bangkokToday()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it('16:59 UTC = 23:59 ICT — still the same Thai day', () => {
    expect(bangkokToday(new Date('2026-08-13T16:59:00Z'))).toBe('2026-08-13');
  });

  it('17:00 UTC = 00:00 ICT — the Thai day has rolled over', () => {
    expect(bangkokToday(new Date('2026-08-13T17:00:00Z'))).toBe('2026-08-14');
  });

  it('23:30 UTC = 06:30 ICT next day — the window a UTC date got wrong', () => {
    const moment = new Date('2026-08-13T23:30:00Z');
    // What the replaced pattern would have produced:
    expect(moment.toISOString().slice(0, 10)).toBe('2026-08-13');
    expect(bangkokToday(moment)).toBe('2026-08-14');
  });

  it('00:00 UTC = 07:00 ICT — same date either way', () => {
    expect(bangkokToday(new Date('2026-08-13T00:00:00Z'))).toBe('2026-08-13');
  });

  it('rolls the month and the year at the Thai boundary', () => {
    expect(bangkokToday(new Date('2026-08-31T17:00:00Z'))).toBe('2026-09-01');
    expect(bangkokToday(new Date('2026-12-31T17:00:00Z'))).toBe('2027-01-01');
  });

  it('zero-pads month and day', () => {
    expect(bangkokToday(new Date('2026-01-05T03:00:00Z'))).toBe('2026-01-05');
  });

  it('is a Gregorian (CE) year, not the Thai Buddhist era', () => {
    // The API stores/expects CE; only DISPLAY uses Thai formatting.
    expect(bangkokToday(new Date('2026-08-13T05:00:00Z')).slice(0, 4)).toBe('2026');
  });

  it('ignores the host timezone — the same instant always yields the Thai day', () => {
    const instant = new Date('2026-08-13T18:00:00Z'); // 01:00 ICT, Aug 14
    expect(bangkokToday(instant)).toBe('2026-08-14');
  });

  it('is re-evaluated per call, never frozen at import', () => {
    // The constants this replaced were module-level, so a page open across
    // midnight kept submitting the previous day's date.
    expect(bangkokToday(new Date('2026-08-13T10:00:00Z'))).toBe('2026-08-13');
    expect(bangkokToday(new Date('2026-08-14T10:00:00Z'))).toBe('2026-08-14');
  });
});
