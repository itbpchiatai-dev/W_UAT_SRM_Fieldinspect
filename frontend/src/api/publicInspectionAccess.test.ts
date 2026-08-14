import { describe, it, expect, vi, beforeEach } from 'vitest';
import { publicApiClient } from './publicInspection';
import {
  lookupPublicInspectionAccess,
  listPublicInspectionAccessPlots,
  selectPublicInspectionPlot,
  getPublicInspectionAccessConfig,
  type PublicInspectionAccessConfig,
  type PublicPhoneAccessLookupResult,
  type PublicSelectPlotResult,
} from './publicInspectionAccess';

const PASSWORD = '135790'; // placeholder test code, never a real one

beforeEach(() => {
  vi.restoreAllMocks();
});

// --- Round 8-9D: capability endpoint ----------------------------------------

describe('getPublicInspectionAccessConfig', () => {
  it('GETs /api/v1/public/inspection-access/config with no body and no params', async () => {
    const config: PublicInspectionAccessConfig = {
      passwordRequired: false, passwordMinLength: 4, passwordMaxLength: 20,
    };
    const getSpy = vi.spyOn(publicApiClient, 'get').mockResolvedValue({ data: config });

    const got = await getPublicInspectionAccessConfig();

    expect(getSpy).toHaveBeenCalledWith('/api/v1/public/inspection-access/config');
    expect(getSpy.mock.calls[0].length).toBe(1);
    expect(got).toEqual(config);
  });

  it('returns passwordRequired=true verbatim (the backend, not the bundle, decides)', async () => {
    vi.spyOn(publicApiClient, 'get').mockResolvedValue({
      data: { passwordRequired: true, passwordMinLength: 4, passwordMaxLength: 20 },
    });

    expect((await getPublicInspectionAccessConfig()).passwordRequired).toBe(true);
  });

  it('uses publicApiClient (no login token) and the response carries no secret', async () => {
    const getSpy = vi.spyOn(publicApiClient, 'get').mockResolvedValue({
      data: { passwordRequired: true, passwordMinLength: 4, passwordMaxLength: 20 },
    });

    const got = await getPublicInspectionAccessConfig();

    expect(getSpy).toHaveBeenCalled();
    expect(Object.keys(got).sort()).toEqual(
      ['passwordMaxLength', 'passwordMinLength', 'passwordRequired'],
    );
    expect(JSON.stringify(got)).not.toMatch(/pepper|hash|digest|credential|phone|token/i);
  });

  it('rejects (never resolves to a phone-only default) when the endpoint fails', async () => {
    vi.spyOn(publicApiClient, 'get').mockRejectedValue({ isAxiosError: true, response: { status: 503 } });

    await expect(getPublicInspectionAccessConfig()).rejects.toBeTruthy();
  });
});

describe('lookupPublicInspectionAccess', () => {
  it('POSTs phone in the body (never a query string) to /inspection-access/lookup', async () => {
    const result: PublicPhoneAccessLookupResult = {
      phoneAccessSessionToken: 'phone-tok', expiresIn: 28800, qrMatchedPlotId: null, plots: [],
    };
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({ data: result });

    await lookupPublicInspectionAccess({ phone: '0845552162' });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/lookup',
      { phone: '0845552162', qrKey: null },
    );
    // never a query-string/params object carrying the phone
    expect(postSpy.mock.calls[0].length).toBe(2);
  });

  it('includes qrKey when provided', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: 'plot-1', plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', qrKey: 'qr-abc' });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/lookup',
      { phone: '0845552162', qrKey: 'qr-abc' },
    );
  });

  it('omits qrKey as null when not provided (optional)', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', qrKey: undefined });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/lookup',
      { phone: '0845552162', qrKey: null },
    );
  });

  it('uses publicApiClient, never the logged-in apiClient', async () => {
    // apiClient carries an Authorization header via its request interceptor;
    // publicApiClient does not — proven structurally by importing from the
    // same module the public verify-code flow already uses.
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });
    await lookupPublicInspectionAccess({ phone: '0845552162' });
    expect(postSpy).toHaveBeenCalled();
  });

  // --- Round 8-9D: password transport --------------------------------------

  it('sends phone + password + qrKey together, all in the POST body', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', password: PASSWORD, qrKey: 'qr-abc' });

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/lookup',
      { phone: '0845552162', qrKey: 'qr-abc', password: PASSWORD },
    );
  });

  it('the password is never in the URL, a query object, or a header', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', password: PASSWORD });

    const [url, body, config] = postSpy.mock.calls[0] as [string, Record<string, unknown>, unknown];
    expect(url).not.toContain(PASSWORD);
    expect(url).toBe('/api/v1/public/inspection-access/lookup');
    // no third argument at all — so no params/headers can carry it
    expect(config).toBeUndefined();
    expect(postSpy.mock.calls[0].length).toBe(2);
    expect(body.password).toBe(PASSWORD);
  });

  it('omits the password key entirely when the caller has none (phone-only mode)', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', password: undefined });

    const body = postSpy.mock.calls[0][1] as Record<string, unknown>;
    expect('password' in body).toBe(false);
    expect(body).toEqual({ phone: '0845552162', qrKey: null });
  });

  it.each([undefined, null, ''])('never sends an empty password (%p)', async (password) => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', password });

    expect('password' in (postSpy.mock.calls[0][1] as Record<string, unknown>)).toBe(false);
  });

  it('the result never echoes the password back', async () => {
    vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });

    const got = await lookupPublicInspectionAccess({ phone: '0845552162', password: PASSWORD });

    expect(JSON.stringify(got)).not.toContain(PASSWORD);
    expect(JSON.stringify(got)).not.toMatch(/password/i);
  });

  it('the module logs nothing at all', async () => {
    // A console.log of the request body would put the password in a place the
    // user can copy out of and a browser extension can read.
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map(
      (m) => vi.spyOn(console, m).mockImplementation(() => {}),
    );
    vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null, plots: [] },
    });
    vi.spyOn(publicApiClient, 'get').mockResolvedValue({
      data: { passwordRequired: true, passwordMinLength: 4, passwordMaxLength: 20 },
    });

    await lookupPublicInspectionAccess({ phone: '0845552162', password: PASSWORD });
    await getPublicInspectionAccessConfig();

    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it('the response type never carries a phone field', async () => {
    const result: PublicPhoneAccessLookupResult = {
      phoneAccessSessionToken: 't', expiresIn: 1, qrMatchedPlotId: null,
      plots: [{
        plotId: 'p1', plotCode: 'P001', plotName: 'Plot One',
        supplierId: 's1', supplierCode: 'SUP001', supplierName: 'Supplier One',
        accessType: 'primary', canInspect: true, unavailableReason: null,
        plotCycleId: 'c1', cycleNo: 1, cycleLabel: 'jun2026',
        crop: 'พริก', variety: null, inspectedToday: false,
        lastInspectionDate: null, lastInspectedAt: null,
        lotNo: null, plantingDate: null,
        plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
        currentYieldPct: null, currentStage: null,
      }],
    };
    vi.spyOn(publicApiClient, 'post').mockResolvedValue({ data: result });

    const got = await lookupPublicInspectionAccess({ phone: '0845552162' });

    expect(JSON.stringify(got)).not.toMatch(/phone(?!AccessSessionToken)/i);
    expect(got).toEqual(result);
  });
});

