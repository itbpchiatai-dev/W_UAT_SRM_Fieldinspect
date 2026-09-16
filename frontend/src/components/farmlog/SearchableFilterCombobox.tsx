/**
 * SearchableFilterCombobox — a filter over a backend-provided list of plain
 * strings (round 8-18's "รอบปลูกปัจจุบัน"/cycleLabel filter is the first user).
 *
 * Round Z — this used to carry its own copy of the open/search/click-outside
 * interaction; it is now a thin face over SearchableSelect, which every
 * chooser in the app shares. What stays here is this control's own contract:
 * options are plain strings (value === label), and '' means "no filter",
 * shown as `allLabel` — the shared control expresses that as null.
 */
import { SearchableSelect } from './SearchableSelect';

interface Props {
  /** Accessible name for the trigger button and the listbox (aria-label). */
  label: string;
  /** Trigger text when no filter is applied, and the "clear" option's own label. */
  allLabel: string;
  /** The real option list — e.g. distinct cycleLabel values in scope. */
  options: string[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
}

export function SearchableFilterCombobox({
  label,
  allLabel,
  options,
  value,
  onChange,
  placeholder = 'ค้นหา...',
  emptyMessage = 'ไม่พบรายการ',
  disabled,
}: Props) {
  return (
    <SearchableSelect
      label={label}
      placeholder={allLabel}
      clearLabel={allLabel}
      options={options.map((o) => ({ value: o, label: o }))}
      value={value || null}
      onChange={(next) => onChange(next ?? '')}
      searchPlaceholder={placeholder}
      emptyMessage={emptyMessage}
      disabled={disabled}
      // A value the option list no longer carries stays readable as itself.
      staleLabel={value}
      className="relative min-w-[220px] flex-1 sm:max-w-xs"
    />
  );
}
