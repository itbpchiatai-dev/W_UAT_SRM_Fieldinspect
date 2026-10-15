/**
 * StageBar — one bar, split by growth stage, in season order.
 *
 * Answers "where in the season is the work?" in a glance. It exists next
 * to the in-production percentage because that number means different
 * things depending on this bar: 90% of plan with most plots still at
 * ระยะงอก is an early guess, while 90% with most at เก็บเกี่ยว is nearly
 * a result.
 *
 * Stage names are master data an admin can rename or extend, so the order
 * is a best-effort list and anything unrecognised keeps its place at the
 * end rather than being dropped — a stage missing from the picture is
 * worse than one in the wrong spot.
 */

export interface StageSlice {
  stage: string;
  plots: number;
}

/** The seeded progression (app/db/seed.py). Anything else sorts after. */
export const STAGE_ORDER = ['ระยะงอก', 'เจริญเติบโต', 'ออกดอก', 'ติดผล', 'เก็บเกี่ยว'];

const TONES = [
  'bg-emerald-200', 'bg-emerald-300', 'bg-emerald-400',
  'bg-emerald-500', 'bg-emerald-600',
];
const FALLBACK_TONE = 'bg-muted-foreground/30';

export function sortStages(slices: StageSlice[]): StageSlice[] {
  return [...slices].sort((a, b) => {
    const ia = STAGE_ORDER.indexOf(a.stage);
    const ib = STAGE_ORDER.indexOf(b.stage);
    if (ia === -1 && ib === -1) return b.plots - a.plots;
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

export interface StageBarProps {
  slices: StageSlice[];
  emptyMessage?: string;
}

export function StageBar({ slices, emptyMessage }: StageBarProps) {
  const ordered = sortStages(slices.filter((s) => s.plots > 0));
  const total = ordered.reduce((sum, s) => sum + s.plots, 0);

  if (total === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {emptyMessage ?? 'ยังไม่มีรอบที่กำลังผลิต'}
      </p>
    );
  }

  return (
    <div>
      <div
        className="flex h-6 w-full overflow-hidden rounded-md"
        role="img"
        aria-label={`ระยะของรอบที่กำลังผลิต ${total} แปลง`}
      >
        {ordered.map((s) => {
          const known = STAGE_ORDER.indexOf(s.stage);
          const tone = known === -1 ? FALLBACK_TONE : TONES[known % TONES.length];
          return (
            <div
              key={s.stage}
              className={tone}
              style={{ width: `${(s.plots / total) * 100}%` }}
              title={`${s.stage} ${s.plots} แปลง`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {ordered.map((s) => {
          const known = STAGE_ORDER.indexOf(s.stage);
          const tone = known === -1 ? FALLBACK_TONE : TONES[known % TONES.length];
          return (
            <span key={s.stage} className="flex items-center gap-1.5">
              <span className={`inline-block h-2.5 w-2.5 rounded-sm ${tone}`} />
              {s.stage} {s.plots.toLocaleString()}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export default StageBar;
