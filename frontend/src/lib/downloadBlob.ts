/**
 * Trigger a browser download from an in-memory Blob (round 8-2.5) — creates a
 * temporary object URL + anchor, clicks it, then revokes the URL. Safe to
 * call repeatedly on the SAME Blob (e.g. re-downloading a stored report):
 * each call gets its own fresh object URL and only revokes that one, so the
 * caller should keep the Blob itself in state, never the object URL.
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
