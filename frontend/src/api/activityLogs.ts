/**
 * ActivityLogs API — read-only audit trail list.
 *
 * Default view is the security-event subset (loginOnly=true) because
 * that's what the admin landing page is for; toggle off to see the
 * full audit stream (create/update/delete/export/...).
 */
import { apiClient } from './client';
import type { ActivityLog } from '../types/auth';

export interface ActivityLogListParams {
  limit?: number;
  offset?: number;
  actionType?: string;
  userId?: string;
  riskLevel?: string;
  securityOnly?: boolean;
  loginOnly?: boolean;
  q?: string;
  /** ISO date string YYYY-MM-DD (UTC start-of-day) */
  dateFrom?: string;
  /** ISO date string YYYY-MM-DD (inclusive end-of-day) */
  dateTo?: string;
}

/** camelCase → snake_case for FastAPI query params. */
function toSnake(params: ActivityLogListParams): Record<string, unknown> {
  return {
    limit: params.limit,
    offset: params.offset,
    action_type: params.actionType,
    user_id: params.userId,
    risk_level: params.riskLevel,
    security_only: params.securityOnly,
    login_only: params.loginOnly,
    q: params.q,
    date_from: params.dateFrom,
    date_to: params.dateTo,
  };
}

export async function listActivityLogs(
  params: ActivityLogListParams = {},
): Promise<ActivityLog[]> {
  const res = await apiClient.get<ActivityLog[]>('/api/v1/admin/activity-logs', {
    params: toSnake(params),
  });
  return res.data;
}

/** Trigger CSV download — auth header survives because we fetch+blob. */
export async function downloadActivityLogsCsv(
  params: Omit<ActivityLogListParams, 'limit' | 'offset'> = {},
): Promise<void> {
  const res = await apiClient.get<Blob>('/api/v1/admin/activity-logs/export.csv', {
    params: toSnake(params),
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = url;
  a.download = `activity-logs-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.URL.revokeObjectURL(url);
}
