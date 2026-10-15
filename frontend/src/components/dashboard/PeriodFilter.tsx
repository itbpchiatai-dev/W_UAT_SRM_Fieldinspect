/**
 * PeriodFilter — three presets plus an explicit from/to window.
 *
 * Replaces round 45's RangeTabs, whose fourth option was "ทั้งหมด". That
 * one answered a question nobody asks ("since the beginning of the data")
 * and got slower as the table grew. What people actually wanted was "that
 * season", which needs two dates.
 *
 * The preset values are the backend's own range keys
 * (dashboard_repository.EXECUTIVE_RANGES), so the string travels from the
 * button into the query with nothing in between to translate — and nothing
 * in between to get it wrong. 'custom' is deliberately NOT one of them: the
 * dates are what define a custom window, and the server falls back to its
 * default if 'custom' arrives without them.
 */
import type { ExecutiveRangeKey } from '../../api/dashboard';

export const PRESETS: { value: ExecutiveRangeKey; label: string }[] = [
  { value: '30d', label: '30 วัน' },
  { value: '3m',  label: '3 เดือน' },
  { value: 'ytd', label: 'ปีนี้' },
];

export const CUSTOM_LABEL = 'กำหนดเอง';

export interface PeriodValue {
  range: ExecutiveRangeKey;
  dateFrom: string | null;
  dateTo: string | null;
}

export interface PeriodFilterProps {
  value: PeriodValue;
  onChange: (next: PeriodValue) => void;
}

const TAB_CLASS = 'rounded px-3 py-1.5 text-sm transition-colors';
const ACTIVE = 'bg-primary font-semibold text-primary-foreground';
const IDLE = 'text-muted-foreground hover:bg-muted';

export function PeriodFilter({ value, onChange }: PeriodFilterProps) {
  const custom = value.range === 'custom';
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="group" aria-label="ช่วงเวลา" className="inline-flex rounded-md border bg-card p-0.5">
        {PRESETS.map((p) => (
          <button
            key={p.value}
            type="button"
            aria-pressed={!custom && p.value === value.range}
            // Switching back to a preset clears the dates: leaving them set
            // would send a window the highlighted tab does not describe.
            onClick={() => onChange({ range: p.value, dateFrom: null, dateTo: null })}
            className={`${TAB_CLASS} ${!custom && p.value === value.range ? ACTIVE : IDLE}`}
          >
            {p.label}
          </button>
        ))}
        <button
          type="button"
          aria-pressed={custom}
          onClick={() => onChange({ ...value, range: 'custom' })}
          className={`${TAB_CLASS} ${custom ? ACTIVE : IDLE}`}
        >
          {CUSTOM_LABEL}
        </button>
      </div>

      {custom && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">ตั้งแต่</span>
            <input
              type="date"
              aria-label="ตั้งแต่วันที่"
              value={value.dateFrom ?? ''}
              onChange={(e) => onChange({ ...value, range: 'custom', dateFrom: e.target.value || null })}
              className="rounded-md border px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">ถึง</span>
            <input
              type="date"
              aria-label="ถึงวันที่"
              value={value.dateTo ?? ''}
              onChange={(e) => onChange({ ...value, range: 'custom', dateTo: e.target.value || null })}
              className="rounded-md border px-2 py-1.5 text-sm"
            />
          </label>
          {/* Said out loud rather than left to be discovered: with neither
              date typed the server has nothing to go on and shows its
              default window, which would otherwise look like a bug. */}
          {!value.dateFrom && !value.dateTo && (
            <span className="text-xs text-muted-foreground">
              เลือกวันที่เพื่อกำหนดช่วงเอง
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default PeriodFilter;
