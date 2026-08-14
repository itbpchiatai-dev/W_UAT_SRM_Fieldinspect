import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from './client';
import {
  listPlots,
  listPlotProvinces,
  listPlotCycleLabels,
  getPlot,
  listPlotCycles,
  createPlotCycle,
  updatePlotCycle,
  closePlotCycle,
  rolloverPlotCycle,
  downloadPlotImportTemplate,
  commitPlotImport,
  reactivatePlot,
  reactivatePlotWithCycle,
  searchPlotsByPhone,
  PlotImportReportError,
  type PlotCycleRolloverPayload,
  type PlotCycleRolloverResult,
  type PlotImportAction,
  type PlotImportPreviewState,
} from './plots';

// Regression: FastAPI's list_plots / list_plot_provinces declare snake_case
// query params (supplier_id, active_only). Sending camelCase keys makes them
// silently ignored — returning every plot in scope instead of the requested
// supplier/active filter, the same class of bug that polluted a plot's
// inspection history via listRecords.
describe('plots query params', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listPlots sends supplier_id / active_only / q as snake_case', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlots({
      supplierId: 'supplier-1',
      province: 'เชียงใหม่',
      q: 'north',
      activeOnly: true,
      limit: 100,
      offset: 0,
    });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots', {
      params: {
        supplier_id: 'supplier-1',
        province: 'เชียงใหม่',
        q: 'north',
        active_only: true,
        limit: 100,
        offset: 0,
      },
    });
  });

  it('listPlotProvinces sends supplier_id / active_only as snake_case', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotProvinces({ supplierId: 'supplier-1', activeOnly: true });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/provinces', {
      params: { supplier_id: 'supplier-1', active_only: true },
    });
  });

  // Round 8-6I Part B/C — plotStatus is additive alongside activeOnly.
  it('listPlots sends plotStatus as plot_status', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlots({ plotStatus: 'inactive' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots', {
      params: { plot_status: 'inactive' },
    });
  });

  it('listPlotProvinces sends plotStatus as plot_status', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotProvinces({ plotStatus: 'active' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/provinces', {
      params: { plot_status: 'active' },
    });
  });

  // Round 8-18 — "รอบปลูกปัจจุบัน" filter.
  it('listPlots sends cycleLabel as cycle_label', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlots({ cycleLabel: 'jun2026' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots', {
      params: { cycle_label: 'jun2026' },
    });
  });
});

describe('listPlotCycleLabels (round 8-18)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('GETs /api/v1/plots/cycle-labels with supplier_id / plot_status as snake_case', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: ['jun2026', 'aug2026'] });

    const result = await listPlotCycleLabels({ supplierId: 'supplier-1', plotStatus: 'active' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/cycle-labels', {
      params: { supplier_id: 'supplier-1', plot_status: 'active' },
    });
    expect(result).toEqual(['jun2026', 'aug2026']);
  });

  it('works with no params (defaults)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotCycleLabels();

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/cycle-labels', {
      params: { supplier_id: undefined, plot_status: undefined },
    });
  });
});

// Round 8-17A.2 — secure phone search: POST body, never a GET query string
// (Uvicorn's access log records the full request line, query string
// included, for every request — a phone must never land there).
describe('searchPlotsByPhone', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs to /api/v1/plots/search-by-phone with a camelCase body', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: [] });

    await searchPlotsByPhone({
      phone: '0812345678',
      supplierId: 'supplier-1',
      province: 'เชียงใหม่',
      crop: 'พริก',
      variety: 'พริกขี้หนู',
      plotStatus: 'active',
      limit: 20,
      offset: 40,
      cycleLabel: 'jun2026',
      q: 'SUP001-P002',
    });

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/search-by-phone', {
      phone: '0812345678',
      supplierId: 'supplier-1',
      province: 'เชียงใหม่',
      crop: 'พริก',
      variety: 'พริกขี้หนู',
      plotStatus: 'active',
      limit: 20,
      offset: 40,
      cycleLabel: 'jun2026',
      q: 'SUP001-P002',
    });
  });

  // Round 8-18B — the Plots page's two search boxes intersect in ONE
  // request: q rides in the POST body next to the phone, so combining them
  // never forces a fallback to GET /plots?q= (which would put the number in
  // the URL and therefore the access log).
  it('carries q in the POST body, never as a query string', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: [] });
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await searchPlotsByPhone({ phone: '0812345678', q: 'SUP001-P002' });

    const [url, body] = postSpy.mock.calls[0];
    expect(url).toBe('/api/v1/plots/search-by-phone');
    expect((body as { q?: string }).q).toBe('SUP001-P002');
    expect(url).not.toContain('?');
    expect(getSpy).not.toHaveBeenCalled();
  });

  it('omits q when only a number is searched', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: [] });

    await searchPlotsByPhone({ phone: '0812345678' });

    expect((postSpy.mock.calls[0][1] as { q?: string }).q).toBeUndefined();
  });

  it('never uses apiClient.get (no query-string path exists for this call)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: [] });

    await searchPlotsByPhone({ phone: '0812345678' });

    expect(postSpy).toHaveBeenCalled();
    expect(getSpy).not.toHaveBeenCalled();
  });

  it('returns the PlotSummary array from the response body', async () => {
    const plot = { id: 'plot-1', plotCode: 'SUP001-P001' };
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: [plot] });

    const result = await searchPlotsByPhone({ phone: '0812345678' });

    expect(result).toEqual([plot]);
  });
});

