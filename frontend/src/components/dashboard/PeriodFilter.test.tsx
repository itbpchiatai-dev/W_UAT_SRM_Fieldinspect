/**
 * PeriodFilter (round 48) — presets plus an explicit from/to window.
 *
 * The behaviour worth pinning is what happens at the SEAM between the two
 * modes: switching back to a preset has to clear the dates, or the page
 * keeps querying a window the highlighted tab does not describe.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PeriodFilter, PRESETS, CUSTOM_LABEL, type PeriodValue } from './PeriodFilter';

const PRESET_VALUE: PeriodValue = { range: '3m', dateFrom: null, dateTo: null };
const CUSTOM_VALUE: PeriodValue = {
  range: 'custom', dateFrom: '2026-06-01', dateTo: '2026-08-31',
};

describe('PeriodFilter — the options', () => {
  it('offers the three presets the backend accepts, and no "ทั้งหมด"', () => {
    // backend: dashboard_repository.EXECUTIVE_RANGES, which round 47 cut
    // down to three. "ทั้งหมด" is gone on both sides or on neither.
    expect(PRESETS.map((p) => p.value)).toEqual(['30d', '3m', 'ytd']);
    render(<PeriodFilter value={PRESET_VALUE} onChange={() => {}} />);
    expect(screen.queryByRole('button', { name: 'ทั้งหมด' })).toBeNull();
  });

  it('adds the custom option alongside them', () => {
    render(<PeriodFilter value={PRESET_VALUE} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: CUSTOM_LABEL })).toBeTruthy();
  });

  it('marks only the active preset as pressed', () => {
    render(<PeriodFilter value={{ ...PRESET_VALUE, range: 'ytd' }} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: 'ปีนี้' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '30 วัน' }).getAttribute('aria-pressed')).toBe('false');
  });
});

describe('PeriodFilter — the date inputs', () => {
  it('keeps them hidden until the custom option is chosen', () => {
    render(<PeriodFilter value={PRESET_VALUE} onChange={() => {}} />);
    expect(screen.queryByLabelText('ตั้งแต่วันที่')).toBeNull();
  });

  it('reveals both of them when custom is chosen', () => {
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={() => {}} />);
    expect(screen.getByLabelText('ตั้งแต่วันที่')).toBeTruthy();
    expect(screen.getByLabelText('ถึงวันที่')).toBeTruthy();
  });

  it('shows the dates it was given', () => {
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={() => {}} />);
    expect((screen.getByLabelText('ตั้งแต่วันที่') as HTMLInputElement).value).toBe('2026-06-01');
    expect((screen.getByLabelText('ถึงวันที่') as HTMLInputElement).value).toBe('2026-08-31');
  });

  it('reports a typed start date', () => {
    const onChange = vi.fn();
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('ตั้งแต่วันที่'), { target: { value: '2026-07-01' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ range: 'custom', dateFrom: '2026-07-01' }),
    );
  });

  it('turns a cleared input into null, not an empty string', () => {
    // '' would be sent as a query param and parsed as a bad date; null is
    // omitted, which is what "no bound" has to mean.
    const onChange = vi.fn();
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('ถึงวันที่'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dateTo: null }));
  });

  it('says what to do when custom is picked but nothing is typed yet', () => {
    // Otherwise the server's default window shows and looks like a bug.
    render(<PeriodFilter value={{ range: 'custom', dateFrom: null, dateTo: null }} onChange={() => {}} />);
    expect(screen.getByText(/เลือกวันที่เพื่อกำหนดช่วงเอง/)).toBeTruthy();
  });

  it('drops the hint once a date is given', () => {
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={() => {}} />);
    expect(screen.queryByText(/เลือกวันที่เพื่อกำหนดช่วงเอง/)).toBeNull();
  });
});

describe('PeriodFilter — switching modes', () => {
  it('clears the dates when a preset is picked', () => {
    // THE seam: leaving them set would keep querying June-August while the
    // highlighted tab says "30 วัน".
    const onChange = vi.fn();
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: '30 วัน' }));
    expect(onChange).toHaveBeenCalledWith({ range: '30d', dateFrom: null, dateTo: null });
  });

  it('keeps the dates when custom is re-selected', () => {
    const onChange = vi.fn();
    render(<PeriodFilter value={CUSTOM_VALUE} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: CUSTOM_LABEL }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ dateFrom: '2026-06-01', dateTo: '2026-08-31' }),
    );
  });

  it('un-presses every preset while custom is active', () => {
    render(<PeriodFilter value={{ ...CUSTOM_VALUE }} onChange={() => {}} />);
    for (const p of PRESETS) {
      const label = p.label;
      expect(screen.getByRole('button', { name: label }).getAttribute('aria-pressed')).toBe('false');
    }
    expect(screen.getByRole('button', { name: CUSTOM_LABEL }).getAttribute('aria-pressed')).toBe('true');
  });
});
