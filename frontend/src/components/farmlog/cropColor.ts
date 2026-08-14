/**
 * Deterministic color for a crop label so the same crop always renders in the
 * same hue across the map markers and its legend, with no server-side color
 * data. A null/blank crop ("ไม่ระบุ") always maps to a neutral gray.
 *
 * The palette is a fixed set of high-contrast hues; a crop is hashed to an
 * index, so adding plots never reshuffles existing colors (only a brand-new
 * distinct crop name picks up the next hue by hash).
 */
const CROP_PALETTE = [
  '#16a34a', // green
  '#dc2626', // red
  '#2563eb', // blue
  '#d97706', // amber
  '#7c3aed', // violet
  '#db2777', // pink
  '#0891b2', // cyan
  '#65a30d', // lime
  '#ea580c', // orange
  '#4f46e5', // indigo
  '#0d9488', // teal
  '#b45309', // brown
] as const;

export const UNSPECIFIED_CROP_COLOR = '#9ca3af'; // gray-400

/** Stable string hash (djb2) → non-negative int. */
function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

export function cropColor(crop: string | null | undefined): string {
  const key = (crop ?? '').trim();
  if (key === '') return UNSPECIFIED_CROP_COLOR;
  return CROP_PALETTE[hashString(key) % CROP_PALETTE.length];
}