// Round 8-6H backend / 8-6I frontend — reactivate a permanently-deactivated
// plot, alone or atomically with its first new cycle.
describe('reactivatePlot / reactivatePlotWithCycle', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('reactivatePlot posts an empty body to POST /api/v1/plots/{plotId}/reactivate', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'plot-1', isActive: true } });

    const result = await reactivatePlot('plot-1');

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/reactivate', {});
    expect(result).toEqual({ id: 'plot-1', isActive: true });
  });

  it('reactivatePlotWithCycle posts the cycle payload to POST /api/v1/plots/{plotId}/reactivate-with-cycle', async () => {
    const responseData = {
      plot: { id: 'plot-1', isActive: true },
      cycle: { id: 'cycle-1', cycleNo: 1, status: 'active' },
    };
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: responseData });
    const payload = {
      poNumber: 'PO25001', pCode: 'Melon-A', crop: 'พริก', variety: 'พริกขี้หนู',
      cycleLabel: 'aug2026', plantCount: 500, expectedYieldFull: 2000, expectedYieldUnit: 'kg',
    };

    const result = await reactivatePlotWithCycle('plot-1', payload);

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/reactivate-with-cycle', payload);
    expect(result).toEqual(responseData);
  });

  it('reactivatePlotWithCycle reuses the same PlotCycleCreatePayload shape as createPlotCycle (no duplicate contract)', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });
    // Minimal valid payload (poNumber/pCode required, everything else optional)
    // — the same shape createPlotCycle/rolloverPlotCycle.newCycle accept.
    const payload = { poNumber: 'PO1', pCode: 'PC1', cycleLabel: 'jun2026' };

    await reactivatePlotWithCycle('plot-2', payload);

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/plot-2/reactivate-with-cycle', payload);
  });
});

// Round 7.3 — plot cycle (รอบปลูก) lifecycle API client. No public
// (unauthenticated) route exists for any of these; they're only reachable
// through apiClient, which always attaches the login session's auth header.
describe('plot cycle API', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listPlotCycles calls GET /api/v1/plots/{plotId}/cycles', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotCycles('plot-1');

    // Round 8-10A — with no options the request is byte-identical to the
    // pre-8-10A one, so the backend's own defaults still apply.
    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/cycles', undefined);
  });

  it('listPlotCycles forwards limit/offset as axios params (round 8-10A)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotCycles('plot-1', { limit: 100, offset: 0 });

    expect(getSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/cycles',
      { params: { limit: 100, offset: 0 } },
    );
    // never hand-built into the URL — axios owns the encoding
    expect(getSpy.mock.calls[0][0]).not.toContain('?');
  });

  it('listPlotCycles omits whichever paging key was not supplied', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listPlotCycles('plot-1', { limit: 25 });

    expect(getSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/cycles',
      { params: { limit: 25 } },
    );
  });

  it('listPlotCycles returns the response body unchanged', async () => {
    const cycles = [
      { id: 'c2', plotId: 'plot-1', cycleNo: 2, status: 'active', cycleLabel: 'jun2026' },
      { id: 'c1', plotId: 'plot-1', cycleNo: 1, status: 'harvested', cycleLabel: null },
    ];
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: cycles });

    // No client-side mapping, sorting or filtering — the backend already
    // orders cycle_no DESC.
    expect(await listPlotCycles('plot-1', { limit: 100, offset: 0 })).toEqual(cycles);
  });

  it('createPlotCycle posts the payload to POST /api/v1/plots/{plotId}/cycles', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });
    const payload = { poNumber: 'PO25001', pCode: 'Melon-A', crop: 'พริก', plantCount: 100, cycleLabel: 'jun2026' };

    await createPlotCycle('plot-1', payload);

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/cycles', payload);
  });

  it('round 8-13B: createPlotCycle accepts poNumber:null or an omitted poNumber — pCode alone is still required by the type', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    // explicit null
    await createPlotCycle('plot-1', { poNumber: null, pCode: 'Melon-A', cycleLabel: 'jun2026' });
    expect(postSpy).toHaveBeenLastCalledWith(
      '/api/v1/plots/plot-1/cycles', expect.objectContaining({ poNumber: null }),
    );

    // omitted entirely — PlotCycleCreatePayload.poNumber is optional (?:).
    await createPlotCycle('plot-1', { pCode: 'Melon-B', cycleLabel: 'jun2026' });
    const [, body] = postSpy.mock.calls[1] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty('poNumber');
    expect(body.pCode).toBe('Melon-B');
  });

  it('updatePlotCycle patches PATCH /api/v1/plots/{plotId}/cycles/{cycleId}', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({ data: {} });
    const payload = { crop: 'ทุเรียน' };

    await updatePlotCycle('plot-1', 'cycle-1', payload);

    expect(patchSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/cycles/cycle-1', payload);
  });

  it('closePlotCycle posts POST /api/v1/plots/{plotId}/cycles/{cycleId}/close', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });
    const payload = { status: 'harvested' as const, closeReason: 'เก็บเกี่ยวแล้ว' };

    await closePlotCycle('plot-1', 'cycle-1', payload);

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/cycles/cycle-1/close', payload);
  });
});

