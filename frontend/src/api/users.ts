/**
 * Users API — list / get / create / update / deactivate + per-user
 * permission overrides (Pattern B).
 *
 * The list endpoint is paginated via `limit` + `offset`. Backend returns
 * UserSummary rows; the detail endpoint hydrates roles + override list.
 */
import { apiClient } from './client';
import type { UserDetail, UserOverride, UserSummary } from '../types/auth';

export interface UserListParams {
  limit?: number;
  offset?: number;
  q?: string;
}

export async function listUsers(params: UserListParams = {}): Promise<UserSummary[]> {
  const res = await apiClient.get<UserSummary[]>('/api/v1/users', { params });
  return res.data;
}

export async function getUser(id: string): Promise<UserDetail> {
  const res = await apiClient.get<UserDetail>(`/api/v1/users/${id}`);
  return res.data;
}

export interface UserCreatePayload {
  email: string;
  fullName: string;
  authProvider: 'local' | 'azure_ad';
  password?: string;
  roleNames?: string[];
  businessUnitIds?: string[];
  supplierId?: string | null;
  isSupplierAdmin?: boolean;
}

export async function createUser(payload: UserCreatePayload): Promise<UserDetail> {
  const res = await apiClient.post<UserDetail>('/api/v1/users', payload);
  return res.data;
}

export interface UserUpdatePayload {
  fullName?: string;
  isActive?: boolean;
  isApproved?: boolean;
  roleNames?: string[];
  businessUnitIds?: string[];
  supplierId?: string | null;
  isSupplierAdmin?: boolean | null;
}

export async function updateUser(id: string, payload: UserUpdatePayload): Promise<UserDetail> {
  const res = await apiClient.patch<UserDetail>(`/api/v1/users/${id}`, payload);
  return res.data;
}

export async function deactivateUser(id: string): Promise<void> {
  await apiClient.post(`/api/v1/users/${id}/deactivate`, {});
}

export async function bulkApproveUsers(userIds: string[]): Promise<{ updated: number }> {
  const res = await apiClient.post<{ updated: number }>('/api/v1/users/bulk-approve', { userIds });
  return res.data;
}

export async function getUserOverrides(id: string): Promise<UserOverride[]> {
  // Backend currently embeds overrides into UserDetail; this helper is the
  // forward-compat shim for when /users/{id}/overrides ships as a sub-route.
  const detail = await getUser(id);
  // UserDetail does not yet ship a typed `overrides` field; cast to any to
  // read it. Pattern B endpoint may evolve — this helper insulates callers.
  const raw = (detail as unknown as { overrides?: UserOverride[] }).overrides ?? [];
  return raw;
}

export async function setUserOverride(
  id: string,
  permissionKey: string,
  granted: boolean,
  reason?: string,
): Promise<UserDetail> {
  const res = await apiClient.post<UserDetail>(`/api/v1/users/${id}/overrides`, {
    permissionKey,
    granted,
    reason: reason ?? null,
  });
  return res.data;
}
