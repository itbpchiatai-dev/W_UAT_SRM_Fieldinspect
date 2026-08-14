/**
 * PlotAccessPhoneFields — shared primary/additional phone editor (round
 * 8-3C). A plain controlled component: it never calls the API itself, so the
 * SAME markup/validation works inside both the Create Plot modal (as part of
 * the atomic plot+cycle+phones create) and PlotAccessPhoneModal (the
 * standalone GET/PUT management flow) — the parent owns the value, the
 * mutation, and when to submit.
 *
 * Validation here is UX-only (immediate Thai feedback + submit gating); the
 * backend (app/schemas/plot.py::PlotAccessPhoneConfig) remains the authority
 * and re-validates independently on every write.
 */
import { useId, useMemo } from 'react';
import { Phone, Plus, Trash2 } from 'lucide-react';
import { normalizeThaiMobile } from '../../lib/phone';
import type { PlotAccessPhoneConfig } from '../../api/plots';

export const MAX_ADDITIONAL_PHONES = 10;

/** Raw, as-typed form value — primaryPhone/additionalPhones stay free text
 * (never pre-normalized) while the user is editing; blank additional rows are
 * a normal transient state, not an error. */
export interface PlotAccessPhoneFieldsValue {
  primaryPhone: string;
  additionalPhones: string[];
}

export function emptyPlotAccessPhoneFieldsValue(): PlotAccessPhoneFieldsValue {
  return { primaryPhone: '', additionalPhones: [] };
}

/** Convert a fetched/canonical config into editable raw form values. */
export function accessPhoneConfigToFieldsValue(
  config: PlotAccessPhoneConfig | null | undefined,
): PlotAccessPhoneFieldsValue {
  if (!config) return emptyPlotAccessPhoneFieldsValue();
  return {
    primaryPhone: config.primaryPhone ?? '',
    additionalPhones: [...config.additionalPhones],
  };
}

export interface PlotAccessPhoneFieldErrors {
  primary?: string;
  // Same length/order as the INPUT additionalPhones (including blank rows,
  // which never carry an error) so a row's error can be indexed directly.
  additional: (string | undefined)[];
}

export interface PlotAccessPhoneBuildResult {
  /** The canonical, ready-to-send config, or null when any error blocks
   * submission. Blank additional rows are silently dropped (never sent, never
   * an error) — only non-blank rows are validated. */
  config: PlotAccessPhoneConfig | null;
  errors: PlotAccessPhoneFieldErrors;
  hasErrors: boolean;
}

/**
 * Pure validation/build function — normalizes non-blank entries, rejects
 * duplicates (primary-vs-additional and additional-vs-additional) and
 * additional-without-primary, same rules the backend enforces. Exported so
 * both this component (live inline errors) and its parents (submit gating)
 * share one implementation.
 */
export function buildPlotAccessPhoneConfig(
  value: PlotAccessPhoneFieldsValue,
): PlotAccessPhoneBuildResult {
  const errors: PlotAccessPhoneFieldErrors = { additional: value.additionalPhones.map(() => undefined) };

  let primaryCanonical: string | null = null;
  const primaryRaw = value.primaryPhone.trim();
  if (primaryRaw) {
    try {
      primaryCanonical = normalizeThaiMobile(primaryRaw);
    } catch (e) {
      errors.primary = e instanceof Error ? e.message : 'รูปแบบเบอร์โทรศัพท์ไม่ถูกต้อง';
    }
  }

  // Non-blank rows only — a blank row is skipped entirely (not validated,
  // not sent), so the user can leave a fresh empty row while adding another.
  const additionalCanonical: (string | null)[] = value.additionalPhones.map((raw, i) => {
    const trimmed = raw.trim();
    if (!trimmed) return null;
    try {
      return normalizeThaiMobile(trimmed);
    } catch (e) {
      errors.additional[i] = e instanceof Error ? e.message : 'รูปแบบเบอร์โทรศัพท์ไม่ถูกต้อง';
      return null;
    }
  });

  const hasFormatErrors = Boolean(errors.primary) || errors.additional.some(Boolean);

  // Duplicate detection compares CANONICAL values, so "081-234-5678" and
  // "0812345678" in two different rows are recognized as the same number.
  if (!hasFormatErrors) {
    const seen = new Map<string, number>(); // canonical -> first row index
    additionalCanonical.forEach((canonical, i) => {
      if (canonical == null) return;
      const firstIndex = seen.get(canonical);
      if (firstIndex != null) {
        errors.additional[i] = 'เบอร์นี้ซ้ำกับเบอร์เสริมแถวอื่น';
        errors.additional[firstIndex] = 'เบอร์นี้ซ้ำกับเบอร์เสริมแถวอื่น';
      } else {
        seen.set(canonical, i);
      }
      if (primaryCanonical != null && canonical === primaryCanonical) {
        errors.additional[i] = 'เบอร์นี้ซ้ำกับเบอร์หลัก';
        errors.primary = errors.primary ?? 'เบอร์นี้ซ้ำกับเบอร์เสริม';
      }
    });
  }

  const additionalFinal = additionalCanonical.filter((c): c is string => c != null);

  // Round 8-3C business rule: additional numbers require a primary.
  if (!errors.primary && primaryCanonical == null && additionalFinal.length > 0) {
    errors.primary = 'กรุณากรอกเบอร์หลักก่อนเพิ่มเบอร์เสริม';
  }

  if (!errors.primary && additionalFinal.length > MAX_ADDITIONAL_PHONES) {
    errors.primary = `เพิ่มเบอร์เสริมได้สูงสุด ${MAX_ADDITIONAL_PHONES} เบอร์`;
  }

  const hasErrors = Boolean(errors.primary) || errors.additional.some(Boolean);
  return {
    config: hasErrors ? null : { primaryPhone: primaryCanonical, additionalPhones: additionalFinal },
    errors,
    hasErrors,
  };
}