// Round 7.9C — rollover client (backed by the round-7.9B atomic
// close+start endpoint). This must be the ONLY way the frontend calls into
// that transition: never closePlotCycle followed by createPlotCycle as two
// separate requests, since a start failure after a successful close would
// strand the plot with no active cycle.
describe('rolloverPlotCycle', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('posts the full payload to POST /api/v1/plots/{plotId}/cycles/{cycleId}/rollover', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });
    const payload: PlotCycleRolloverPayload = {
      closeStatus: 'harvested',
      closeReason: 'เก็บเกี่ยวแล้ว',
      newCycle: {
        poNumber: 'PO25001', pCode: 'Melon-A', cycleLabel: 'jul2026',
        crop: 'เมล่อน', variety: 'ออเรนจ์', lotNo: 'LOT-02',
        plantingDate: '2026-08-01', plantCount: 500,
        expectedYieldFull: 1500, expectedYieldUnit: 'ผล',
      },
    };

    await rolloverPlotCycle('plot-1', 'cycle-1', payload);

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/cycles/cycle-1/rollover',
      payload,
    );
  });

  it('maps the response onto PlotCycleRolloverResult (closedCycle + newCycle + activeCycle*)', async () => {
    const result: PlotCycleRolloverResult = {
      plotId: 'plot-1',
      activeCycleId: 'cycle-2',
      activeCycleNo: 2,
      closedCycle: {
        id: 'cycle-1', plotId: 'plot-1', cycleNo: 1, status: 'harvested',
        crop: 'พริก', variety: null, cycleLabel: 'may2026', lotNo: null, plantingDate: null,
        poNumber: null, pCode: null, lotNoSource: null, lotRunningNo: null, supplierLotNo: null,
        oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
        plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
        startedAt: '2026-06-01T00:00:00Z', closedAt: '2026-08-01T00:00:00Z',
        closedById: 'user-1', closeReason: 'เก็บเกี่ยวแล้ว',
        finalYieldPct: '80.0', finalEstimatedYield: '800.00', finalInspectionRecordId: 'rec-1',
        harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
        harvestDate: null, finalNote: null,
        createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
      newCycle: {
        id: 'cycle-2', plotId: 'plot-1', cycleNo: 2, status: 'active',
        crop: 'เมล่อน', variety: 'ออเรนจ์', cycleLabel: 'aug2026', lotNo: 'LOT-02',
        poNumber: 'PO25001', pCode: 'Melon-A', lotNoSource: 'auto', lotRunningNo: 2, supplierLotNo: null,
        oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
        plantingDate: '2026-08-01', plantCount: 500,
        expectedYieldFull: '1500.00', expectedYieldUnit: 'ผล',
        startedAt: '2026-08-01T00:00:00Z', closedAt: null,
        closedById: null, closeReason: null,
        finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
        harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
        harvestDate: null, finalNote: null,
        createdAt: '2026-08-01T00:00:00Z', updatedAt: '2026-08-01T00:00:00Z',
      },
    };
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: result });

    const got = await rolloverPlotCycle('plot-1', 'cycle-1', {
      closeStatus: 'harvested', newCycle: { poNumber: 'PO', pCode: 'PC', cycleLabel: 'jun2026' },
    });

    expect(got).toEqual(result);
    expect(got.activeCycleId).toBe('cycle-2');
    expect(got.closedCycle.status).toBe('harvested');
    expect(got.newCycle.cycleNo).toBe(2);
    // round 8.0 — cycleLabel round-trips on both cycles
    expect(got.closedCycle.cycleLabel).toBe('may2026');
    expect(got.newCycle.cycleLabel).toBe('aug2026');
  });
});

