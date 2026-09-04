/**
 * Auto Plot Code display helpers (round B) — the client-side mirror of the
 * backend's services/plot_code.py. These tests exist mostly to pin the two
 * things a mirror can silently get wrong: the era (ค.ศ., not พ.ศ.) and the
 * timezone the month is read in.
 */
import { describe, it, expect } from 'vitest';
import {
  autoPlotCodePreview,
  monthStamp,
  parseLocalDate,
  plotCodeSourceBadge,
} from './plot-code';

describe('monthStamp', () => {
  it('renders YYMM in the Christian era', () => {
    expect(monthStamp(new Date(2026, 4, 17))).toBe('2605');   // May 2026
    expect(monthStamp(new Date(2026, 0, 9))).toBe('2601');    // month padded
    expect(monthStamp(new Date(2026, 10, 30))).toBe('2611');
    expect(monthStamp(new Date(2030, 11, 25))).toBe('3012');
  });

  it('is NOT the Buddhist era', () => {
    // A deliberate, user-confirmed decision: matches the Auto Lot's own
    // examples so a plot and its first cycle's lot agree on what "26" is.
    expect(monthStamp(new Date(2026, 4, 1))).not.toBe('6905');
  });

  it('matches what the backend would render for the same calendar month', () => {
    // plot_code.py: f"{on.year % 100:02d}{on.month:02d}"
    for (const [y, m, expected] of [[2026, 5, '2605'], [2026, 12, '2612'], [2100, 3, '0003']] as const) {
      expect(monthStamp(new Date(y, m - 1, 15))).toBe(expected);
    }
  });
});

describe('parseLocalDate', () => {
  it('reads an <input type="date"> value as a LOCAL date, not UTC', () => {
    const d = parseLocalDate('2026-05-01');
    expect(d).not.toBeNull();
    expect(d!.getFullYear()).toBe(2026);
    expect(d!.getMonth()).toBe(4);      // May, locally — never 30 April
    expect(d!.getDate()).toBe(1);
  });

  it('returns null for blank/malformed input so the caller can fall back', () => {
    for (const bad of [null, undefined, '', '   ', '2026-5-1', 'tomorrow', '20260501']) {
      expect(parseLocalDate(bad)).toBeNull();
    }
  });

  it('rejects an impossible date instead of silently rolling it over', () => {
    // new Date(2026, 1, 31) would quietly become 3 March.
    expect(parseLocalDate('2026-02-31')).toBeNull();
  });
});

describe('autoPlotCodePreview', () => {
  it('renders {supplierCode}-{YYMM}-### for the chosen planting month', () => {
    expect(autoPlotCodePreview('JPS', '2026-05-20')).toBe('JPS-2605-###');
  });

  it('uses the PLANTING month, not today', () => {
    // A plot registered in April for a May planting must read 2605 — that is
    // the season it will be filed under for the rest of its life.
    expect(autoPlotCodePreview('JPS', '2026-05-03')).toBe('JPS-2605-###');
    expect(autoPlotCodePreview('JPS', '2026-11-30')).toBe('JPS-2611-###');
  });

  it('falls back to today when there is no planting date yet', () => {
    expect(autoPlotCodePreview('JPS', null)).toBe(`JPS-${monthStamp(new Date())}-###`);
    expect(autoPlotCodePreview('JPS')).toBe(`JPS-${monthStamp(new Date())}-###`);
  });

  it('upper-cases and trims the supplier code, as the backend stores it', () => {
    expect(autoPlotCodePreview('  jps  ', '2026-05-01')).toBe('JPS-2605-###');
  });

  it('shows a readable placeholder before a supplier is chosen', () => {
    expect(autoPlotCodePreview(null, '2026-05-01')).toBe('<รหัส Supplier>-2605-###');
    expect(autoPlotCodePreview('', '2026-05-01')).toBe('<รหัส Supplier>-2605-###');
  });

  it('never invents a running number', () => {
    // It is allocated server-side, at save, under the supplier's month series.
    expect(autoPlotCodePreview('JPS', '2026-05-01')).toContain('###');
    expect(autoPlotCodePreview('JPS', '2026-05-01')).not.toMatch(/-\d{3}$/);
  });
});

describe('plotCodeSourceBadge', () => {
  it('uses the SAME vocabulary as the lot badge', () => {
    expect(plotCodeSourceBadge('auto')?.label).toBe('อัตโนมัติ');
    expect(plotCodeSourceBadge('manual')?.label).toBe('กรอกเอง');
    expect(plotCodeSourceBadge('legacy')?.label).toBe('ข้อมูลเดิม');
  });

  it('shows nothing for a plot that predates the generator', () => {
    // Nobody CHOSE those codes — tagging them "กรอกเอง" would be a claim the
    // data does not support.
    expect(plotCodeSourceBadge(null)).toBeNull();
    expect(plotCodeSourceBadge(undefined)).toBeNull();
  });
});
