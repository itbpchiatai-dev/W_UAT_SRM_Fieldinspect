import { apiClient } from './client';

export interface CropTypeStat {
  cropType: string | null;
  count: number;
}

export interface DashboardSummary {
  totalRecords: number;
  recordsThisMonth: number;
  avgConditionScore: number | null;
  lowScoreCount: number;
  totalPlots: number;
  totalSuppliers: number | null;
  byCropType: CropTypeStat[];
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const { data } = await apiClient.get<DashboardSummary>('/api/v1/dashboard/summary');
  return data;
}