// --- round 8-3C: access-phone admin API + no-phone-in-update-payload -------
import {
  getPlotAccessPhones,
  replacePlotAccessPhones,
  createPlotWithCycle,
  type PlotAccessPhoneConfig,
  type PlotAccessPhoneConfigResponse,
  type PlotUpdatePayload,
} from './plots';

// --- round 8-12B: Supplier Lot No round-trips through every cycle payload ---

describe('supplierLotNo contract (round 8-12A/8-12B)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('createPlotCycle sends supplierLotNo in the request body', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await createPlotCycle('plot-1', {
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      lotNo: null, supplierLotNo: 'SUP-OWN-1',
    });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/cycles',
      expect.objectContaining({ supplierLotNo: 'SUP-OWN-1' }),
    );
  });

  it('updatePlotCycle sends supplierLotNo, including an explicit null to clear it', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({ data: {} });

    await updatePlotCycle('plot-1', 'cycle-1', { supplierLotNo: null });

    expect(patchSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/cycles/cycle-1',
      expect.objectContaining({ supplierLotNo: null }),
    );
  });

  it('rolloverPlotCycle carries supplierLotNo inside newCycle', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await rolloverPlotCycle('plot-1', 'cycle-1', {
      closeStatus: 'harvested',
      newCycle: {
        poNumber: 'PO25002', pCode: 'WM-142', cycleLabel: '2606',
        supplierLotNo: 'SUP-OWN-2',
      },
    });

    const [, body] = postSpy.mock.calls[0] as [string, { newCycle: { supplierLotNo?: string | null } }];
    expect(body.newCycle.supplierLotNo).toBe('SUP-OWN-2');
  });

  it('reactivatePlotWithCycle carries supplierLotNo', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await reactivatePlotWithCycle('plot-1', {
      poNumber: 'PO25003', pCode: 'WM-143', cycleLabel: '2607',
      supplierLotNo: 'SUP-OWN-3',
    });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/reactivate-with-cycle',
      expect.objectContaining({ supplierLotNo: 'SUP-OWN-3' }),
    );
  });

  it('a cycle response exposes supplierLotNo unchanged', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: [{ id: 'c1', supplierLotNo: 'SUP-OWN-9', lotNo: '2605-SUP010-WM-141-001' }],
    });

    const cycles = await listPlotCycles('plot-1');

    expect(cycles[0].supplierLotNo).toBe('SUP-OWN-9');
    // the system lot is a SEPARATE field — never merged with the supplier one
    expect(cycles[0].lotNo).toBe('2605-SUP010-WM-141-001');
  });

  it('a plot response exposes activeCycleSupplierLotNo unchanged', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { id: 'p1', activeCycleSupplierLotNo: 'SUP-OWN-5', activeCycleLotNo: '2605-SUP010-WM-141-001' },
    });

    const plot = await getPlot('p1');

    expect(plot.activeCycleSupplierLotNo).toBe('SUP-OWN-5');
    expect(plot.activeCycleLotNo).toBe('2605-SUP010-WM-141-001');
  });

  it('never sends the server-derived lot fields or the internal series key', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await createPlotCycle('plot-1', {
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605', supplierLotNo: 'S-1',
    });

    const [, body] = postSpy.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty('lotNoSource');
    expect(body).not.toHaveProperty('lotRunningNo');
    expect(body).not.toHaveProperty('autoLotSeriesKey');
  });
});

