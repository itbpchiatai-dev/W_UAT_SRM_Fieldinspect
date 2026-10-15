/**
 * ProgressRow (round 45) — the bar width and the printed percentage are
 * separate inputs, and that separation is the whole reason this component
 * exists rather than being three divs inline.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProgressRow, clampBar } from './ProgressRow';

describe('clampBar', () => {
  it('keeps an ordinary value', () => {
    expect(clampBar(62)).toBe(62);
  });

  it('caps a result above plan so the bar cannot overflow its track', () => {
    // A supplier at 125% of target is a real result; a bar 125% as wide as
    // its container is a layout bug.
    expect(clampBar(125)).toBe(100);
  });

  it('floors a negative at zero', () => {
    expect(clampBar(-10)).toBe(0);
  });

  it('treats null and NaN as an empty track', () => {
    expect(clampBar(null)).toBe(0);
    expect(clampBar(Number.NaN)).toBe(0);
  });
});

describe('ProgressRow', () => {
  it('draws the bar from barPct while printing a different base in the note', () => {
    // The yield funnel's third row: the bar is 7% of the TARGET scale while
    // the note reports 87.5% of the HARVEST. One prop could not be both,
    // and conflating them would print "87.5%" beside a nearly full bar.
    render(
      <ProgressRow
        label="หลังทำความสะอาด"
        value="28,712 kg"
        barPct={7}
        note="87.5% ของที่เก็บได้"
      />,
    );
    const bar = screen.getByRole('progressbar', { name: 'หลังทำความสะอาด' });
    expect(bar.getAttribute('aria-valuenow')).toBe('7');
    expect(screen.getByText('87.5% ของที่เก็บได้')).toBeTruthy();
    expect(bar.firstElementChild!.getAttribute('style')).toContain('width: 7%');
  });

  it('renders the value exactly as given — it never formats numbers itself', () => {
    render(<ProgressRow label="เป้าผลิต" value="411,525 kg" barPct={100} />);
    expect(screen.getByText('411,525 kg')).toBeTruthy();
  });

  it('shows an empty track and no aria value when there is nothing to show', () => {
    render(<ProgressRow label="หลังทำความสะอาด" value="—" barPct={null} />);
    const bar = screen.getByRole('progressbar', { name: 'หลังทำความสะอาด' });
    expect(bar.getAttribute('aria-valuenow')).toBeNull();
    expect(bar.firstElementChild!.getAttribute('style')).toContain('width: 0%');
  });

  it('renders the hint line when given one', () => {
    render(
      <ProgressRow
        label="หลังทำความสะอาด" value="28,712 kg" barPct={7}
        hint="จาก 12 บันทึก · ยังไม่กรอกอีก 293"
      />,
    );
    expect(screen.getByText(/ยังไม่กรอกอีก 293/)).toBeTruthy();
  });

  it('omits the note and hint when they are not supplied', () => {
    const { container } = render(
      <ProgressRow label="เป้าผลิต" value="411,525 kg" barPct={100} />,
    );
    expect(container.querySelectorAll('p').length).toBe(0);
  });
});
