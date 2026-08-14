/**
 * SearchableFilterCombobox — generic searchable single-select dropdown for
 * a filter whose options come from a real backend-provided list of plain
 * strings (round 8-18's "รอบปลูกปัจจุบัน"/cycleLabel filter is the first
 * user). Same open/search/select/click-outside/Escape interaction as
 * Plots.tsx's own SupplierFilterCombobox — a SEPARATE component (not a
 * refactor of that one): Supplier options are objects with a code/name
 * display and their own active-suppliers fetch, while this is plain
 * strings from whatever list the caller passes in.
 *
 * value === '' means "no filter" (shows `allLabel`); any other value must
 * be one of `options` (or whatever was already selected before the option
 * list refreshed — this component never rejects an out-of-list value, it
 * just won't appear highlighted in the list until it reloads).
 */
import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Search } from 'lucide-react';

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
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);
  const normalizedSearch = search.trim().toLowerCase();
  const visibleOptions = normalizedSearch
    ? options.filter((opt) => opt.toLowerCase().includes(normalizedSearch))
    : options;

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  function select(next: string) {
    onChange(next);
    setSearch('');
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative min-w-[220px] flex-1 sm:max-w-xs">
      <button
        type="button"
        onClick={() => !disabled && setOpen((current) => !current)}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-secondary/60 focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className={value ? 'truncate text-foreground' : 'truncate text-muted-foreground'}>
          {value || allLabel}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-lg">
          <label className="relative block">
            <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={placeholder}
              className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
          </label>
          <div role="listbox" aria-label={label} className="mt-2 max-h-64 overflow-y-auto">
            <button
              type="button"
              role="option"
              aria-selected={value === ''}
              onClick={() => select('')}
              className="flex w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-secondary"
            >
              {allLabel}
            </button>
            {visibleOptions.map((opt) => (
              <button
                key={opt}
                type="button"
                role="option"
                aria-selected={opt === value}
                onClick={() => select(opt)}
                className={`flex w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-secondary ${
                  opt === value ? 'bg-primary/10 text-primary' : ''
                }`}
              >
                {opt}
              </button>
            ))}
            {visibleOptions.length === 0 && (
              <p className="px-3 py-3 text-sm text-muted-foreground">{emptyMessage}</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
