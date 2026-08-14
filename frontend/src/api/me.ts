/**
 * /me endpoints — current user profile, effective permissions, menu tree.
 * Requires a valid access token (apiClient adds the Authorization header).
 */
import { apiClient } from './client';
import type { MenuItem, User } from '../types/auth';

export async function getMe(): Promise<User> {
  const res = await apiClient.get<User>('/api/v1/me');
  return res.data;
}

export async function getMyPermissions(): Promise<string[]> {
  const res = await apiClient.get<{ permissionKeys: string[] }>('/api/v1/me/permissions');
  return res.data.permissionKeys;
}

export async function getMyMenus(): Promise<MenuItem[]> {
  const res = await apiClient.get<MenuItem[]>('/api/v1/me/menus');
  return res.data;
}
