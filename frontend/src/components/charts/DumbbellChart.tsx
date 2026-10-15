/**
 * DumbbellChart — two dots per row and the gap between them.
 *
 * Built for one question: a P.Code was estimated at X% of plan while it
 * was growing, and finished at Y% once the crop was cleaned and weighed —
 * how far apart are those, and which way?
 *
 * Paired bars would need twice the height and make the eye compare two
 * lengths. A dumbbell puts the comparison in the one place that matters:
 * the distance between two points on a shared axis. Rows arrive already
 * sorted by the caller (worst gap first), so the thing to talk about is
 * the top row.
 *
 * A row with only one value is drawn with one dot and no connector — not
 * a line down to zero, which would assert a result nobody has measured.
 */

export interface DumbbellRow {
  key: string;
  label: string;
  /** % of plan while growing. null when nothing is still in production. */
  estimatePct: number | null;
  /** % of plan once finished. null until something finishes. */
  actualPct: number | null;
}

export interface DumbbellChartProps {
  rows: DumbbellRow[];
  /** Axis ceiling. Fixed by the caller so a good season does not rescale
   *  the chart and flatten everything else. */
  maxPct?: number;
  estimateLabel?: string;
  actualLabel?: string;
  emptyMessage?: string;
}

const ROW_H = 28;
const PAD = { top: 8, right: 16, bottom: 22, left: 128 };
const W = 720;

export function clampPct(pct: number, maxPct: number): number {
  return Math.max(0, Math.min(maxPct, pct));
}

/** Positive = finished BELOW the estimate, which is the direction worth
 *  noticing. null when the pair is incomplete. */
export function gapPoints(row: DumbbellRow): number | null {
  if (row.estimatePct === null || row.actualPct === null) return null;
  return Math.round((row.estimatePct - row.actualPct) * 10) / 10;
}

export function DumbbellChart({
  rows,
  maxPct = 150,
  estimateLabel = 'คาดการณ์ (กำลังผลิต)',
  actualLabel = 'ผลิตได้จริง (หลังคัด)',
  emptyMessage,
}: DumbbellChartProps) {
  if (rows.length === 0) {
    return (
      <div className="flex min-h-[120px] items-center justify-center rounded-lg border border-dashed bg-card px-6 py-8 text-center">
        <p className="text-sm text-muted-foreground">
          {emptyMessage ?? 'ยังไม่มีข้อมูลให้เทียบ'}
        </p>
      </div>
    );
  }

  const h = PAD.top + rows.length * ROW_H + PAD.bottom;
  const plotW = W - PAD.left - PAD.right;
  const x = (pct: number) => PAD.left + (clampPct(pct, maxPct) / maxPct) * plotW;
  const y = (i: number) => PAD.top + i * ROW_H + ROW_H / 2;
  const ticks = [0, maxPct / 3, (maxPct * 2) / 3, maxPct].map(Math.round);

  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-blue-600" />
          {estimateLabel}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rotate-45 bg-green-700" />
          {actualLabel}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${h}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${estimateLabel} เทียบกับ ${actualLabel} ${rows.length} รายการ`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={x(t)} x2={x(t)} y1={PAD.top} y2={h - PAD.bottom}
              className="stroke-border" strokeWidth={1}
            />
            <text
              x={x(t)} y={h - 6} textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {t}%
            </text>
          </g>
        ))}
        {/* 100% of plan — the line everything is really being read against. */}
        <line
          x1={x(100)} x2={x(100)} y1={PAD.top} y2={h - PAD.bottom}
          className="stroke-muted-foreground/50" strokeWidth={1} strokeDasharray="3 3"
        />

        {rows.map((r, i) => {
          const both = r.estimatePct !== null && r.actualPct !== null;
          return (
            <g key={r.key}>
              <text
                x={PAD.left - 8} y={y(i) + 4} textAnchor="end"
                className="fill-foreground text-[11px]"
              >
                {r.label}
              </text>
              {both && (
                <line
                  x1={x(r.estimatePct!)} x2={x(r.actualPct!)}
                  y1={y(i)} y2={y(i)}
                  className="stroke-muted-foreground" strokeWidth={2}
                />
              )}
              {r.estimatePct !== null && (
                <circle cx={x(r.estimatePct)} cy={y(i)} r={5} className="fill-blue-600">
                  <title>{`${r.label} · ${estimateLabel} ${r.estimatePct}%`}</title>
                </circle>
              )}
              {r.actualPct !== null && (
                <rect
                  x={x(r.actualPct) - 4.5} y={y(i) - 4.5}
                  width={9} height={9}
                  transform={`rotate(45 ${x(r.actualPct)} ${y(i)})`}
                  className="fill-green-700"
                >
                  <title>{`${r.label} · ${actualLabel} ${r.actualPct}%`}</title>
                </rect>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default DumbbellChart;
