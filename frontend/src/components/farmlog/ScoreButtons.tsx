/**
 * ScoreButtons — tap-first 1–10 condition-score picker, replacing the old
 * range sliders on the inspection forms: ten number chips plus an explicit
 * "ว่าง" chip that clears the score back to null (= ยังไม่ให้คะแนน).
 *
 * Same visual language as OptionButtons (Step 12.5 tap-UI, ≥44px targets),
 * but not merged into it: the scale here is a fixed 1..10, and "no score"
 * is a meaningful state a field officer must be able to tap back to
 * deliberately — hence the dedicated ว่าง chip (shown pressed while the
 * score is empty) instead of only OptionButtons' tap-again-to-clear.
 */
const SCORES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

const baseChipCls =
  'min-h-11 min-w-11 rounded-lg border text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50';
const unselectedCls = 'border-gray-300 bg-white text-gray-700 hover:border-green-400 hover:bg-green-50';

export function ScoreButtons({ label, value, onChange, disabled }: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">{label}</span>
        <span className={`text-sm font-semibold ${value == null ? 'text-gray-400' : 'text-green-700'}`}>
          {value == null ? 'ยังไม่ให้คะแนน' : `${value} / 10`}
        </span>
      </div>
      <div role="group" aria-label={label} className="flex flex-wrap gap-1.5">
        {SCORES.map((n) => {
          const selected = value === n;
          return (
            <button
              key={n}
              type="button"
              disabled={disabled}
              aria-pressed={selected}
              onClick={() => onChange(selected ? null : n)}
              className={`${baseChipCls} ${
                selected
                  ? 'border-green-600 bg-green-600 text-white shadow-sm'
                  : unselectedCls
              }`}
            >
              {n}
            </button>
          );
        })}
        <button
          type="button"
          disabled={disabled}
          aria-pressed={value == null}
          onClick={() => onChange(null)}
          className={`${baseChipCls} px-3 ${
            value == null
              ? 'border-gray-500 bg-gray-500 text-white shadow-sm'
              : unselectedCls
          }`}
        >
          ว่าง
        </button>
      </div>
    </div>
  );
}
