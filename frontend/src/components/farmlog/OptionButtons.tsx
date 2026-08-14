/**
 * OptionButtons (Step 12.5 tap-UI) — segmented tap-to-select for on-site entry.
 *
 * Renders options as large tappable chips. Single select by default —
 * tapping the selected chip clears it. With `multiple`, chips toggle
 * independently and `value` holds a ", "-joined list (kept a plain string
 * so callers' form state, payloads, and every read-only display stay
 * unchanged — the backend column is a plain text field either way).
 * Used for every list field in the record form so field officers tap
 * instead of opening dropdowns (Spec §19: tap target ≥44px).
 */
const MULTI_SEPARATOR = ', ';

export function splitMultiValue(value: string | null): string[] {
  if (!value) return [];
  return value.split(',').map((v) => v.trim()).filter(Boolean);
}

interface Props {
  options: string[];
  value: string | null;
  onChange: (value: string | null) => void;
  disabled?: boolean;
  loading?: boolean;
  /** Multi-select mode — `value` is a ", "-joined list of selected options. */
  multiple?: boolean;
}

export function OptionButtons({ options, value, onChange, disabled, loading, multiple }: Props) {
  if (loading) {
    return <div className="flex gap-2">{[0, 1, 2].map(i => (
      <div key={i} className="h-11 w-20 animate-pulse rounded-lg bg-gray-100" />
    ))}</div>;
  }
  if (options.length === 0) {
    return <p className="text-xs text-gray-400 italic">— ยังไม่มีตัวเลือก —</p>;
  }
  const selectedValues = multiple ? splitMultiValue(value) : value ? [value] : [];
  // Keep previously-saved values visible even if no longer in the list.
  const stale = selectedValues.filter(v => !options.includes(v));
  const opts = [...stale, ...options];

  function toggle(opt: string, selected: boolean) {
    if (multiple) {
      const next = selected
        ? selectedValues.filter(v => v !== opt)
        : [...selectedValues, opt];
      onChange(next.length > 0 ? next.join(MULTI_SEPARATOR) : null);
      return;
    }
    onChange(selected ? null : opt);
  }

  return (
    <div className="flex flex-wrap gap-2">
      {opts.map(opt => {
        const selected = selectedValues.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            disabled={disabled}
            aria-pressed={selected}
            onClick={() => toggle(opt, selected)}
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
