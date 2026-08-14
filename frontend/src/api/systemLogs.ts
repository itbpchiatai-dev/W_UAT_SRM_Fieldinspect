/**
 * SystemLogs API — read-only list endpoint.
 *
 * Server-side pagination via limit+offset. Optional filters: status
 * (success / failure / warning / info / started) and category (free-text
 * exact match — backend uses `category = :v`).
 */
import { apiClient } from './client';
import type { SystemLog } from '../types/auth';

export interface SystemLogListParams {
  limit?: number;
  offset?: number;
  status?: string;
  category?: string;
  q?: string;
  /** ISO date string YYYY-MM-DD (UTC start-of-day) */
  dateFrom?: string;
  /** ISO date string YYYY-MM-DD (inclusive end-of-day) */
  dateTo?: string;
}

function toSnake(params: SystemLogListParams): Record<string, unknown> {
  return {
    limit: params.limit,
    offset: params.offset,
    status: params.status,
    category: params.category,
    q: params.q,
    date_from: params.dateFrom,
    date_to: params.dateTo,
  };
}

export async function listSystemLogs(params: SystemLogListParams = {}): Promise<SystemLog[]> {
  const res = await apiClient.get<SystemLog[]>('/api/v1/admin/system-logs', {
    params: toSnake(params),
  });
  return res.data;
}

/** Trigger CSV download (server-side filter applied via same params).
 *  Uses fetch+blob so the auth header travels — direct anchor href would
 *  drop the bearer token and 401. */
export async function downloadSystemLogsCsv(
  params: Omit<SystemLogListParams, 'limit' | 'offset'> = {},
): Promise<void> {
  const res = await apiClient.get<Blob>('/api/v1/admin/system-logs/export.csv', {
    params: toSnake(params),
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = url;
  a.download = `system-logs-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}
