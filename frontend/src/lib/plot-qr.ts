/**
 * plot-qr — parses QR payloads scanned from a plot's field sign, and builds
 * the payload printed onto new signs.
 *
 * Round 20 QR hardening: new signs print an opaque `qr` key instead of the
 * guessable supplierCode+plotCode pair. Both shapes resolve to a plot, so
 * every consumer works with the `PlotQrLocator` union below rather than a
 * single fixed shape — see `mode`.
 *
 * Four scannable formats (URL is checked first since it's what every
 * newly-printed sign uses; the other three predate round 20/17.0 and stay
 * supported so already-printed signs keep working):
 *   URL (new):    https://<app>/public/inspect?qr=<opaque-key>
 *   URL (legacy): https://<app>/public/inspect?supplierCode=SUP001&plotCode=PLOT001
 *   JSON:         {"supplierCode":"SUP001","plotCode":"PLOT001"}
 *   short:        SUP001|PLOT001
 */

export type PlotQrLocator =
  | { mode: 'qr'; qrKey: string }
  | { mode: 'legacy'; supplierCode: string; plotCode: string };

function parseDeepLinkUrl(text: string): PlotQrLocator | null {
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  return parseDeepLinkParams(url.searchParams);
}

export function parsePlotQr(raw: string): PlotQrLocator | null {
  const text = raw.trim();
  if (!text) return null;

  if (text.startsWith('http://') || text.startsWith('https://')) {
    return parseDeepLinkUrl(text);
  }

  if (text.startsWith('{')) {
    let obj: unknown;
    try {
      obj = JSON.parse(text);
    } catch {
      return null;
    }
    if (typeof obj !== 'object' || obj === null) return null;
    const supplierCode = (obj as Record<string, unknown>).supplierCode;
    const plotCode = (obj as Record<string, unknown>).plotCode;
    if (typeof supplierCode !== 'string' || typeof plotCode !== 'string') return null;
    const trimmedSupplierCode = supplierCode.trim();
    const trimmedPlotCode = plotCode.trim();
    if (!trimmedSupplierCode || !trimmedPlotCode) return null;
    return { mode: 'legacy', supplierCode: trimmedSupplierCode, plotCode: trimmedPlotCode };
  }

  const parts = text.split('|').map(p => p.trim());
  if (parts.length === 2 && parts[0] && parts[1]) {
    return { mode: 'legacy', supplierCode: parts[0], plotCode: parts[1] };
  }

  return null;
}

/** Builds the short pipe-delimited QR payload — kept only for parsePlotQr's
 * backward-compat branch and its tests; new signs print buildPlotQrDeepLink
 * instead (see PlotQrPrintSheet). */
export function buildPlotQrPayload(supplierCode: string, plotCode: string): string {
  return `${supplierCode.trim()}|${plotCode.trim()}`;
}

/**
 * Builds the URL deep link printed on new plot field signs.
 *
 * Round 20 QR hardening: prefers the plot's opaque `qrKey` (unguessable,
 * doesn't reveal supplierCode/plotCode) — `?qr=<key>`. Falls back to the
 * legacy `?supplierCode=...&plotCode=...` shape only if a plot genuinely
 * has no qrKey yet (shouldn't happen after the round-20 backfill, but
 * printing must never emit a broken `qr=undefined` link).
 *
 * `baseUrl` is passed in explicitly (not read from import.meta.env/
 * window.location here) so this stays a pure, easily-testable function —
 * see getPublicAppBaseUrl() for how callers resolve it.
 *
 * Never carries an inspection code, session token, or any other secret —
 * the verify-code step still gates record creation exactly as before.
 */
export function buildPlotQrDeepLink(
  item: { qrKey: string | null; supplierCode: string; plotCode: string },
  baseUrl: string,
): string {
  const base = baseUrl.replace(/\/+$/, '');
  const params = item.qrKey
    ? new URLSearchParams({ qr: item.qrKey })
    : new URLSearchParams({ supplierCode: item.supplierCode.trim(), plotCode: item.plotCode.trim() });
  return `${base}/public/inspect?${params.toString()}`;
}

/** Resolves the base URL for QR deep links: an explicit env override for
 * environments where the app is served from a different origin than it's
 * reached at (e.g. behind a reverse proxy), falling back to the browser's
 * own origin — never a hardcoded production domain. */
export function getPublicAppBaseUrl(): string {
  return import.meta.env.VITE_PUBLIC_APP_URL || window.location.origin;
}

/**
 * Reads the plot locator straight off the current page's query string —
 * used by PublicInspect.tsx to prefill and skip ahead when opened via a QR
 * deep link. Prefers `qr` (round 20); falls back to the legacy
 * supplierCode+plotCode pair (both must be present and non-blank). Neither
 * shape present returns null so the caller falls back to the normal
 * scan/manual-entry flow.
 */
export function parseDeepLinkParams(searchParams: URLSearchParams): PlotQrLocator | null {
  const qrKey = searchParams.get('qr')?.trim();
  if (qrKey) return { mode: 'qr', qrKey };
  const supplierCode = searchParams.get('supplierCode')?.trim();
  const plotCode = searchParams.get('plotCode')?.trim();
  if (!supplierCode || !plotCode) return null;
  return { mode: 'legacy', supplierCode, plotCode };
}