describe('plot access-phone API (round 8-3C)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getPlotAccessPhones calls GET /api/v1/plots/{plotId}/access-phones', async () => {
    const response: PlotAccessPhoneConfigResponse = {
      primaryPhone: '0845552162', additionalPhones: ['0812345678'],
      items: [
        { id: 'row-1', phone: '0845552162', accessType: 'primary', isActive: true, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
      ],
    };
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: response });

    const result = await getPlotAccessPhones('plot-1');

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/access-phones');
    expect(result).toEqual(response);
  });

  it('replacePlotAccessPhones PUTs the full config to /api/v1/plots/{plotId}/access-phones', async () => {
    const payload: PlotAccessPhoneConfig = {
      primaryPhone: '0845552162', additionalPhones: ['0812345678', '0891112222'],
    };
    const putSpy = vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { ...payload, items: [] },
    });

    const result = await replacePlotAccessPhones('plot-1', payload);

    expect(putSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/access-phones', payload);
    expect(result.primaryPhone).toBe('0845552162');
    expect(result.additionalPhones).toEqual(['0812345678', '0891112222']);
  });

  it('createPlotWithCycle forwards accessPhones as part of the SAME request body', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await createPlotWithCycle({
      plot: { supplierId: 'sup-1', plotCode: 'P001', name: 'แปลง A' },
      cycle: { poNumber: 'PO', pCode: 'PC', cycleLabel: 'jun2026' },
      accessPhones: { primaryPhone: '0845552162', additionalPhones: [] },
    });

    expect(postSpy).toHaveBeenCalledWith('/api/v1/plots/with-cycle', {
      plot: { supplierId: 'sup-1', plotCode: 'P001', name: 'แปลง A' },
      cycle: { poNumber: 'PO', pCode: 'PC', cycleLabel: 'jun2026' },
      accessPhones: { primaryPhone: '0845552162', additionalPhones: [] },
    });
    // exactly one POST — no second request for the phones
    expect(postSpy).toHaveBeenCalledTimes(1);
  });

  it('createPlotWithCycle omits accessPhones entirely when not provided', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} });

    await createPlotWithCycle({
      plot: { supplierId: 'sup-1', plotCode: 'P001', name: 'แปลง A' },
      cycle: { poNumber: 'PO', pCode: 'PC', cycleLabel: 'jun2026' },
    });

    const sentBody = postSpy.mock.calls[0][1] as Record<string, unknown>;
    expect(Object.prototype.hasOwnProperty.call(sentBody, 'accessPhones')).toBe(false);
  });

  it('PlotUpdatePayload has no phone fields (phone access is a separate sub-resource)', () => {
    const payload: PlotUpdatePayload = { name: 'แปลง A' };
    expect(payload.name).toBe('แปลง A');

    // Compile-time guard: the type itself must reject these fields — a
    // literal assigning either one must fail to typecheck (excess-property
    // check). If either @ts-expect-error below is no longer an error (i.e.
    // the field was actually added to the type), `tsc --noEmit` fails here.
    // @ts-expect-error PlotUpdatePayload must never carry primaryPhone
    const _withPrimary: PlotUpdatePayload = { name: 'x', primaryPhone: '0845552162' };
    // @ts-expect-error PlotUpdatePayload must never carry additionalPhones
    const _withAdditional: PlotUpdatePayload = { name: 'x', additionalPhones: [] };
    void _withPrimary;
    void _withAdditional;
  });
});

// --- round 8-3E.1 Part D: PlotImportAction must match the backend's 5
// supported actions exactly (was missing start_next_cycle, the action the
// template promotes as the everyday "start next cycle" workflow). ---------
describe('PlotImportAction contract (round 8-3E.1)', () => {
  it('accepts start_next_cycle as a valid PlotImportAction', () => {
    const action: PlotImportAction = 'start_next_cycle';
    expect(action).toBe('start_next_cycle');
  });

  it('still accepts every legacy/promoted action (none removed)', () => {
    const actions: PlotImportAction[] = [
      'create_plot_with_cycle', 'start_new_cycle', 'update_current_cycle',
      'close_and_start_new_cycle', 'start_next_cycle',
    ];
    expect(actions).toHaveLength(5);
  });

  // Round 8-7B — final_plot (round 8-7A) added to the union.
  it('accepts final_plot as a valid PlotImportAction', () => {
    const action: PlotImportAction = 'final_plot';
    expect(action).toBe('final_plot');
  });
});

