import { describe, it, expect } from 'vitest';
import type {
  SupplierCreatePayload,
  SupplierDetail,
  SupplierSummary,
  SupplierUpdatePayload,
} from './suppliers';

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
