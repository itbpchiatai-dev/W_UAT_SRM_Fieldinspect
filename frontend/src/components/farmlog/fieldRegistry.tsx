/**
 * Field registry (Step 12) — one FieldType → one { renderer, validator }.
 *
 * Schema-driven form fields render through this single contract so adding a
 * custom field never touches the form code. Core-only widgets (score / percent
 * / photo / geo / plot_picker) keep their bespoke Step-11 rendering inside
 * RecordForm; this registry covers the admin-creatable custom types
 * (text / multiline / number / date / list / boolean) plus sensible fallbacks.
 */
import type { FieldDefinition, FieldType } from '../../api/fielddefs';
import { OptionButtons } from './OptionButtons';

// Round 8-25N — explicit bg-white/text-gray-900, same browser-auto-dark-
// widget fix as PublicInspect/YieldQuantityInput/SmartPlotPicker (see their
// comments) — every "ฟิลด์เพิ่มเติม" custom field renders inside one of
// those pages' fixed light-card sections in both RecordForm and
// PublicInspect.
const inputCls =
  'w-full rounded-md border border-gray-300 bg-white text-gray-900 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500 disabled:bg-gray-50 disabled:text-gray-500';

export type FieldValue = string | number | boolean | null;

/** Coerce a raw DOM value into the typed value for the given field. */
export function coerceValue(type: FieldType, raw: string): FieldValue {
  if (raw === '') return null;
  if (type === 'number' || type === 'score' || type === 'percent') {
    const n = Number(raw);
    return Number.isNaN(n) ? null : n;
  }
  if (type === 'boolean') return raw === 'true';
  return raw;
}

/** Validate one field value. Returns an error string or null when valid. */
export function validateField(field: FieldDefinition, value: FieldValue): string | null {
  const empty = value === null || value === undefined || value === '';
  if (field.required && empty && field.fieldType !== 'boolean') {
    return `กรุณาระบุ${field.label}`;
  }
  if (empty) return null;
  switch (field.fieldType) {
    case 'number':
    case 'score':
    case 'percent':
      if (typeof value !== 'number' || Number.isNaN(value)) return `${field.label} ต้องเป็นตัวเลข`;
      if (field.fieldType === 'score' && (value < 1 || value > 10)) return `${field.label} ต้องอยู่ระหว่าง 1–10`;
      if (field.fieldType === 'percent' && (value < 0 || value > 150)) return `${field.label} ต้องอยู่ระหว่าง 0–150`;
      return null;
    case 'list':
      if (field.options.length && !field.options.includes(String(value))) {
        return `${field.label} ไม่ตรงกับตัวเลือก`;
      }
      return null;
    default:
      return null;
  }
}

interface DynamicFieldProps {
  field: FieldDefinition;
  value: FieldValue;
  onChange: (value: FieldValue) => void;
  disabled?: boolean;
  error?: string;
}

/** Render the input widget for a single (custom) field by its type. */
export function DynamicFieldRenderer({ field, value, onChange, disabled, error }: DynamicFieldProps) {
  const { fieldType: type } = field;
  const common = { disabled, className: inputCls };

  let control: React.ReactNode;
  switch (type) {
    case 'boolean': {
      const opts: { label: string; val: boolean }[] = [
        { label: 'ใช่', val: true },
        { label: 'ไม่ใช่', val: false },
      ];
      control = (
        <div className="flex flex-wrap gap-2">
          {opts.map(({ label, val }) => {
            const selected = value === val;
            return (
              <button
                key={label}
                type="button"
                disabled={disabled}
                aria-pressed={selected}
                onClick={() => onChange(selected ? null : val)}
                className={`min-h-11 rounded-lg border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${
                  selected ? 'border-green-600 bg-green-600 text-white shadow-sm'
                           : 'border-gray-300 bg-white text-gray-700 hover:border-green-400 hover:bg-green-50'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      );
      break;
    }
    case 'multiline':
      control = (
        <textarea
          {...common}
          rows={3}
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(coerceValue(type, e.target.value))}
        />
      );
      break;
    case 'percent': {
      const pct = typeof value === 'number' ? value : 100;
      control = (
        <div className="flex items-center gap-3">
          <input
            type="range"
            min={0}
            max={150}
            step={1}
            value={pct}
            disabled={disabled}
            onChange={(e) => onChange(Number(e.target.value))}
            className="h-2 flex-1 cursor-pointer accent-green-600 disabled:cursor-not-allowed"
          />
          <span className="w-14 shrink-0 text-right text-sm font-semibold text-green-700">{pct}%</span>
        </div>
      );
      break;
    }
    case 'number':
    case 'score':
      control = (
        <input
          {...common}
          type="number"
          step={type === 'number' ? '0.01' : '1'}
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(coerceValue(type, e.target.value))}
        />
      );
      break;
    case 'date':
      control = (
        <input
          {...common}
          type="date"
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(coerceValue(type, e.target.value))}
        />
      );
      break;
    case 'list':
      control = (
        <OptionButtons
          options={field.options.map(String)}
          value={value == null ? null : String(value)}
          disabled={disabled}
          onChange={(v) => onChange(v)}
        />
      );
      break;
    default: // text + any unsupported-as-custom fallback
      control = (
        <input
          {...common}
          type="text"
          value={value == null ? '' : String(value)}
          onChange={(e) => onChange(coerceValue('text', e.target.value))}
        />
      );
  }

  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700">
        {field.label}
        {field.required && <span className="ml-1 text-red-500">*</span>}
      </label>
      {control}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}
