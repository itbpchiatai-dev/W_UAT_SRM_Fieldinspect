/**
 * ProgressRow — one labelled bar: a name, a bar, a value, and a note.
 *
 * Used twice on the executive overview and the two uses are why `barPct`
 * and `pct` are separate props:
 *
 *   - the yield funnel draws three bars to a COMMON scale (all against the
 *     target) while each row reports a percentage of a DIFFERENT base
 *     (harvest/target, then cleaned/harvest). One number cannot be both.
 *   - the comparison table draws the bar and the number from the same
 *     achievement figure, and simply passes it twice.
 *
 * Collapsing them into one prop looks tidier right up until a row shows a
 * bar at 90% of the width beside the text "9%".
 */

export type BarTone = 'primary' | 'green' | 'orange' | 'red' | 'blue' | 'purple' | 'muted';

const BAR_TONE: Record<BarTone, string> = {
  primary: 'bg-primary',
  green:   'bg-green-600',
  orange:  'bg-orange-500',
  red:     'bg-red-600',
  blue:    'bg-blue-600',
  purple:  'bg-purple-600',
  muted:   'bg-muted-foreground/40',
};

export interface ProgressRowProps {
  label: string;
  /** Already formatted for display — this component never formats numbers. */
  value: string;
  /** Bar width 0-100. Clamped, so a 125% result draws a full bar instead of
   *  overflowing its container. null draws an empty track. */
  barPct: number | null;
  /** Shown at the end of the row. Usually a percentage against a stated
   *  base ("77.5% ของเป้า"). */
  note?: string;
  /** Second line under the row — where "ยังไม่กรอก N" lives. */
  hint?: string;
  tone?: BarTone;
}

export function clampBar(pct: number | null): number {
  if (pct === null || !Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(100, pct));
}

export function ProgressRow({
  label, value, barPct, note, hint, tone = 'primary',
}: ProgressRowProps) {
  const width = clampBar(barPct);
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-muted-foreground">{label}</span>
        <span className="text-sm font-semibold tabular-nums">{value}</span>
      </div>
      <div className="mt-1 flex items-center gap-3">
        <div
          className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-label={label}
          aria-valuenow={barPct === null ? undefined : Math.round(barPct)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={`h-full rounded-full ${BAR_TONE[tone]}`}
            style={{ width: `${width}%` }}
          />
        </div>
        {note && (
          <span className="w-32 shrink-0 text-right text-xs text-muted-foreground">
            {note}
          </span>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export default ProgressRow;