describe('listPublicInspectionAccessPlots', () => {
  it('POSTs the phoneAccessSessionToken to /inspection-access/plots', async () => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({ data: { plots: [] } });

    await listPublicInspectionAccessPlots('phone-tok');

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/plots',
      { phoneAccessSessionToken: 'phone-tok' },
    );
  });
});

describe('selectPublicInspectionPlot', () => {
  it('POSTs token + plotId + inspectorType to /inspection-access/select-plot', async () => {
    const result: PublicSelectPlotResult = {
      inspectionSessionToken: 'insp-tok', expiresIn: 1800,
      plotId: 'p1', plotCode: 'P001', plotName: 'Plot One',
      supplierId: 's1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCycleId: 'c1', cycleNo: 1, cycleLabel: 'jun2026',
      currentCrop: 'พริก', currentVariety: 'พริกขี้หนู', currentLotNo: 'LOT-01',
      currentPlantingDate: '2026-06-01', plantCount: 1000,
      expectedYieldFull: '800.00', expectedYieldUnit: 'kg',
      currentYieldPct: null, currentStage: null, lastInspectedAt: null,
    };
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({ data: result });

    const got = await selectPublicInspectionPlot('phone-tok', 'p1', 'farmer');

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/public/inspection-access/select-plot',
      { phoneAccessSessionToken: 'phone-tok', plotId: 'p1', inspectorType: 'farmer' },
    );
    expect(got).toEqual(result);
  });

  // Round 8-11A — canonical values sent on the wire (items 18/19).
  it.each(['farmer', 'supplier', 'chiatai'] as const)('accepts inspectorType=%s', async (type) => {
    const postSpy = vi.spyOn(publicApiClient, 'post').mockResolvedValue({
      data: { inspectionSessionToken: 't', expiresIn: 1, plotId: 'p1', plotCode: 'P', plotName: 'P',
        supplierId: 's', supplierCode: 'S', supplierName: 'S', plotCycleId: 'c', cycleNo: 1,
        cycleLabel: null, currentCrop: null, currentVariety: null, currentLotNo: null,
        currentPlantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null },
    });

    await selectPublicInspectionPlot('phone-tok', 'p1', type);

    expect(postSpy).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ inspectorType: type }));
  });

  it('the response type never carries a phone field', async () => {
    const result: PublicSelectPlotResult = {
      inspectionSessionToken: 'insp-tok', expiresIn: 1800,
      plotId: 'p1', plotCode: 'P001', plotName: 'Plot One',
      supplierId: 's1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      plotCycleId: 'c1', cycleNo: 1, cycleLabel: null,
      currentCrop: null, currentVariety: null, currentLotNo: null,
      currentPlantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
      currentYieldPct: null, currentStage: null, lastInspectedAt: null,
    };
    vi.spyOn(publicApiClient, 'post').mockResolvedValue({ data: result });

    const got = await selectPublicInspectionPlot('phone-tok', 'p1', 'supplier');

    expect(JSON.stringify(got)).not.toMatch(/phone(?!AccessSessionToken)/i);
    expect(got).toEqual(result);
  });
});
