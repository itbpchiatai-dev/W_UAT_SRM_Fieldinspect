/**
 * Permissions catalog — read-only. The catalog is small and rarely
 * changes; pages cache the full list and group locally.
 */
import { apiClient } from './client';
import type { Permission } from '../types/auth';

export async function listPermissions(): Promise<Permission[]> {
  const res = await apiClient.get<Permission[]>('/api/v1/permissions');
  return res.data;
}

/** Group a flat permission list by category — used by Roles + Permissions
 *  pages to render section headers without recomputing for each row. */
export function groupByCategory(perms: Permission[]): Record<string, Permission[]> {
  const out: Record<string, Permission[]> = {};
  for (const p of perms) {
    (out[p.category] ??= []).push(p);
  }
  for (const k of Object.keys(out)) {
    out[k]!.sort((a, b) => a.key.localeCompare(b.key));
  }
  return out;
}
