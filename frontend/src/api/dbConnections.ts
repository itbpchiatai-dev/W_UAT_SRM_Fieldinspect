/**
 * Database Connections + Query Sandbox API (Setup).
 *
 * Backs /settings/db-connections (CRUD + test) and /settings/query-sandbox.
 * The password is write-only — it is never present on a read response.
 */
import { apiClient } from './client';

export type SslMode = 'disable' | 'prefer' | 'require' | 'verify-ca' | 'verify-full';

export interface DbConnection {
  id: string;
  name: string;
  description: string | null;
  host: string;
  port: number;
  database: string;
  username: string;
  sslMode: SslMode;
  isActive: boolean;
  allowWrite: boolean;
  lastTestedAt: string | null;
  lastTestStatus: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DbConnectionCreate {
  name: string;
  description?: string | null;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  sslMode: SslMode;
  isActive: boolean;
  allowWrite: boolean;
}

// Partial update; omit `password` to keep the stored one.
export type DbConnectionUpdate = Partial<DbConnectionCreate>;

export interface DbConnectionTestResult {
  success: boolean;
  message: string;
  serverVersion: string | null;
  latencyMs: number | null;
}

export interface DbTable {
  schemaName: string;
  name: string;
  type: 'table' | 'view';
}

export interface QueryRequest {
  sql: string;
  readOnly: boolean;
  limit: number;
}

export interface QueryResult {
  columns: string[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  durationMs: number;
  command: string | null;
  readOnly: boolean;
}

const BASE = '/api/v1/db-connections';

export async function listConnections(): Promise<DbConnection[]> {
  const res = await apiClient.get<DbConnection[]>(BASE);
  return res.data;
}

export async function createConnection(payload: DbConnectionCreate): Promise<DbConnection> {
  const res = await apiClient.post<DbConnection>(BASE, payload);
  return res.data;
}

export async function updateConnection(
  id: string,
  payload: DbConnectionUpdate,
): Promise<DbConnection> {
  const res = await apiClient.put<DbConnection>(`${BASE}/${id}`, payload);
  return res.data;
}

export async function deleteConnection(id: string): Promise<void> {
  await apiClient.delete(`${BASE}/${id}`);
}

export async function testConnection(id: string): Promise<DbConnectionTestResult> {
  const res = await apiClient.post<DbConnectionTestResult>(`${BASE}/${id}/test`);
  return res.data;
}

export async function listTables(id: string): Promise<DbTable[]> {
  const res = await apiClient.get<DbTable[]>(`${BASE}/${id}/tables`);
  return res.data;
}

export async function runQuery(id: string, payload: QueryRequest): Promise<QueryResult> {
  const res = await apiClient.post<QueryResult>(`${BASE}/${id}/query`, payload);
  return res.data;
}
