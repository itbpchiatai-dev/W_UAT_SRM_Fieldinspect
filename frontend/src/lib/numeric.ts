/**
 * Numeric normalization for API fields backed by Postgres `Numeric`/
 * `Decimal` columns (plots.latitude/longitude/rai, records.latitude/
 * longitude/yieldPct, etc.) — FastAPI/Pydantic serializes these as JSON
 * strings (verified against the actual response shape; see round 15's
 * report), but some frontend types still declare them as `number`. Calling
 * `.toFixed()` directly on what's actually a string throws at runtime
 * (round 15.1 fixes several such call sites) — always go through
 * `toNumberOrNull` first.
 */
export function toNumberOrNull(value: string | number | null | undefined): number | null {
  if (value == null) return null;
  const n = typeof value === 'number' ? value : parseFloat(value);
  return Number.isFinite(n) ? n : null;
}

/** Normalize + format in one step. Returns null (not "NaN") for anything
 * that doesn't parse, so callers can render a fallback instead of a
 * confusing "NaN" string. */
export function formatFixed(value: string | number | null | undefined, digits: number): string | null {
  const n = toNumberOrNull(value);
  return n == null ? null : n.toFixed(digits);
}
