/**
 * YieldQuantityInput — round 8-8B shared kg/percentage Yield input, used
 * IDENTICALLY by RecordForm (logged-in) and PublicInspect (public) so the
 * two flows can never visually/behaviorally drift (contract #11).
 *
 * kg is the PRIMARY input; the percentage slider is a synchronized
 * alternate view of the SAME value — editing either updates both, via
 * lib/yield-planning.ts's quantityKgToPct/pctToQuantityKg (mirrors Backend
 * round 8-8A.1's derive_yield exactly: same unit factors, same
 * "non-comparable" rules). The Backend remains the source of truth
 * (contract #10) — everything here is a live PREVIEW, recomputed on every
 * keystroke/drag, never persisted by this component itself.
 *
 * When the active cycle has no comparable kg target (no plan, a non-weight
 * unit like ผล/ลัง, or a target that rounds to 0.00), the kg input STAYS
 * enabled (contract #7 — a field worker can still record a raw quantity)
 * but the slider is disabled and yieldPct is always null — never a faked
 * 100%. Renders inline (no alert()); the caller wraps this in its own
 * `<section>` card — this component has no outer card of its own.
 *
 * Round 8-8B.1 — real growers reported genuine harvests over 150% of plan,
 * so 150% (YIELD_WARNING_PCT) is now a non-blocking amber NOTICE only
 * (role="status", never role="alert" — that's reserved for the blocking
 * `error` prop) — Submit is never disabled by it. The slider's own `max`
 * expands dynamically past 150 in 50-point steps (160% -> 200, 225% -> 250,
 * 510% -> 550, ...) so a huge value is still draggable, capped at
 * MAX_STORABLE_YIELD_PCT (9999.9, the column's own NUMERIC(5,1) storage
 * ceiling) — never fixed at 9999.9 all the time, which would make the
 * common 0-100% range unusably coarse. The stored yieldPct value itself is
 * NEVER clamped to 150 — only the slider's max attribute changes.
 */
import {
  targetToKg, formatYieldQuantity, quantityKgToPct, pctToQuantityKg,
  YIELD_WARNING_PCT, MAX_STORABLE_YIELD_PCT,
} from '../../lib/yield-planning';

/** The slider's `max` for the CURRENT yieldPct: 150 while at/under the
 * warning threshold, otherwise the next 50-point tier above it, capped at
 * the technical storage ceiling. Purely derived per render — no separate
 * state — so it automatically snaps back to 150 the moment yieldPct drops
 * back to <=150, without touching the value itself. */
function computeSliderMax(yieldPct: number | null): number {
  if (yieldPct == null || yieldPct <= YIELD_WARNING_PCT) return YIELD_WARNING_PCT;
  const expanded = Math.ceil(yieldPct / 50) * 50;
  return Math.min(expanded, MAX_STORABLE_YIELD_PCT);
}

export interface YieldQuantityInputProps {
  quantityKg: number | null;
  yieldPct: number | null;
  expectedYieldFull: string | number | null | undefined;
  expectedYieldUnit: string | null | undefined;
  /** The plot's latest inspection-derived Yield % (round 8-3J/8-8B), shown
   * as a compact hint next to the target — NOT re-applied by this component
   * (initial-value defaulting is the caller's job, lib/yield-planning.ts's
   * computeInitialYieldValue, run once when a plot/cycle is selected). */
  latestYieldPct?: string | number | null;
  disabled?: boolean;
  onChange: (value: { quantityKg: number | null; yieldPct: number | null }) => void;
  error?: string | null;
}

// Round 8-25N — explicit bg-white/text-gray-900, same fix and same reason
// as PublicInspect.tsx's own inputCls: with no explicit background/text
// color, `html { color-scheme: light dark }` (index.css) lets the BROWSER
// auto-dark-theme this input the instant the device's OS is in dark mode —
// independent of our app's own .dark class. This component's caller-side
// card (RecordForm's "ผลผลิต (Yield)" section AND PublicInspect's Yield
// card) is a fixed light-green/white box either way, never one of the
// pages' dark-mode-aware bg-card sections — so a fixed light input here is
// consistent with its surrounding card in both callers, not just a patch.
const inputCls = 'w-full rounded-md border border-gray-300 bg-white text-gray-900 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-gray-50 disabled:text-gray-500';

export function YieldQuantityInput({
  quantityKg,
  yieldPct,
  expectedYieldFull,
  expectedYieldUnit,
  latestYieldPct,
  disabled,
  onChange,
  error,
}: YieldQuantityInputProps) {
  const targetKg = targetToKg(expectedYieldFull, expectedYieldUnit);
  const latestPct = latestYieldPct == null || latestYieldPct === ''
    ? null
    : Number(latestYieldPct);
  const sliderMax = computeSliderMax(yieldPct);
  const showWarning = yieldPct != null && yieldPct > YIELD_WARNING_PCT;

  function handleKgChange(raw: string) {
    if (raw === '') {
      onChange({ quantityKg: null, yieldPct: null });
      return;
    }
    const n = Number(raw);
    if (!Number.isFinite(n)) return;
    onChange({ quantityKg: n, yieldPct: quantityKgToPct(n, targetKg) });
  }

  function handlePctChange(raw: string) {
    const pct = Number(raw);
    if (!Number.isFinite(pct)) return;
    onChange({ quantityKg: pctToQuantityKg(pct, targetKg), yieldPct: pct });
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">ผลผลิตที่คาดว่าจะได้</label>
        <div className="flex items-center gap-2">
          <input
            type="number"
            inputMode="decimal"
            min={0}
            step={0.01}
            value={quantityKg ?? ''}
            disabled={disabled}
            onChange={(e) => handleKgChange(e.target.value)}
            className={inputCls}
          />
          <span className="shrink-0 text-sm text-gray-500">kg</span>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          {targetKg != null
            ? `เทียบกับเป้าผลิต ${formatYieldQuantity(targetKg, 'kg')} (ที่ 100%)${latestPct != null && Number.isFinite(latestPct) ? ` · ล่าสุด ${latestPct}%` : ''}`
            : 'รอบปลูกนี้ไม่มีเป้าผลิตหน่วย kg สำหรับคำนวณเปอร์เซ็นต์'}
        </p>
        {error && <p role="alert" className="mt-1 text-xs text-red-600">{error}</p>}
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm text-gray-600">เปอร์เซ็นต์เทียบเป้าผลิต</span>
          <span className="text-lg font-bold text-green-700">
            {yieldPct != null ? `${yieldPct.toFixed(1)}%` : '—'}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={sliderMax}
          step={0.1}
          value={yieldPct ?? 0}
          disabled={disabled || targetKg == null}
          onChange={(e) => handlePctChange(e.target.value)}
          className="h-2 w-full cursor-pointer accent-green-600 disabled:cursor-not-allowed disabled:opacity-50"
        />
        {showWarning && (
          <p role="status" className="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-700">
            ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก
          </p>
        )}
      </div>
    </div>
  );
}
