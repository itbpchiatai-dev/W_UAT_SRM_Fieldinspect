/**
 * Field Definitions API — schema-driven form field catalog (Step 12).
 */
import { apiClient } from './client';

export type FieldType =
  | 'score' | 'percent' | 'photo' | 'geo' | 'plot_picker'
  | 'list' | 'date' | 'text' | 'multiline' | 'number' | 'boolean';

export interface FieldDefinition {
  id: string;
  key: string;
  label: string;
  fieldType: FieldType;
  required: boolean;
  optionsSource: string | null;
  options: string[];
  isCore: boolean;
  listDefault: boolean;
  orderIndex: number;
  active: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface FieldDefinitionCreatePayload {
  key: string;
  label: string;
  fieldType: FieldType;
  required?: boolean;
  optionsSource?: string | null;
  options?: string[];
  listDefault?: boolean;
  orderIndex?: number;
}

export type FieldDefinitionUpdatePayload = Partial<{
  label: string;
  required: boolean;
  optionsSource: string | null;
  options: string[];
  listDefault: boolean;
  orderIndex: number;
  active: boolean;
}>;

export const listFieldDefinitions = (activeOnly = false) =>
  apiClient
    .get<FieldDefinition[]>('/api/v1/fielddefs', { params: { activeOnly } })
    .then((r) => r.data);

export const createFieldDefinition = (payload: FieldDefinitionCreatePayload) =>
  apiClient.post<FieldDefinition>('/api/v1/fielddefs', payload).then((r) => r.data);

export const updateFieldDefinition = (id: string, payload: FieldDefinitionUpdatePayload) =>
  apiClient.patch<FieldDefinition>(`/api/v1/fielddefs/${id}`, payload).then((r) => r.data);

export const deleteFieldDefinition = (id: string) =>
  apiClient.delete(`/api/v1/fielddefs/${id}`).then(() => undefined);
