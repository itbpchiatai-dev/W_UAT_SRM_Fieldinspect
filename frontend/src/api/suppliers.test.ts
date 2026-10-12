import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from './client';
import { listSuppliers } from './suppliers';
import type {
  SupplierCreatePayload,
  SupplierDetail,
  SupplierSummary,
  SupplierUpdatePayload,
} from './suppliers';

// Regression: FastAPI's list_suppliers declares `active_only` (snake_case).
// Sending `activeOnly` left it silently ignored, so the endpoint's own
// default of False applied and INACTIVE suppliers came back -- a closed
// Supplier (reported on code 17288) stayed selectable in the Supplier filter
// on Plots, RecordForm, RecordList, both Supplier reports and Users. Same
// class of bug the plots/records query params already carry a guard for.
describe('supplier query params', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listSuppliers sends active_only as snake_case', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listSuppliers({ activeOnly: true, limit: 200 });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/suppliers', {
      params: {
        limit: 200,
        offset: undefined,
        q: undefined,
        active_only: true,
      },
    });
  });

  it('never puts a camelCase activeOnly key on the wire', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listSuppliers({ activeOnly: true });

    const sent = (getSpy.mock.calls[0]?.[1] as { params: Record<string, unknown> }).params;
    expect(sent).not.toHaveProperty('activeOnly');
    expect(sent.active_only).toBe(true);
  });

  it('leaves active_only undefined when the caller omits it, keeping the endpoint default', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listSuppliers({ q: 'ใบข้าว' });

    const sent = (getSpy.mock.calls[0]?.[1] as { params: Record<string, unknown> }).params;
    expect(sent.active_only).toBeUndefined();
    expect(sent.q).toBe('ใบข้าว');
  });
});

describe('Supplier types — inspectionCode retirement (round 8-3G)', () => {
  it('rejects inspectionCode on SupplierCreatePayload', () => {
    // @ts-expect-error inspectionCode must not be assignable to SupplierCreatePayload
    const payload: SupplierCreatePayload = { code: 'SUP001', name: 'Supplier One', inspectionCode: '9999' };
    expect(payload).toBeTruthy();
  });

  it('rejects inspectionCode on SupplierUpdatePayload', () => {
    // @ts-expect-error inspectionCode must not be assignable to SupplierUpdatePayload
    const payload: SupplierUpdatePayload = { inspectionCode: '9999' };
    expect(payload).toBeTruthy();
  });

  it('rejects inspectionCode on SupplierSummary', () => {
    const base: SupplierSummary = {
      id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true,
      contactName: null, contactEmail: null,
    };
    // @ts-expect-error inspectionCode must not be assignable to SupplierSummary
    const summary: SupplierSummary = { ...base, inspectionCode: '1111' };
    expect(summary).toBeTruthy();
  });

  it('rejects inspectionCode on SupplierDetail', () => {
    const base: SupplierDetail = {
      id: 'sup-1', code: 'SUP001', name: 'Supplier One',
      taxId: null, contactName: null, contactEmail: null, contactPhone: null,
      address: null, isActive: true,
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    };
    // @ts-expect-error inspectionCode must not be assignable to SupplierDetail
    const detail: SupplierDetail = { ...base, inspectionCode: '1111' };
    expect(detail).toBeTruthy();
  });
});