// --- round 8-7B: final_plot preview-state contract + commitPlotImport's
// (legacy JSON) previewState multipart field. ------------------------------
describe('PlotImportPreviewState finalPlotRows contract (round 8-7A.1/8-7B)', () => {
  it('a preview response carrying finalPlotRows round-trips through the type', () => {
    const state: PlotImportPreviewState = {
      fileSha256: 'a'.repeat(64),
      startNextRows: [],
      finalPlotRows: [
        {
          rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P001',
          plotUpdatedAt: '2026-07-01T00:00:00Z',
          activeCycleId: 'cycle-1', activeCycleNo: 3,
          activeCycleUpdatedAt: '2026-07-01T00:00:00Z',
          cycleLabel: 'jul2026', resolvedFinalInspectionRecordId: 'record-1',
        },
      ],
    };
    expect(state.finalPlotRows?.[0].resolvedFinalInspectionRecordId).toBe('record-1');
  });

  it('finalPlotRows is optional — a pre-8-7A response with only startNextRows still type-checks', () => {
    const state: PlotImportPreviewState = {
      fileSha256: 'b'.repeat(64),
      startNextRows: [
        { rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P001', resolvedAction: 'start_new_cycle', activeCycleId: null },
      ],
    };
    expect(state.finalPlotRows).toBeUndefined();
  });
});

describe('commitPlotImport (legacy JSON) previewState multipart field (round 8-7A.1/8-7B)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('appends previewState as a JSON string multipart field when given', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        createdPlots: 0, startedCycles: 0, updatedCycles: 0, rolledOverCycles: 0,
        finalizedPlots: 1, skippedRows: 0, rowResults: [],
      },
    });
    const file = new File([new Uint8Array([1, 2, 3])], 'import.xlsx');
    const previewState: PlotImportPreviewState = {
      fileSha256: 'c'.repeat(64),
      startNextRows: [],
      finalPlotRows: [{
        rowNumber: 3, supplierCode: 'SUP001', plotCode: 'P001',
        plotUpdatedAt: '2026-07-01T00:00:00Z',
        activeCycleId: 'cycle-1', activeCycleNo: 3,
        activeCycleUpdatedAt: '2026-07-01T00:00:00Z',
        cycleLabel: 'jul2026', resolvedFinalInspectionRecordId: null,
      }],
    };

    await commitPlotImport(file, previewState);

    expect(postSpy).toHaveBeenCalledTimes(1);
    const form = postSpy.mock.calls[0][1] as FormData;
    expect(form.get('previewState')).toBe(JSON.stringify(previewState));
  });

  it('sends no previewState field at all when omitted (legacy compatible)', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        createdPlots: 1, startedCycles: 1, updatedCycles: 0, rolledOverCycles: 0,
        finalizedPlots: 0, skippedRows: 0, rowResults: [],
      },
    });
    const file = new File([new Uint8Array([1, 2, 3])], 'import.xlsx');

    await commitPlotImport(file);

    const form = postSpy.mock.calls[0][1] as FormData;
    expect(form.get('previewState')).toBeNull();
  });
});

