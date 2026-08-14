/**
 * Auth endpoints — login, logout, refresh, SSO redirect/callback.
 *
 * Cookie-based refresh: every call goes through apiClient
 * (withCredentials: true), so the httpOnly refresh cookie tags along
 * automatically. Access token is stored in lib/auth-token.ts.
 */
import { apiClient } from './client';
import type { LoginResponse } from '../types/auth';

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>('/api/v1/auth/login', {
    email: email.trim().toLowerCase(),
    password,
  });
  return res.data;
}

export async function logout(): Promise<void> {
  await apiClient.post('/api/v1/auth/logout', {});
}

export async function refresh(): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>('/api/v1/auth/refresh', {});
  return res.data;
}

export async function ssoRedirect(): Promise<{ url: string }> {
  const res = await apiClient.get<{ url: string }>('/api/v1/auth/sso/redirect');
  return res.data;
}

/**
 * SSO callback — exchange the code + state for an access token.
 * State is mandatory: backend rejects calls without it (CSRF guard via
 * httponly cookie issued at /sso/redirect).
 */
export async function ssoCallback(code: string, state: string): Promise<LoginResponse> {
  const res = await apiClient.post<LoginResponse>(
    '/api/v1/auth/sso/callback', { code, state },
  );
  return res.data;
}
