/**
 * SearchableSelect (round Z) — one searchable dropdown, worn three ways.
 *
 * The app had grown two near-identical comboboxes (Plots.tsx's
 * SupplierFilterCombobox and SearchableFilterCombobox) for the FILTER bar,
 * while every form field was a plain <select> — no search at all, which is
 * unusable for 77 provinces or the ~500 P.Codes UAT carries. This is the
 * shared behaviour: open, search, pick, close on Escape or an outside click.
 *
 * Options are {value, label} because a form's value is rarely its label: a
 * Supplier is an id shown as "SUP001 — ชื่อ", a P.Code is "CTT-507" shown with
 * its variety. `clearLabel` is what makes the same control serve a filter
 * ("ทุกรอบปลูก" clears it); a required field simply omits it and then has
 * nothing to clear to.
 */
import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Search } from 'lucide-react';

export interface SearchableOption {
  value: string;
  label: string;
}

interface Props {
  /** Accessible name for the trigger, and the text shown while nothing is
   *  chosen (a form's "— เลือกจังหวัด —", a filter's own label). */
  label: string;
  options: SearchableOption[];
  value: string | null;
  onChange: (value: string | null) => void;
  /** Adds a first option that clears the field. Filters want one; required
   *  form fields do not. */
  clearLabel?: string;
  /** What to show when `value` is not in `options` (a master-data row
   *  deactivated since it was chosen). Without it the raw value is shown. */
  staleLabel?: string;
  /** Trigger text override — when the resting state should read differently
   *  from the accessible name (a filter shows "ทุกรอบปลูก", not its label). */
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  /** Layout: filters sit in a row and cap their width; form fields fill
   *  their column. */
  className?: string;
}

export function SearchableSelect({
  label,
  options,
  value,
  onChange,
  clearLabel,
  staleLabel,
  placeholder,
  searchPlaceholder = 'ค้นหา...',
  emptyMessage = 'ไม่พบรายการ',
  disabled,
  className = 'relative w-full',
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);

  const normalized = search.trim().toLowerCase();
  const visible = normalized
    ? options.filter((o) => o.label.toLowerCase().includes(normalized))
    : options;
  const selected = options.find((o) => o.value === value) ?? null;
  const resting = placeholder ?? label;
  const triggerText = value ? (selected?.label ?? staleLabel ?? value) : resting;

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

  function select(next: string | null) {
    onChange(next);
    setSearch('');
    setOpen(false);
  }

  return (
    <div ref={ref} className={className}>
      <button
        type="button"
        // `disabled` on the button is what blocks the click; a second
        // check here would be code no test could ever falsify.
        onClick={() => setOpen((current) => !current)}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-secondary/60 focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground disabled:opacity-70"
      >
        <span className={value ? 'truncate text-foreground' : 'truncate text-muted-foreground'}>
          {triggerText}
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
              placeholder={searchPlaceholder}
              className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
          </label>
          <div role="listbox" aria-label={label} className="mt-2 max-h-64 overflow-y-auto">
            {clearLabel !== undefined && (
              <button
                type="button"
                role="option"
                aria-selected={!value}
                onClick={() => select(null)}
                className="flex w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-secondary"
              >
                {clearLabel}
              </button>
            )}
            {visible.map((o) => (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={o.value === value}
                onClick={() => select(o.value)}
                className={`flex w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-secondary ${
                  o.value === value ? 'bg-primary/10 text-primary' : ''
                }`}
              >
                {o.label}
              </button>
            ))}
            {visible.length === 0 && (
              <p className="px-3 py-3 text-sm text-muted-foreground">{emptyMessage}</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
