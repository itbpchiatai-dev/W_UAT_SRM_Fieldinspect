/**
 * BarLineChart — bars on the left axis, a line on the right axis, one SVG.
 *
 * Hand-rolled rather than pulling in a charting library: this app already
 * draws its Thailand map as inline SVG (ThailandPlotMap.tsx), the page needs
 * exactly one chart shape, and the smallest chart library still costs more
 * bundle than the whole executive page.
 *
 * The two series answer different questions on purpose — how MUCH work
 * happened (bars) against how GOOD it looked (line) — so they carry
 * independent axes, and the right-hand one is pinned to the score's own
 * 0-10 range instead of being scaled to the data. A score axis that
 * rescales makes a flat 6.8-to-7.0 month look like a cliff.
 */

export interface BarLinePoint {
  /** X-axis label, already formatted for the locale. */
  label: string;
  bar: number;
  line: number | null;
}

export interface BarLineChartProps {
  points: BarLinePoint[];
  barLabel: string;
  lineLabel: string;
  /** Right axis ceiling. Fixed by the caller, never derived from the data. */
  lineMax?: number;
  /** Shown instead of the chart when there is not enough to draw a trend. */
  emptyMessage?: string;
}

const W = 720;
const H = 240;
const PAD = { top: 16, right: 40, bottom: 28, left: 40 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

/** A chart of one point is not a trend — it is a number drawn wide. */
export const MIN_POINTS_FOR_TREND = 2;

/** Nice-ish ceiling so the top gridline is a round number. */
export function barAxisMax(points: BarLinePoint[]): number {
  const peak = Math.max(0, ...points.map((p) => p.bar));
  if (peak <= 0) return 1;
  const step = Math.pow(10, Math.floor(Math.log10(peak)));
  return Math.ceil(peak / step) * step;
}

export function BarLineChart({
  points, barLabel, lineLabel, lineMax = 10, emptyMessage,
}: BarLineChartProps) {
  if (points.length < MIN_POINTS_FOR_TREND) {
    return (
      <div className="flex min-h-[160px] items-center justify-center rounded-lg border border-dashed bg-card px-6 py-10 text-center">
        <p className="text-sm text-muted-foreground">
          {emptyMessage ?? 'ข้อมูลยังไม่พอวาดแนวโน้ม'}
        </p>
      </div>
    );
  }

  const maxBar = barAxisMax(points);
  const slot = PLOT_W / points.length;
  const barW = Math.max(4, slot * 0.55);
  const x = (i: number) => PAD.left + slot * i + slot / 2;
  const yBar = (v: number) => PAD.top + PLOT_H - (v / maxBar) * PLOT_H;
  const yLine = (v: number) => PAD.top + PLOT_H - (Math.min(v, lineMax) / lineMax) * PLOT_H;

  // The line is drawn only across points that HAVE a score. A month with no
  // scored inspection leaves a gap rather than a straight line pretending
  // the score held steady through it.
  const segments: string[] = [];
  let run: string[] = [];
  points.forEach((p, i) => {
    if (p.line === null) {
      if (run.length > 1) segments.push(run.join(' '));
      run = [];
      return;
    }
    run.push(`${x(i)},${yLine(p.line)}`);
  });
  if (run.length > 1) segments.push(run.join(' '));

  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-primary" />
          {barLabel}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-orange-500" />
          {lineLabel}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${barLabel} และ ${lineLabel} ${points.length} ช่วงเวลา`}
      >
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1={PAD.left} x2={W - PAD.right}
            y1={PAD.top + PLOT_H * f} y2={PAD.top + PLOT_H * f}
            className="stroke-border" strokeWidth={1}
          />
        ))}
        <text x={4} y={PAD.top + 4} className="fill-muted-foreground text-[10px]">
          {maxBar}
        </text>
        <text x={W - PAD.right + 6} y={PAD.top + 4} className="fill-muted-foreground text-[10px]">
          {lineMax}
        </text>
        <text x={W - PAD.right + 6} y={PAD.top + PLOT_H} className="fill-muted-foreground text-[10px]">
          0
        </text>

        {points.map((p, i) => (
          <rect
            key={`bar-${p.label}-${i}`}
            x={x(i) - barW / 2}
            y={yBar(p.bar)}
            width={barW}
            height={Math.max(0, PAD.top + PLOT_H - yBar(p.bar))}
            rx={2}
            className="fill-primary"
          >
            <title>{`${p.label} · ${barLabel} ${p.bar}`}</title>
          </rect>
        ))}

        {segments.map((pts) => (
          <polyline
            key={pts.slice(0, 24)}
            points={pts}
            fill="none"
            className="stroke-orange-500"
            strokeWidth={2}
            strokeLinejoin="round"
          />
        ))}
        {points.map((p, i) => p.line === null ? null : (
          <circle
            key={`dot-${p.label}-${i}`}
            cx={x(i)} cy={yLine(p.line)} r={3}
            className="fill-orange-500"
          >
            <title>{`${p.label} · ${lineLabel} ${p.line}`}</title>
          </circle>
        ))}

        {points.map((p, i) => (
          <text
            key={`lab-${p.label}-${i}`}
            x={x(i)} y={H - 8}
            textAnchor="middle"
            className="fill-muted-foreground text-[10px]"
          >
            {p.label}
          </text>
        ))}
      </svg>
    </div>
  );
}

export default BarLineChart;
