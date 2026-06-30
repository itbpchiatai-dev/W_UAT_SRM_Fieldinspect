/**
 * Master Data API — editable dropdown option source (Step 12.5).
 */
import { apiClient } from './client';

export interface MasterDataItem {
  id: string;
  type: string;
  value: string;
  parent: string | null;
  orderIndex: number;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MasterDataListParams {
  type?: string;
  parent?: string;
  activeOnly?: boolean;
}

export interface MasterDataCreatePayload {
  type: string;
  value: string;
  parent?: string | null;
  orderIndex?: number;
}

export type MasterDataUpdatePayload = Partial<{
  value: string;
  parent: string | null;
  orderIndex: number;
  active: boolean;
}>;

export const listMasterData = (params: MasterDataListParams = {}) =>
  apiClient.get<MasterDataItem[]>('/api/v1/masterdata', { params }).then((r) => r.data);

export const createMasterData = (payload: MasterDataCreatePayload) =>
  apiClient.post<MasterDataItem>('/api/v1/masterdata', payload).then((r) => r.data);

export const updateMasterData = (id: string, payload: MasterDataUpdatePayload) =>
  apiClient.patch<MasterDataItem>(`/api/v1/masterdata/${id}`, payload).then((r) => r.data);

export const deleteMasterData = (id: string) =>
  apiClient.delete(`/api/v1/masterdata/${id}`).then(() => undefined);
