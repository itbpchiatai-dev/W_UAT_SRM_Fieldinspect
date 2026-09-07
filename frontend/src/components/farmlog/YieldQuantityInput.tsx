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
 * The one exception to "editing either updates both" is `measuredQuantity`
 * (see its prop docstring): on the stages where the kg came off a scale, the
 * percentage is a read-only gauge, because back-computing a measured weight
 * from a dragged percentage would overwrite a fact with an estimate.
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
  /** Round C — what the kg box is called for THIS growth stage. Defaults to
   * the forecast wording; the harvest and final-yield stages pass
   * "ผลผลิตที่เก็บได้" instead (lib/inspection-stages.ts), because there the
   * number is measured, not predicted. Same field and same column either way —
   * only the name changes, so the two stages never grow a second kg box that
   * means almost the same thing. */
  quantityLabel?: string;
  /** True when the kg above is a MEASURED figure (the harvest and final-yield
   * stages) rather than a forecast. Two consequences, both about the same
   * thing — at those stages the percentage is an OUTPUT, not an input:
   *
   *   - the percentage becomes a read-only gauge instead of a draggable
   *     slider. Dragging it back-computes the kg (pctToQuantityKg), which is
   *     nonsense against a number that came off a scale.
   *   - its label names the figure it is computed from. The final-yield stage
   *     shows TWO kg boxes (ผลผลิตที่เก็บได้ and หลังทำความสะอาด) and only the
   *     first one feeds this percentage; without saying so, "150%" reads as
   *     ambiguous the moment the second box appears.
   *
   * Defaults false, so every forecasting stage keeps the original two-way
   * kg <-> % slider untouched. */
  measuredQuantity?: boolean;
  /** Hide the percentage row entirely. Used by the ผลผลิตสุดท้าย stage, where
   * the card carries TWO kg figures (ผลผลิตที่เก็บได้ and หลังทำความสะอาด) and
   * a single percentage next to them was read as ambiguous no matter how it
   * was labelled — the user asked for it gone.
   *
   * DISPLAY ONLY. The Backend still derives and stores yield_pct for the
   * record from ผลผลิตที่เก็บได้ exactly as before (services/
   * yield_calculation.py), and it still appears in the Records list, Plot
   * Status report and dashboard — hiding it here does not stop it existing.
   * The over-150% notice is deliberately NOT hidden with it: it guards against
   * a mistyped weight, which is precisely the risk that survives when the
   * percentage is out of sight. */
  hidePercentage?: boolean;
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
  quantityLabel = 'ผลผลิตที่คาดว่าจะได้',
  measuredQuantity = false,
  hidePercentage = false,
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

  // Rendered inside the percentage row normally, and on its own when that row
  // is hidden — a mistyped weight must still be flagged either way.
  const warningNotice = showWarning ? (
    <p role="status" className="mt-2 rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-700">
      ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก
    </p>
  ) : null;

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">{quantityLabel}</label>
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

      {hidePercentage ? warningNotice : (
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm text-gray-600">
            เปอร์เซ็นต์เทียบเป้าผลิต
            {measuredQuantity && (
              <span className="text-gray-500"> (จาก{quantityLabel})</span>
            )}
          </span>
          <span className="text-lg font-bold text-green-700">
            {yieldPct != null ? `${yieldPct.toFixed(1)}%` : '—'}
          </span>
        </div>
        {measuredQuantity ? (
          // A gauge, not a control — see measuredQuantity's docstring. Rendered
          // as a real progressbar rather than a disabled range so it neither
          // greys out (this IS the answer, not an unavailable input) nor takes
          // focus as something the user could operate.
          <div
            role="progressbar"
            aria-valuenow={yieldPct ?? 0}
            aria-valuemin={0}
            aria-valuemax={sliderMax}
            aria-label="เปอร์เซ็นต์เทียบเป้าผลิต"
            className="h-2 w-full overflow-hidden rounded-full bg-gray-200"
          >
            <div
              className="h-full rounded-full bg-green-600"
              style={{ width: `${Math.min(100, ((yieldPct ?? 0) / sliderMax) * 100)}%` }}
            />
          </div>
        ) : (
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
        )}
        {warningNotice}
      </div>
      )}
    </div>
  );
}
