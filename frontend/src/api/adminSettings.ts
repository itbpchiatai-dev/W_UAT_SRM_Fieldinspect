/**
 * Admin app-settings (Pattern C).
 *
 * `getPublicAuthSettings` returns a tightly-scoped subset (auth.local.enabled,
 * auth.sso.enabled) that is readable WITHOUT a token — the Login page
 * uses it to decide which sign-in methods to show.
 *
 * `listAllSettings` + `updateSetting` are admin-only and back the
 * /settings/auth admin page (Phase C). Each PUT returns the updated row
 * so callers can invalidate their TanStack Query cache straight from the
 * response.
 */
import { apiClient } from './client';
import type { AppSettingValue, PublicAuthSettings } from '../types/auth';

export async function getPublicAuthSettings(): Promise<PublicAuthSettings> {
  const res = await apiClient.get<PublicAuthSettings>('/api/v1/admin/settings/public');
  return res.data;
}

export async function listAllSettings(): Promise<AppSettingValue[]> {
  const res = await apiClient.get<AppSettingValue[]>('/api/v1/admin/settings');
  return res.data;
}

export async function updateSetting(
  key: string,
  value: unknown,
): Promise<AppSettingValue> {
  const res = await apiClient.put<AppSettingValue>(
    `/api/v1/admin/settings/${encodeURIComponent(key)}`,
    { value },
  );
  return res.data;
}
