/**
 * FilterMenu — Supplier, P.Code and cycle status folded behind one button.
 *
 * Three comboboxes sitting in the header pushed the period tabs onto a
 * second line and made the page look like a form. They collapse to one
 * control instead, with a count so the page can never be filtered without
 * saying so — reading a filtered number as the company total is the exact
 * mistake this is here to prevent.
 *
 * Deliberately not a modal: a filter panel that blocks the page stops you
 * seeing what it changes.
 */
import { useEffect, useRef, useState } from 'react';
import { SlidersHorizontal, X } from 'lucide-react';
import { SearchableSelect, type SearchableOption } from '../farmlog/SearchableSelect';

/** Mirrors the backend's plot_cycles.status values (CYCLE_STATUSES).
 *  No default: closing a cycle is a staff action that may never happen,
 *  so the page must not quietly depend on it. */
export const CYCLE_STATUS_OPTIONS: SearchableOption[] = [
  { value: 'active', label: 'กำลังปลูก' },
  { value: 'harvested', label: 'เก็บเกี่ยวแล้ว' },
  { value: 'cancelled', label: 'ยกเลิก' },
];

export interface FilterMenuValue {
  supplierId: string | null;
  pCode: string | null;
  cycleStatus: string | null;
}

export interface FilterMenuProps {
  value: FilterMenuValue;
  onChange: (next: FilterMenuValue) => void;
  supplierOptions: SearchableOption[];
  pCodeOptions: SearchableOption[];
}

export function activeFilterCount(v: FilterMenuValue): number {
  return [v.supplierId, v.pCode, v.cycleStatus].filter(Boolean).length;
}

export function FilterMenu({
  value, onChange, supplierOptions, pCodeOptions,
}: FilterMenuProps) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const count = activeFilterCount(value);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const set = (patch: Partial<FilterMenuValue>) => onChange({ ...value, ...patch });

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        aria-expanded={open}
        aria-label={count ? `ตัวกรอง ${count} รายการ` : 'ตัวกรอง'}
        onClick={() => setOpen((o) => !o)}
        className={
          'inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm ' +
          (count ? 'border-primary bg-primary/10 font-medium text-primary'
                 : 'bg-card text-muted-foreground hover:bg-muted')
        }
      >
        <SlidersHorizontal className="h-4 w-4" />
        ตัวกรอง
        {count > 0 && (
          <span className="rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground">
            {count}
          </span>
        )}
      </button>

      {open && (
        <div
          role="group"
          aria-label="ตัวกรองเพิ่มเติม"
          className="absolute right-0 z-20 mt-1 w-72 rounded-md border bg-card p-3 shadow-lg"
        >
          <div className="flex flex-col gap-3">
            <SearchableSelect
              label="กรอง Supplier"
              placeholder="ทุก Supplier"
              clearLabel="— ทุก Supplier —"
              options={supplierOptions}
              value={value.supplierId}
              onChange={(v) => set({ supplierId: v })}
              className="w-full"
            />
            <SearchableSelect
              label="กรอง P.Code"
              placeholder="ทุก P.Code"
              clearLabel="— ทุก P.Code —"
              options={pCodeOptions}
              value={value.pCode}
              onChange={(v) => set({ pCode: v })}
              className="w-full"
            />
            <SearchableSelect
              label="กรองสถานะรอบปลูก"
              placeholder="ทุกสถานะ"
              clearLabel="— ทุกสถานะ —"
              options={CYCLE_STATUS_OPTIONS}
              value={value.cycleStatus}
              onChange={(v) => set({ cycleStatus: v })}
              className="w-full"
            />
            <button
              type="button"
              disabled={count === 0}
              onClick={() => onChange({ supplierId: null, pCode: null, cycleStatus: null })}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              <X className="h-4 w-4" />
              ล้างตัวกรองทั้งหมด
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default FilterMenu;