// --- round 8-6B: downloadPlotImportTemplate filter contract + Blob error
// normalization. The backend (round 8-6A) takes optional supplier_id/
// province/crop/variety/q — snake_case, same FastAPI-ignores-camelCase
// gotcha as listPlots above (see that describe block's comment). ------------
describe('downloadPlotImportTemplate (round 8-6B)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('no params → the exact pre-8-6A request (no params key at all)', async () => {
    const blob = new Blob(['xlsx-bytes']);
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: blob });

    const result = await downloadPlotImportTemplate();

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/import-template', {
      responseType: 'blob',
    });
    expect(result).toBe(blob);
  });

  it('calling with an empty object also keeps the generic (no-params) request', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({});

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/import-template', {
      responseType: 'blob',
    });
  });

  it('serializes supplierId as supplier_id (never camelCase)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1' });

    const call = getSpy.mock.calls[0];
    expect(call[1]).toMatchObject({ params: { supplier_id: 'sup-1' } });
    expect((call[1] as { params: Record<string, unknown> }).params).not.toHaveProperty('supplierId');
  });

  it('serializes province/crop/variety/q, all set at once', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({
      supplierId: 'sup-1', province: 'เชียงใหม่', crop: 'พริก', variety: 'พริกขี้หนู', q: 'P00',
    });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/import-template', {
      responseType: 'blob',
      params: {
        supplier_id: 'sup-1', province: 'เชียงใหม่', crop: 'พริก', variety: 'พริกขี้หนู', q: 'P00',
      },
    });
  });

  it('omits undefined/empty filter fields instead of sending them blank', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1', province: '', crop: undefined });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toEqual({ supplier_id: 'sup-1' });
    expect(Object.prototype.hasOwnProperty.call(params, 'province')).toBe(false);
    expect(Object.prototype.hasOwnProperty.call(params, 'crop')).toBe(false);
  });

  it('serializes cycleLabel as cycle_label (round 8-18)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1', cycleLabel: 'jun2026' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/import-template', {
      responseType: 'blob',
      params: { supplier_id: 'sup-1', cycle_label: 'jun2026' },
    });
  });

  it('drops cycleLabel when combined with templateMode all_suppliers', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ templateMode: 'all_suppliers', cycleLabel: 'jun2026' });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toEqual({ template_mode: 'all_suppliers' });
    expect(Object.prototype.hasOwnProperty.call(params, 'cycle_label')).toBe(false);
  });

  it('never sends page/limit/offset — the backend exports every match itself', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1' });

    const config = getSpy.mock.calls[0][1] as Record<string, unknown>;
    const params = config.params as Record<string, unknown>;
    expect(params).not.toHaveProperty('page');
    expect(params).not.toHaveProperty('limit');
    expect(params).not.toHaveProperty('offset');
  });

  it('always requests responseType blob', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1' });

    expect(getSpy.mock.calls[0][1]).toMatchObject({ responseType: 'blob' });
  });

  it('a 422 JSON error blob is decoded into a PlotImportReportError with the backend detail + status', async () => {
    const errorBlob = new Blob([JSON.stringify({ detail: 'กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง' })], {
      type: 'application/json',
    });
    vi.spyOn(apiClient, 'get').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 422, data: errorBlob, headers: { 'content-type': 'application/json' } },
      }),
    );

    let caught: unknown;
    try {
      await downloadPlotImportTemplate({ province: 'เชียงใหม่' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(422);
    expect((caught as PlotImportReportError).message).toBe('กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง');
  });

  it('a 404 JSON error blob (out-of-scope Supplier) is decoded with status 404', async () => {
    const errorBlob = new Blob([JSON.stringify({ detail: 'ไม่พบ Supplier' })], { type: 'application/json' });
    vi.spyOn(apiClient, 'get').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 404, data: errorBlob, headers: { 'content-type': 'application/json' } },
      }),
    );

    let caught: unknown;
    try {
      await downloadPlotImportTemplate({ supplierId: 'other-sup' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(404);
    expect((caught as PlotImportReportError).message).toBe('ไม่พบ Supplier');
  });

  it('a network failure with no response at all → PlotImportReportError with status null and a polite fallback message', async () => {
    vi.spyOn(apiClient, 'get').mockRejectedValue(
      Object.assign(new Error('Network Error'), { isAxiosError: true }),
    );

    let caught: unknown;
    try {
      await downloadPlotImportTemplate({ supplierId: 'sup-1' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBeNull();
    expect((caught as PlotImportReportError).message).toBeTruthy();
    expect((caught as PlotImportReportError).message).not.toBe('[object Blob]');
  });

  it('does not affect previewPlotImport/commitPlotImportWithReport (separate helpers, unchanged)', async () => {
    // Sanity: downloadPlotImportTemplate uses apiClient.get; the preview/
    // commit-report helpers use apiClient.post and are untouched by this
    // round — asserting the get-mock here never intercepts a post call.
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });
    const postSpy = vi.spyOn(apiClient, 'post');

    await downloadPlotImportTemplate({ supplierId: 'sup-1' });

    expect(getSpy).toHaveBeenCalled();
    expect(postSpy).not.toHaveBeenCalled();
  });

  // --- round 8-6G: templateMode: 'all_suppliers' ------------------------

  it('templateMode: all_suppliers sends only template_mode=all_suppliers', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ templateMode: 'all_suppliers' });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/import-template', {
      responseType: 'blob',
      params: { template_mode: 'all_suppliers' },
    });
  });

  it('templateMode: all_suppliers drops every other field even if the caller passed both by mistake', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({
      templateMode: 'all_suppliers',
      supplierId: 'sup-1', province: 'เชียงใหม่', crop: 'พริก', variety: 'พริกขี้หนู', q: 'P00',
    });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toEqual({ template_mode: 'all_suppliers' });
  });

  it('a 403 for all_suppliers (out-of-scope caller) is decoded with status 403', async () => {
    const errorBlob = new Blob([JSON.stringify({ detail: 'ไม่มีสิทธิ์ดาวน์โหลด Excel ทุก Supplier' })], {
      type: 'application/json',
    });
    vi.spyOn(apiClient, 'get').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 403, data: errorBlob, headers: { 'content-type': 'application/json' } },
      }),
    );

    let caught: unknown;
    try {
      await downloadPlotImportTemplate({ templateMode: 'all_suppliers' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(403);
    expect((caught as PlotImportReportError).message).toBe('ไม่มีสิทธิ์ดาวน์โหลด Excel ทุก Supplier');
  });

  it('a 422 for all_suppliers combined with a filter is decoded with status 422', async () => {
    const errorBlob = new Blob([JSON.stringify({
      detail: 'ไม่สามารถระบุ Supplier หรือตัวกรองอื่นพร้อมกับการดาวน์โหลดทุก Supplier ได้',
    })], { type: 'application/json' });
    vi.spyOn(apiClient, 'get').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 422, data: errorBlob, headers: { 'content-type': 'application/json' } },
      }),
    );

    let caught: unknown;
    try {
      await downloadPlotImportTemplate({ templateMode: 'all_suppliers', supplierId: 'sup-1' });
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(PlotImportReportError);
    expect((caught as PlotImportReportError).status).toBe(422);
  });

  // --- round 8-6J: plotStatus on both the filtered and all_suppliers modes --

  it('filtered template sends plotStatus as plot_status alongside supplierId', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1', plotStatus: 'inactive' });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toMatchObject({ supplier_id: 'sup-1', plot_status: 'inactive' });
  });

  it('filtered template sends plotStatus: "all"', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1', plotStatus: 'all' });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toMatchObject({ plot_status: 'all' });
  });

  it('templateMode: all_suppliers still sends plotStatus (not one of the "dropped" fields)', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ templateMode: 'all_suppliers', plotStatus: 'inactive' });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toEqual({ template_mode: 'all_suppliers', plot_status: 'inactive' });
  });

  it('templateMode: all_suppliers still never sends supplierId/province/crop/variety/q even with plotStatus set', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({
      templateMode: 'all_suppliers', plotStatus: 'active',
      supplierId: 'sup-1', province: 'เชียงใหม่', crop: 'พริก', variety: 'พริกขี้หนู', q: 'P00',
    });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).toEqual({ template_mode: 'all_suppliers', plot_status: 'active' });
  });

  it('omitting plotStatus never sends plot_status at all', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await downloadPlotImportTemplate({ supplierId: 'sup-1' });

    const params = (getSpy.mock.calls[0][1] as { params: Record<string, unknown> }).params;
    expect(params).not.toHaveProperty('plot_status');
  });
});

