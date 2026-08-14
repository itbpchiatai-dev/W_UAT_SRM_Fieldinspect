/**
 * Roles API — list / get / create / update / delete.
 *
 * Backend enforces:
 *   - `internal:` / `external:` prefix matches provider_scope on create.
 *   - System roles (is_system=true) are read-only.
 *   - Delete forbidden if usersCount > 0 (backend returns 400/409).
 */
import { apiClient } from './client';
import type { RoleDetail, RoleSummary } from '../types/auth';

export async function listRoles(): Promise<RoleSummary[]> {
  const res = await apiClient.get<RoleSummary[]>('/api/v1/roles');
  return res.data;
}

export async function getRole(id: string): Promise<RoleDetail> {
  const res = await apiClient.get<RoleDetail>(`/api/v1/roles/${id}`);
  return res.data;
}

export interface RoleCreatePayload {
  name: string;                         // "internal:xyz" or "external:xyz"
  displayName: string;
  providerScope: 'internal' | 'external' | 'any';
  description?: string;
  permissionKeys?: string[];
}

export async function createRole(payload: RoleCreatePayload): Promise<RoleDetail> {
  const res = await apiClient.post<RoleDetail>('/api/v1/roles', payload);
  return res.data;
}

export interface RoleUpdatePayload {
  displayName?: string;
  description?: string | null;
  permissionKeys?: string[];
}

export async function updateRole(id: string, payload: RoleUpdatePayload): Promise<RoleDetail> {
  const res = await apiClient.patch<RoleDetail>(`/api/v1/roles/${id}`, payload);
  return res.data;
}

export async function deleteRole(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/roles/${id}`);
}
