/**
 * Public approval API — token-gated approve / reject.
 *
 * NOTE: these endpoints intentionally do NOT use apiClient because
 * apiClient pins an Authorization header from the auth store, which
 * would 401 a logged-out admin clicking the link from email. We use a
 * thin fetch here that talks unauth\u2011enticated to the backend.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export interface PendingUserPublic {
  email: string;
  fullName: string;
  requestedAt: string;
  expiresAt: string;
}

export interface TokenStatus {
  status: 'valid' | 'expired' | 'consumed' | 'not_found' | 'approved' | 'rejected';
  pending: PendingUserPublic | null;
}

async function _request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok && res.status !== 410) {
    throw new Error(`${res.status}`);
  }
  return res.json();
}

export async function resolveToken(token: string): Promise<TokenStatus> {
  return _request<TokenStatus>(`/api/v1/users/approval/${encodeURIComponent(token)}`);
}

export async function approveViaToken(
  token: string, replyMessage: string,
): Promise<TokenStatus> {
  return _request<TokenStatus>(
    `/api/v1/users/approval/${encodeURIComponent(token)}/approve`,
    { method: 'POST', body: JSON.stringify({ replyMessage }) },
  );
}

export async function rejectViaToken(
  token: string, reason: string, replyMessage: string,
): Promise<TokenStatus> {
  return _request<TokenStatus>(
    `/api/v1/users/approval/${encodeURIComponent(token)}/reject`,
    { method: 'POST', body: JSON.stringify({ reason, replyMessage }) },
  );
}
