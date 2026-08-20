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

import {
  listCycleYieldReport, downloadCycleYieldReport,
  listPlotStatus, downloadPlotStatusReport,
} from './reports';

beforeEach(() => getMock.mockReset());

describe('listPlotStatus', () => {
  // Round 8-25D — the on-screen table's page-size selector.
  it('passes limit/offset through unchanged', async () => {
    getMock.mockResolvedValue({ data: [] });
    await listPlotStatus({ crop: 'พริก', limit: 500, offset: 1000 });
    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe('/api/v1/reports/plot-status');
    expect(config.params.limit).toBe(500);
    expect(config.params.offset).toBe(1000);
  });
});

describe('downloadPlotStatusReport', () => {
  // Round 8-25D regression guard — same contract as downloadCycleYieldReport.
  it('never sends limit/offset, even if the caller passes them', async () => {
    getMock.mockResolvedValue({ data: new Blob(['xlsx']) });
    await downloadPlotStatusReport({ crop: 'พริก', limit: 100, offset: 200 });
    const [url, config] = getMock.mock.calls[0];
    expect(url).toBe('/api/v1/reports/plot-status/export');
    expect(config.params.limit).toBeUndefined();
    expect(config.params.offset).toBeUndefined();
  });
});

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
      limit: undefined, offset: undefined,
    });
  });

  it('omits empty params (undefined, not empty string)', async () => {
    getMock.mockResolvedValue({ data: [] });
    await listCycleYieldReport({ status: 'closed' });
    const config = getMock.mock.calls[0][1];
    expect(config.params).toEqual({
      supplier_id: undefined, crop: undefined, status: 'closed',
      date_from: undefined, date_to: undefined,
      limit: undefined, offset: undefined,
    });
  });

  // Round 8-25D — the on-screen table's page-size selector.
  it('passes limit/offset through unchanged', async () => {
    getMock.mockResolvedValue({ data: [] });
    await listCycleYieldReport({ status: 'closed', limit: 500, offset: 1000 });
    const config = getMock.mock.calls[0][1];
    expect(config.params.limit).toBe(500);
    expect(config.params.offset).toBe(1000);
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

  // Round 8-25D regression guard — a downloaded workbook must contain every
  // filtered row, never just the on-screen page, even if a caller passes
  // its pageSize state through by mistake.
  it('never sends limit/offset, even if the caller passes them', async () => {
    getMock.mockResolvedValue({ data: new Blob(['xlsx']) });
    await downloadCycleYieldReport({ status: 'closed', limit: 100, offset: 200 });
    const config = getMock.mock.calls[0][1];
    expect(config.params.limit).toBeUndefined();
    expect(config.params.offset).toBeUndefined();
  });
});