export function PlotAccessPhoneFields({
  value,
  onChange,
  disabled = false,
  autoFocusPrimary = false,
}: {
  value: PlotAccessPhoneFieldsValue;
  onChange: (value: PlotAccessPhoneFieldsValue) => void;
  disabled?: boolean;
  /** Focuses the primary input on mount — used when this renders inside a
   * freshly-opened modal (round 8-3C Part E: "focus primary field เมื่อเปิด"). */
  autoFocusPrimary?: boolean;
}) {
  const idBase = useId();
  const { errors } = useMemo(() => buildPlotAccessPhoneConfig(value), [value]);
  const canAddMore = value.additionalPhones.length < MAX_ADDITIONAL_PHONES;

  function setPrimary(next: string) {
    onChange({ ...value, primaryPhone: next });
  }

  function setAdditionalAt(index: number, next: string) {
    const additionalPhones = value.additionalPhones.slice();
    additionalPhones[index] = next;
    onChange({ ...value, additionalPhones });
  }

  function removeAt(index: number) {
    onChange({
      ...value,
      additionalPhones: value.additionalPhones.filter((_, i) => i !== index),
    });
  }

  function addRow() {
    if (!canAddMore) return;
    onChange({ ...value, additionalPhones: [...value.additionalPhones, ''] });
  }

  const inputClass = 'field-input flex-1';
  const errorInputClass = 'field-input flex-1 border-destructive focus:ring-destructive';

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor={`${idBase}-primary`} className="mb-1 block text-xs font-medium text-muted-foreground">
          เบอร์หลัก
        </label>
        <input
          id={`${idBase}-primary`}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          autoFocus={autoFocusPrimary}
          disabled={disabled}
          value={value.primaryPhone}
          onChange={(e) => setPrimary(e.target.value)}
          placeholder="084-555-2162"
          className={errors.primary ? errorInputClass : inputClass}
          aria-invalid={Boolean(errors.primary)}
          aria-describedby={errors.primary ? `${idBase}-primary-error` : undefined}
        />
        {errors.primary && (
          <p id={`${idBase}-primary-error`} className="mt-1 text-xs text-destructive">{errors.primary}</p>
        )}
      </div>

      <div>
        <span className="mb-1 block text-xs font-medium text-muted-foreground">เบอร์เสริม</span>
        <div className="space-y-2">
          {value.additionalPhones.map((phone, i) => (
            // rows have no stable id, only a position, so index is the key
            <div key={i}>
              <div className="flex items-center gap-2">
                <input
                  type="tel"
                  inputMode="tel"
                  autoComplete="off"
                  disabled={disabled}
                  value={phone}
                  onChange={(e) => setAdditionalAt(i, e.target.value)}
                  placeholder="081-234-5678"
                  aria-label={`เบอร์เสริมที่ ${i + 1}`}
                  className={errors.additional[i] ? errorInputClass : inputClass}
                  aria-invalid={Boolean(errors.additional[i])}
                  aria-describedby={errors.additional[i] ? `${idBase}-additional-${i}-error` : undefined}
                />
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => removeAt(i)}
                  title="ลบเบอร์นี้"
                  aria-label={`ลบเบอร์เสริมที่ ${i + 1}`}
                  className="shrink-0 rounded-md border border-border bg-background p-2 text-muted-foreground shadow-sm transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-60"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              {errors.additional[i] && (
                <p id={`${idBase}-additional-${i}-error`} className="mt-1 text-xs text-destructive">
                  {errors.additional[i]}
                </p>
              )}
            </div>
          ))}
        </div>

        <button
          type="button"
          disabled={disabled || !canAddMore}
          onClick={addRow}
          title={canAddMore ? 'เพิ่มเบอร์เสริม' : `เพิ่มเบอร์เสริมได้สูงสุด ${MAX_ADDITIONAL_PHONES} เบอร์`}
          aria-label="เพิ่มเบอร์เสริม"
          className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Plus className="h-3.5 w-3.5" />
          เพิ่มเบอร์เสริม
        </button>
      </div>
    </div>
  );
}

/** Small heading badge used by both PlotDetail and the modal so "เบอร์โทร
 * สำหรับเข้าตรวจแปลง" always carries the same icon. */
export function PlotAccessPhoneHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="flex items-center gap-1.5 text-base font-semibold text-foreground">
      <Phone className="h-4 w-4 text-muted-foreground" />
      {children}
    </h2>
  );
}
