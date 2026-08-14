/**
 * Reports API — cycle-yield report (round 8-2.8B). The list/download calls must
 * map camelCase params → snake_case query params and hit the right endpoint;
 * download requests a blob.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const getMock = vi.fn();
vi.mock('./client', () => ({
  apiClient: { get: (...a: unknown[]) => getMock(...a) },
}));

import { listCycleYieldReport, downloadCycleYieldReport } from './reports';

beforeEach(() => getMock.mockReset());

describe('listCycleYieldReport', () => {
  it('GETs /reports/cycle-yield and maps params to snake_case', async () => {
    getMock.mockResolvedValue({ data: [] });

    await listCycleYieldReport({
      supplierId: 'sup-1', crop: 'พริก', status: 'harvested',
      dateFrom: '2026-06-01', dateTo: '2026-06-30',
    });

    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe('/api/v1/reports/cycle-yield');
    expect(config.params).toEqual({
      supplier_id: 'sup-1', crop: 'พริก', status: 'harvested',
      date_from: '2026-06-01', date_to: '2026-06-30',
    });
  });

  it('omits empty params (undefined, not empty string)', async () => {
    getMock.mockResolvedValue({ data: [] });
    await listCycleYieldReport({ status: 'closed' });
    const config = getMock.mock.calls[0][1];
    expect(config.params).toEqual({
      supplier_id: undefined, crop: undefined, status: 'closed',
      date_from: undefined, date_to: undefined,
    });
  });
});

describe('downloadCycleYieldReport', () => {
  it('GETs the export endpoint as a blob with the same param mapping', async () => {
    getMock.mockResolvedValue({ data: new Blob(['xlsx']) });

    await downloadCycleYieldReport({ supplierId: 'sup-1', status: 'closed' });

    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe('/api/v1/reports/cycle-yield/export');
    expect(config.responseType).toBe('blob');
    expect(config.params.supplier_id).toBe('sup-1');
    expect(config.params.status).toBe('closed');
  });
});