// --- round 8-9B: plot inspection password ("รหัสยืนยันแปลง") admin API -----
import {
  getPlotInspectionAccessCredential,
  setPlotInspectionAccessCredential,
  type PlotInspectionCredentialStatus,
} from './plots';

describe('plot inspection credential API (round 8-9B)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getPlotInspectionAccessCredential GETs /api/v1/plots/{plotId}/inspection-access-credential', async () => {
    const response: PlotInspectionCredentialStatus = {
      configured: true, credentialVersion: 3, updatedAt: '2026-08-01T10:00:00Z',
    };
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: response });

    const result = await getPlotInspectionAccessCredential('plot-1');

    expect(getSpy).toHaveBeenCalledWith('/api/v1/plots/plot-1/inspection-access-credential');
    expect(result).toEqual(response);
  });

  it('maps the not-configured response verbatim (nulls stay null)', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { configured: false, credentialVersion: null, updatedAt: null },
    });

    const result = await getPlotInspectionAccessCredential('plot-1');

    expect(result.configured).toBe(false);
    expect(result.credentialVersion).toBeNull();
    expect(result.updatedAt).toBeNull();
  });

  it('setPlotInspectionAccessCredential PUTs { password } as the request body', async () => {
    const putSpy = vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { configured: true, credentialVersion: 1, updatedAt: '2026-08-01T10:00:00Z' },
    });

    const result = await setPlotInspectionAccessCredential('plot-1', '135790');

    expect(putSpy).toHaveBeenCalledWith(
      '/api/v1/plots/plot-1/inspection-access-credential',
      { password: '135790' },
    );
    expect(result.configured).toBe(true);
    expect(result.credentialVersion).toBe(1);
  });

  it('never puts the password in the URL, a query string, or anything but the body', async () => {
    const putSpy = vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { configured: true, credentialVersion: 1, updatedAt: null },
    });

    await setPlotInspectionAccessCredential('plot-1', '135790');

    const [url, body, config] = putSpy.mock.calls[0];
    expect(url).not.toContain('135790');
    expect(url).not.toContain('?');
    expect(body).toEqual({ password: '135790' });   // body only
    expect(config).toBeUndefined();                 // no params/headers carrying it
  });

  it('sends only the password key — no version/confirm/plotId smuggled into the body', async () => {
    const putSpy = vi.spyOn(apiClient, 'put').mockResolvedValue({
      data: { configured: true, credentialVersion: 1, updatedAt: null },
    });

    await setPlotInspectionAccessCredential('plot-1', '482913');

    expect(Object.keys(putSpy.mock.calls[0][1] as object)).toEqual(['password']);
  });
});
