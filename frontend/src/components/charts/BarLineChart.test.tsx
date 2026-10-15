/**
 * BarLineChart (round 45) — the two decisions that make this chart honest:
 * the score axis does not rescale to its data, and a month with no score
 * leaves a gap instead of a straight line through it.
 *
 * jsdom has no layout, so these assert on the SVG geometry the component
 * computes, which IS the thing under test.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BarLineChart, barAxisMax, MIN_POINTS_FOR_TREND, type BarLinePoint } from './BarLineChart';

function pt(label: string, bar: number, line: number | null): BarLinePoint {
  return { label, bar, line };
}

function renderChart(points: BarLinePoint[], emptyMessage?: string) {
  return render(
    <BarLineChart
      points={points}
      barLabel="จำนวนการตรวจ"
      lineLabel="คะแนนเฉลี่ย"
      emptyMessage={emptyMessage}
    />,
  );
}

describe('barAxisMax', () => {
  it('rounds the ceiling up to a round number', () => {
    expect(barAxisMax([pt('a', 71, null), pt('b', 44, null)])).toBe(80);
    expect(barAxisMax([pt('a', 3, null)])).toBe(3);
    expect(barAxisMax([pt('a', 123, null)])).toBe(200);
  });

  it('never returns zero, so no division by zero reaches the geometry', () => {
    expect(barAxisMax([pt('a', 0, null)])).toBe(1);
    expect(barAxisMax([])).toBe(1);
  });
});

describe('BarLineChart — not enough data', () => {
  it('shows the empty message instead of a chart for a single point', () => {
    // One bar drawn across a whole card is a number pretending to be a
    // trend. UAT currently has 27 days of data and will land here.
    renderChart([pt('ต.ค.', 21, 7.0)], 'ข้อมูลยังไม่พอวาดแนวโน้ม (มี 27 วัน)');
    expect(screen.getByText(/ข้อมูลยังไม่พอวาดแนวโน้ม/)).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('has a default message so the caller can omit one', () => {
    renderChart([]);
    expect(screen.getByText('ข้อมูลยังไม่พอวาดแนวโน้ม')).toBeTruthy();
  });

  it('draws the chart as soon as there are two points', () => {
    expect(MIN_POINTS_FOR_TREND).toBe(2);
    renderChart([pt('ก.ย.', 47, 6.7), pt('ต.ค.', 21, 7.0)]);
    expect(screen.getByRole('img')).toBeTruthy();
  });
});

describe('BarLineChart — the drawing', () => {
  const points = [
    pt('พ.ค.', 71, 5.6), pt('มิ.ย.', 44, 5.7),
    pt('ก.ค.', 59, 8.1), pt('ส.ค.', 55, 7.7),
  ];

  it('draws one bar and one dot per point', () => {
    const { container } = renderChart(points);
    expect(container.querySelectorAll('rect').length).toBe(4);
    expect(container.querySelectorAll('circle').length).toBe(4);
  });

  it('labels every bucket on the x axis', () => {
    renderChart(points);
    for (const p of points) expect(screen.getByText(p.label)).toBeTruthy();
  });

  it('a taller bar is drawn taller', () => {
    const { container } = renderChart(points);
    const [first, second] = Array.from(container.querySelectorAll('rect'));
    expect(Number(first.getAttribute('height'))).toBeGreaterThan(
      Number(second.getAttribute('height')),
    );
  });

  it('pins the score axis to 0-10 instead of scaling it to the data', () => {
    // A month that moved 6.8 -> 7.0 must not look like a cliff. With a fixed
    // axis those two dots sit within a couple of percent of the plot height;
    // with a rescaled one they would be at the floor and the ceiling.
    const { container } = render(
      <BarLineChart
        points={[pt('ก.ย.', 10, 6.8), pt('ต.ค.', 10, 7.0)]}
        barLabel="จำนวนการตรวจ"
        lineLabel="คะแนนเฉลี่ย"
      />,
    );
    const [a, b] = Array.from(container.querySelectorAll('circle'));
    const gap = Math.abs(Number(a.getAttribute('cy')) - Number(b.getAttribute('cy')));
    expect(gap).toBeLessThan(10);
  });

  it('honours a different line ceiling when the caller sets one', () => {
    const { container } = render(
      <BarLineChart
        points={[pt('a', 1, 50), pt('b', 1, 100)]}
        barLabel="x" lineLabel="y" lineMax={100}
      />,
    );
    const [a, b] = Array.from(container.querySelectorAll('circle'));
    expect(Number(a.getAttribute('cy'))).toBeGreaterThan(Number(b.getAttribute('cy')));
  });

  it('breaks the line at a bucket with no score rather than drawing through it', () => {
    // A straight segment across the gap would assert the score held steady
    // through a month nobody scored.
    const { container } = renderChart([
      pt('ก.ค.', 10, 7.0), pt('ส.ค.', 10, 7.2),
      pt('ก.ย.', 10, null),
      pt('ต.ค.', 10, 6.9), pt('พ.ย.', 10, 6.5),
    ]);
    expect(container.querySelectorAll('polyline').length).toBe(2);
    expect(container.querySelectorAll('circle').length).toBe(4);
  });

  it('draws no line at all when nothing was scored', () => {
    const { container } = renderChart([pt('a', 5, null), pt('b', 7, null)]);
    expect(container.querySelectorAll('polyline').length).toBe(0);
    expect(container.querySelectorAll('rect').length).toBe(2);
  });

  it('names both series in the legend and in the accessible label', () => {
    renderChart(points);
    expect(screen.getByText('จำนวนการตรวจ')).toBeTruthy();
    expect(screen.getByText('คะแนนเฉลี่ย')).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('4 ช่วงเวลา');
  });

  it('survives an all-zero series without dividing by zero', () => {
    const { container } = renderChart([pt('a', 0, null), pt('b', 0, null)]);
    for (const rect of Array.from(container.querySelectorAll('rect'))) {
      const h = Number(rect.getAttribute('height'));
      expect(Number.isFinite(h)).toBe(true);
      expect(h).toBeGreaterThanOrEqual(0);
    }
  });
});
