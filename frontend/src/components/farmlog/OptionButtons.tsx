/**
 * OptionButtons (Step 12.5 tap-UI) — segmented tap-to-select for on-site entry.
 *
 * Renders options as large tappable chips (single select). Tapping the selected
 * chip clears it. Used for every list field in the record form so field officers
 * tap instead of opening dropdowns (Spec §19: tap target ≥44px).
 */
interface Props {
  options: string[];
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  loading?: boolean;
}

export function OptionButtons({ options, value, onChange, disabled, loading }: Props) {
  if (loading) {
    return <div className="flex gap-2">{[0, 1, 2].map(i => (
      <div key={i} className="h-11 w-20 animate-pulse rounded-lg bg-gray-100" />
    ))}</div>;
  }
  if (options.length === 0) {
    return <p className="text-xs text-gray-400 italic">— ยังไม่มีตัวเลือก —</p>;
  }
  // Keep a previously-saved value visible even if it's no longer in the list.
  const opts = value && !options.includes(value) ? [value, ...options] : options;

  return (
    <div className="flex flex-wrap gap-2">
      {opts.map(opt => {
        const selected = opt === value;
        return (
          <button
            key={opt}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => onChange(selected ? null : opt)}
            className={`min-h-11 rounded-lg border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
              selected
                ? 'border-green-600 bg-green-600 text-white shadow-sm'
                : 'border-gray-300 bg-white text-gray-700 hover:border-green-400 hover:bg-green-50'
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}
