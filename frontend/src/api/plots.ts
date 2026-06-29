/**
 * Plots API — list / get / create / update / deactivate / assign users
 */
import { apiClient } from './client';

export interface AssignedUser {
  userId: string;
  email: string;
  fullName: string;
  assignedAt: string;
}

export interface PlotSummary {
  id: string;
  supplierId: string;
  plotCode: string;
  name: string;
  province: string | null;
  latitude: number | null;
  longitude: number | null;
  isActive: boolean;
  assignedCount: number;
}

export interface PlotDetail {
  id: string;
  supplierId: string;
  plotCode: string;
  name: string;
  village: string | null;
  district: string | null;
  province: string | null;
  latitude: number | null;
  longitude: number | null;
  rai: number | null;
  isActive: boolean;
  assignedUsers: AssignedUser[];
  createdAt: string;
  updatedAt: string;
}

export interface PlotListParams {
  supplierId?: string;
  limit?: number;
  offset?: number;
  q?: string;
  activeOnly?: boolean;
}

export interface PlotCreatePayload {
  supplierId: string;
  plotCode: string;
  name: string;
  village?: string | null;
  district?: string | null;
  province?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  rai?: number | null;
}

export interface PlotUpdatePayload {
  name?: string;
  village?: string | null;
  district?: string | null;
  province?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  rai?: number | null;
  isActive?: boolean;
}

export async function listPlots(params: PlotListParams = {}): Promise<PlotSummary[]> {
  const res = await apiClient.get<PlotSummary[]>('/api/v1/plots', { params });
  return res.data;
}

export async function getPlot(id: string): Promise<PlotDetail> {
  const res = await apiClient.get<PlotDetail>(`/api/v1/plots/${id}`);
  return res.data;
}

export async function createPlot(payload: PlotCreatePayload): Promise<PlotDetail> {
  const res = await apiClient.post<PlotDetail>('/api/v1/plots', payload);
  return res.data;
}

export async function updatePlot(id: string, payload: PlotUpdatePayload): Promise<PlotDetail> {
  const res = await apiClient.patch<PlotDetail>(`/api/v1/plots/${id}`, payload);
  return res.data;
}

export async function deactivatePlot(id: string): Promise<PlotDetail> {
  const res = await apiClient.post<PlotDetail>(`/api/v1/plots/${id}/deactivate`, {});
  return res.data;
}

export async function assignPlotUsers(id: string, userIds: string[]): Promise<PlotDetail> {
  const res = await apiClient.put<PlotDetail>(`/api/v1/plots/${id}/assignments`, { userIds });
  return res.data;
}
