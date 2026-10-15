/**
 * DumbbellChart (round 51) — the chart exists to show a GAP, so the tests
 * are about what happens when one half of the pair is missing and about
 * the axis not rescaling underneath the reader.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DumbbellChart, clampPct, gapPoints, type DumbbellRow } from './DumbbellChart';

function row(o: Partial<DumbbellRow> = {}): DumbbellRow {
  return { key: 'WM-141', label: 'WM-141', estimatePct: 118, actualPct: 96, ...o };
}

describe('gapPoints', () => {
  it('is positive when the finished result came in below the estimate', () => {
    // The direction worth noticing: we thought 118, we got 96.
    expect(gapPoints(row())).toBe(22);
  });

  it('is negative when the crop beat its estimate', () => {
    expect(gapPoints(row({ estimatePct: 85, actualPct: 91 }))).toBe(-6);
  });

  it('is null while half the pair is missing', () => {
    // Nothing has finished yet — that is not a gap of 118.
    expect(gapPoints(row({ actualPct: null }))).toBeNull();
    expect(gapPoints(row({ estimatePct: null }))).toBeNull();
  });
});

describe('clampPct', () => {
  it('keeps an ordinary value', () => {
    expect(clampPct(96, 150)).toBe(96);
  });

  it('pins a runaway result to the axis instead of drawing off-canvas', () => {
    expect(clampPct(400, 150)).toBe(150);
  });

  it('floors a negative', () => {
    expect(clampPct(-5, 150)).toBe(0);
  });
});

describe('DumbbellChart', () => {
  const rows = [
    row(),
    row({ key: 'CTT-507', label: 'CTT-507', estimatePct: 92, actualPct: 78 }),
  ];

  it('draws a dot pair and a connector per row', () => {
    const { container } = render(<DumbbellChart rows={rows} />);
    expect(container.querySelectorAll('circle').length).toBe(2);
    expect(container.querySelectorAll('rect').length).toBe(2);
    // 4 axis ticks + the 100% marker + 2 connectors
    expect(container.querySelectorAll('line').length).toBe(7);
  });

  it('labels every row', () => {
    render(<DumbbellChart rows={rows} />);
    expect(screen.getByText('WM-141')).toBeTruthy();
    expect(screen.getByText('CTT-507')).toBeTruthy();
  });

  it('draws no connector when only the estimate exists', () => {
    // A line reaching down to zero would claim a measured result.
    const { container } = render(
      <DumbbellChart rows={[row({ actualPct: null })]} />,
    );
    expect(container.querySelectorAll('circle').length).toBe(1);
    expect(container.querySelectorAll('rect').length).toBe(0);
    expect(container.querySelectorAll('line').length).toBe(5); // ticks + 100%
  });

  it('draws the finished dot alone when nothing is still growing', () => {
    const { container } = render(
      <DumbbellChart rows={[row({ estimatePct: null })]} />,
    );
    expect(container.querySelectorAll('circle').length).toBe(0);
    expect(container.querySelectorAll('rect').length).toBe(1);
  });

  it('keeps the axis fixed so one good season does not flatten the rest', () => {
    // Both charts share a ceiling, so a row at 96% sits in the same place
    // whether or not another row reached 131%.
    const alone = render(<DumbbellChart rows={[row()]} />);
    const x1 = alone.container.querySelector('circle')!.getAttribute('cx');
    alone.unmount();
    const withOutlier = render(
      <DumbbellChart rows={[row(), row({ key: 'X', label: 'X', estimatePct: 131 })]} />,
    );
    const x2 = withOutlier.container.querySelector('circle')!.getAttribute('cx');
    expect(x1).toBe(x2);
  });

  it('marks the 100% line, which is what everything is read against', () => {
    const { container } = render(<DumbbellChart rows={rows} />);
    const dashed = Array.from(container.querySelectorAll('line'))
      .filter((l) => l.getAttribute('stroke-dasharray'));
    expect(dashed.length).toBe(1);
  });

  it('explains itself when there is nothing to compare', () => {
    render(<DumbbellChart rows={[]} emptyMessage="ยังไม่มี P.Code ที่เทียบได้" />);
    expect(screen.getByText('ยังไม่มี P.Code ที่เทียบได้')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('names both series for a reader who cannot see the shapes', () => {
    render(<DumbbellChart rows={rows} />);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('2 รายการ');
  });
});
