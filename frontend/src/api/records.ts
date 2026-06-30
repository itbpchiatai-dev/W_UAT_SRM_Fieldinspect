/**
 * Records API — FarmLog field inspection records (Step 12.5: yield/list-driven)
 */
import { apiClient } from './client';

export interface RecordSummary {
  id: string;
  plotId: string;
  supplierId: string;
  recordedById: string;
  recordDate: string;
  crop: string | null;
  variety: string | null;
  growthStage: string | null;
  yieldPct: string | null;
  pestStatus: string | null;
  diseaseStatus: string | null;
  isActive: boolean;
  createdAt: string;
  plotCode: string;
  plotName: string;
  supplierName: string;
}

export interface RecordDetail {
  id: string;
  plotId: string;
  supplierId: string;
  recordedById: string;
  recordedByEmail: string;
  recordedByName: string;
  plotCode: string;
  plotName: string;
  supplierName: string;
  recordDate: string;
  crop: string | null;
  variety: string | null;
  growthStage: string | null;
  plantingDate: string | null;
  yieldPct: string | null;
  weatherCondition: string | null;
  fieldPrepLevel: string | null;
  careLevel: string | null;
  pestStatus: string | null;
  diseaseStatus: string | null;
  weedStatus: string | null;
  irrigationMethod: string | null;
  fertilizer: string | null;
  recommendation: string | null;
  notes: string | null;
  latitude: string | null;
  longitude: string | null;
  photoUrls: string[];
  customFields: Record<string, unknown>;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface RecordListParams {
  plotId?: string;
  supplierId?: string;
  dateFrom?: string;
  dateTo?: string;
  activeOnly?: boolean;
  limit?: number;
  offset?: number;
}

export interface RecordCreatePayload {
  plotId: string;
  supplierId: string;
  recordDate: string;
  crop?: string | null;
  variety?: string | null;
  growthStage?: string | null;
  plantingDate?: string | null;
  yieldPct?: number | null;
  weatherCondition?: string | null;
  fieldPrepLevel?: string | null;
  careLevel?: string | null;
  pestStatus?: string | null;
  diseaseStatus?: string | null;
  weedStatus?: string | null;
  irrigationMethod?: string | null;
  fertilizer?: string | null;
  recommendation?: string | null;
  notes?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  photoUrls?: string[];
  customFields?: Record<string, unknown>;
}

export type RecordUpdatePayload = Partial<RecordCreatePayload> & { isActive?: boolean };

export const listRecords = (params: RecordListParams = {}) =>
  apiClient.get<RecordSummary[]>('/api/v1/records', { params }).then(r => r.data);

export const getRecord = (id: string) =>
  apiClient.get<RecordDetail>(`/api/v1/records/${id}`).then(r => r.data);

export const createRecord = (payload: RecordCreatePayload) =>
  apiClient.post<RecordDetail>('/api/v1/records', payload).then(r => r.data);

export const updateRecord = (id: string, payload: RecordUpdatePayload) =>
  apiClient.patch<RecordDetail>(`/api/v1/records/${id}`, payload).then(r => r.data);

export const deactivateRecord = (id: string) =>
  apiClient.post<RecordDetail>(`/api/v1/records/${id}/deactivate`).then(r => r.data);
