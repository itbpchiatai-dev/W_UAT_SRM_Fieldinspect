/**
 * Menus API — tree CRUD. Reorder = PATCH the two affected nodes with new
 * order_index values (the backend has no /reorder endpoint by design;
 * keeping order in the row itself keeps replication / audit log trivial).
 */
import { apiClient } from './client';
import type { MenuItemTree } from '../types/auth';

export async function listMenus(): Promise<MenuItemTree[]> {
  const res = await apiClient.get<MenuItemTree[]>('/api/v1/menus');
  return res.data;
}

export interface MenuCreatePayload {
  key: string;
  labelTh: string;
  labelEn: string;
  icon?: string | null;
  path: string;
  parentId?: string | null;
  orderIndex?: number;
  requiredPermissionKey: string;
}

export async function createMenu(payload: MenuCreatePayload): Promise<MenuItemTree> {
  const res = await apiClient.post<MenuItemTree>('/api/v1/menus', payload);
  return res.data;
}

export interface MenuUpdatePayload {
  labelTh?: string;
  labelEn?: string;
  icon?: string | null;
  path?: string;
  parentId?: string | null;
  orderIndex?: number;
  requiredPermissionKey?: string;
}

export async function updateMenu(id: string, payload: MenuUpdatePayload): Promise<MenuItemTree> {
  const res = await apiClient.patch<MenuItemTree>(`/api/v1/menus/${id}`, payload);
  return res.data;
}

export async function deleteMenu(id: string): Promise<void> {
  await apiClient.delete(`/api/v1/menus/${id}`);
}

/** Swap two siblings' order_index values. Two sequential PATCH calls —
 *  no transaction guarantee across them, which is intentional: a partial
 *  failure leaves both rows in a valid ordering (just less ideal). */
export async function swapMenuOrder(
  a: { id: string; orderIndex: number },
  b: { id: string; orderIndex: number },
): Promise<void> {
  await updateMenu(a.id, { orderIndex: b.orderIndex });
  await updateMenu(b.id, { orderIndex: a.orderIndex });
}
