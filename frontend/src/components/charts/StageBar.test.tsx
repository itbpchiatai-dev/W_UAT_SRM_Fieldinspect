/**
 * StageBar (round 51) — season order is the whole point, and the stage
 * names are master data somebody can rename.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StageBar, sortStages, STAGE_ORDER } from './StageBar';

describe('sortStages', () => {
  it('puts the season in order regardless of how it arrives', () => {
    // The API returns them by count, which would show เก็บเกี่ยว first.
    const sorted = sortStages([
      { stage: 'เก็บเกี่ยว', plots: 33 },
      { stage: 'ระยะงอก', plots: 11 },
      { stage: 'ออกดอก', plots: 19 },
    ]);
    expect(sorted.map((s) => s.stage)).toEqual(['ระยะงอก', 'ออกดอก', 'เก็บเกี่ยว']);
  });

  it('keeps an unknown stage instead of dropping it', () => {
    // growth_stage is master data: an admin can add one tomorrow, and a
    // stage missing from the picture is worse than one out of place.
    const sorted = sortStages([
      { stage: 'ระยะใหม่ที่เพิ่งเพิ่ม', plots: 4 },
      { stage: 'ระยะงอก', plots: 11 },
    ]);
    expect(sorted.map((s) => s.stage)).toEqual(['ระยะงอก', 'ระยะใหม่ที่เพิ่งเพิ่ม']);
  });

  it('orders two unknown stages by size', () => {
    const sorted = sortStages([
      { stage: 'ก', plots: 2 }, { stage: 'ข', plots: 9 },
    ]);
    expect(sorted.map((s) => s.stage)).toEqual(['ข', 'ก']);
  });

  it('does not mutate what it was given', () => {
    const input = [{ stage: 'เก็บเกี่ยว', plots: 1 }, { stage: 'ระยะงอก', plots: 2 }];
    sortStages(input);
    expect(input[0].stage).toBe('เก็บเกี่ยว');
  });

  it('knows the seeded progression', () => {
    expect(STAGE_ORDER).toEqual(['ระยะงอก', 'เจริญเติบโต', 'ออกดอก', 'ติดผล', 'เก็บเกี่ยว']);
  });
});

describe('StageBar', () => {
  const slices = [
    { stage: 'เก็บเกี่ยว', plots: 33 },
    { stage: 'ติดผล', plots: 24 },
    { stage: 'ระยะงอก', plots: 11 },
  ];

  it('draws one segment per stage, widths in proportion', () => {
    const { container } = render(<StageBar slices={slices} />);
    const bar = screen.getByRole('img');
    const segments = Array.from(bar.children) as HTMLElement[];
    expect(segments.length).toBe(3);
    // 11 of 68 plots -> the first (ระยะงอก, by season order) is the narrowest
    const widths = segments.map((s) => parseFloat(s.style.width));
    expect(widths[0]).toBeLessThan(widths[2]);
    expect(Math.round(widths.reduce((a, b) => a + b, 0))).toBe(100);
    expect(container).toBeTruthy();
  });

  it('says how many plots the bar covers', () => {
    render(<StageBar slices={slices} />);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('68 แปลง');
  });

  it('lists each stage with its count under the bar', () => {
    render(<StageBar slices={slices} />);
    expect(screen.getByText(/ระยะงอก 11/)).toBeTruthy();
    expect(screen.getByText(/เก็บเกี่ยว 33/)).toBeTruthy();
  });

  it('ignores a stage with no plots rather than drawing a zero-width sliver', () => {
    render(<StageBar slices={[...slices, { stage: 'ออกดอก', plots: 0 }]} />);
    expect(screen.queryByText(/ออกดอก/)).toBeNull();
  });

  it('says nothing is growing instead of drawing an empty bar', () => {
    render(<StageBar slices={[]} />);
    expect(screen.getByText('ยังไม่มีรอบที่กำลังผลิต')).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('takes a caller message for the empty case', () => {
    render(<StageBar slices={[]} emptyMessage="ไม่มีแปลงในตัวกรองนี้" />);
    expect(screen.getByText('ไม่มีแปลงในตัวกรองนี้')).toBeTruthy();
  });
});
