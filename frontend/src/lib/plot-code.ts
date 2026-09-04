/**
 * Auto Plot Code display helpers (round B) — the client-side mirror of the
 * backend's services/plot_code.py.
 *
 * A plot code is generated server-side as:
 *
 *     {supplierCode}-{YYMM}-{running}      e.g. "JPS-2605-001"
 *
 * The running number is allocated ONLY at save time, under the supplier's
 * month series, so nothing here may invent one — every preview renders "###"
 * in its place, exactly as autoLotPreview does for the Auto Lot.
 *
 * Display-only. Nothing here is ever sent: the client asks for a generated
 * code by sending plotCode: null, and reads back whatever the server minted.
 */

/** YYMM in the Christian era — 2026-05-17 -> "2605". Mirrors plot_code.py's
 * month_stamp, including the choice of ค.ศ. over พ.ศ. so a plot code and the
 * Auto Lot on its first cycle never disagree about which year "26" is. */
export function monthStamp(on: Date): string {
  const yy = String(on.getFullYear() % 100).padStart(2, '0');
  const mm = String(on.getMonth() + 1).padStart(2, '0');
  return `${yy}${mm}`;
}

/** Parse an <input type="date"> value ("YYYY-MM-DD") as a LOCAL date.
 *
 * Deliberately not `new Date(value)`: that parses a bare date string as UTC
 * midnight, which in Thailand (UTC+7) still reads as the same day — but the
 * same trap one timezone west silently shifts the month, and the month is the
 * whole point of this string. Returns null for a blank or malformed value so
 * the caller can fall back to today. */
export function parseLocalDate(value: string | null | undefined): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value ?? '').trim());
  if (!match) return null;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  // Rejects impossible dates that the constructor would silently roll over
  // (e.g. "2026-02-31" -> 3 March).
  return date.getMonth() === Number(m) - 1 ? date : null;
}

/**
 * The code the server WILL generate, with "###" where the running number goes.
 *
 * `plantingDate` is the "YYYY-MM-DD" the form holds for the first cycle: the
 * code is stamped with the PLANTING month, not the day the plot happens to be
 * registered, so a plot entered in April for a May planting previews (and
 * saves as) 2605 — the season it will be filed under. Blank/invalid falls back
 * to today, which is what the backend does too.
 *
 * A component that isn't known yet renders as a readable Thai placeholder
 * rather than a fabricated value.
 */
export function autoPlotCodePreview(
  supplierCode: string | null | undefined,
  plantingDate?: string | null,
): string {
  const supplier = (supplierCode ?? '').trim().toUpperCase() || '<รหัส Supplier>';
  const month = monthStamp(parseLocalDate(plantingDate) ?? new Date());
  return `${supplier}-${month}-###`;
}

/** Human label + tone for a plot's plotCodeSource badge — the sibling of
 * lotSourceBadge in PlotCycleModals.tsx, and deliberately the same vocabulary:
 * a user seeing "อัตโนมัติ" on a lot and on a plot code should understand the
 * same thing by it. null (a plot created before round B) reads as "ข้อมูลเดิม"
 * rather than being tagged as hand-entered, because nobody chose it: those
 * codes simply predate the generator. */
export function plotCodeSourceBadge(
  source: string | null | undefined,
): { label: string; className: string } | null {
  if (source === 'auto') return { label: 'อัตโนมัติ', className: 'bg-green-100 text-green-700' };
  if (source === 'manual') return { label: 'กรอกเอง', className: 'bg-blue-100 text-blue-700' };
  if (source === 'legacy') return { label: 'ข้อมูลเดิม', className: 'bg-gray-100 text-gray-600' };
  return null;
}
