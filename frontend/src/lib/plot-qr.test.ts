import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  buildPlotQrPayload,
  buildPlotQrDeepLink,
  getPublicAppBaseUrl,
  parseDeepLinkParams,
  parsePlotQr,
} from './plot-qr';

describe('parsePlotQr', () => {
  it('parses a URL deep link with the round-20 opaque qr key', () => {
    expect(parsePlotQr('https://app.example.com/public/inspect?qr=abc123XYZ')).toEqual({
      mode: 'qr',
      qrKey: 'abc123XYZ',
    });
  });

  it('parses a legacy URL deep link (round 17.0, pre-round-20)', () => {
    expect(parsePlotQr('https://app.example.com/public/inspect?supplierCode=SUP001&plotCode=PLOT001')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
  });

  it('parses a legacy deep link with extra/reordered query params', () => {
    expect(parsePlotQr('http://localhost:5173/public/inspect?plotCode=PLOT001&supplierCode=SUP001&utm_source=sign')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
  });

  it('prefers qr over supplierCode/plotCode when a URL somehow carries both', () => {
    expect(parsePlotQr('https://app.example.com/public/inspect?qr=abc123&supplierCode=SUP001&plotCode=PLOT001')).toEqual({
      mode: 'qr',
      qrKey: 'abc123',
    });
  });

  it('returns null for a deep link missing every recognized query param', () => {
    expect(parsePlotQr('https://app.example.com/public/inspect?supplierCode=SUP001')).toBeNull();
    expect(parsePlotQr('https://app.example.com/public/inspect')).toBeNull();
  });

  it('returns null for a malformed URL', () => {
    expect(parsePlotQr('https://')).toBeNull();
  });

  it('parses the JSON format (legacy — never carries a qr key)', () => {
    expect(parsePlotQr('{"supplierCode":"SUP001","plotCode":"PLOT001"}')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
  });

  it('parses the short pipe-delimited format (legacy)', () => {
    expect(parsePlotQr('SUP001|PLOT001')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
  });

  it('trims whitespace around values', () => {
    expect(parsePlotQr('  SUP001 | PLOT001  ')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
    expect(parsePlotQr('{"supplierCode":" SUP001 ","plotCode":" PLOT001 "}')).toEqual({
      mode: 'legacy',
      supplierCode: 'SUP001',
      plotCode: 'PLOT001',
    });
  });

  it('returns null for malformed JSON', () => {
    expect(parsePlotQr('{"supplierCode":"SUP001"')).toBeNull();
  });

  it('returns null when JSON is missing a required field', () => {
    expect(parsePlotQr('{"supplierCode":"SUP001"}')).toBeNull();
    expect(parsePlotQr('{"plotCode":"PLOT001"}')).toBeNull();
  });

  it('returns null for pipe text with the wrong number of segments', () => {
    expect(parsePlotQr('SUP001')).toBeNull();
    expect(parsePlotQr('SUP001|PLOT001|EXTRA')).toBeNull();
  });

  it('returns null for empty or unrelated text', () => {
    expect(parsePlotQr('')).toBeNull();
    expect(parsePlotQr('   ')).toBeNull();
    expect(parsePlotQr('just some random text')).toBeNull();
  });
});

describe('buildPlotQrPayload', () => {
  it('joins supplierCode and plotCode with a pipe', () => {
    expect(buildPlotQrPayload('SUP001', 'PLOT001')).toBe('SUP001|PLOT001');
  });

  it('trims whitespace from both parts', () => {
    expect(buildPlotQrPayload(' SUP001 ', ' PLOT001 ')).toBe('SUP001|PLOT001');
  });

  it('round-trips through parsePlotQr', () => {
    const payload = buildPlotQrPayload('SUP002', 'PLOT045');
    expect(parsePlotQr(payload)).toEqual({ mode: 'legacy', supplierCode: 'SUP002', plotCode: 'PLOT045' });
  });
});

describe('buildPlotQrDeepLink', () => {
  it('builds a /public/inspect URL with the opaque qr key when the plot has one', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: 'opaque-key-123', supplierCode: 'SUP001', plotCode: 'SUP001-P001' },
      'https://app.example.com',
    );
    expect(url).toBe('https://app.example.com/public/inspect?qr=opaque-key-123');
  });

  it('never leaks supplierCode/plotCode into the URL when qrKey is set', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: 'opaque-key-123', supplierCode: 'SUP001', plotCode: 'SUP001-P001' },
      'https://app.example.com',
    );
    expect(url).not.toContain('SUP001');
  });

  it('falls back to the legacy supplierCode/plotCode shape when qrKey is null', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: null, supplierCode: 'SUP001', plotCode: 'PLOT001' },
      'https://app.example.com',
    );
    expect(url).toBe('https://app.example.com/public/inspect?supplierCode=SUP001&plotCode=PLOT001');
  });

  it('strips a trailing slash from baseUrl', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: 'k1', supplierCode: 'SUP001', plotCode: 'PLOT001' },
      'https://app.example.com/',
    );
    expect(url).toBe('https://app.example.com/public/inspect?qr=k1');
  });

  it('never includes an inspection code, token, or secret', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: 'k1', supplierCode: 'SUP001', plotCode: 'PLOT001' },
      'https://app.example.com',
    );
    expect(url.toLowerCase()).not.toMatch(/inspectioncode|token|secret/);
  });

  it('round-trips through parsePlotQr', () => {
    const url = buildPlotQrDeepLink(
      { qrKey: 'opaque-key-456', supplierCode: 'SUP002', plotCode: 'PLOT045' },
      'https://app.example.com',
    );
    expect(parsePlotQr(url)).toEqual({ mode: 'qr', qrKey: 'opaque-key-456' });
  });
});

describe('getPublicAppBaseUrl', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('falls back to window.location.origin when VITE_PUBLIC_APP_URL is unset', () => {
    vi.stubEnv('VITE_PUBLIC_APP_URL', '');
    expect(getPublicAppBaseUrl()).toBe(window.location.origin);
  });

  it('uses VITE_PUBLIC_APP_URL when set', () => {
    vi.stubEnv('VITE_PUBLIC_APP_URL', 'https://configured.example.com');
    expect(getPublicAppBaseUrl()).toBe('https://configured.example.com');
  });
});

describe('parseDeepLinkParams', () => {
  it('reads the round-20 opaque qr key from URLSearchParams', () => {
    const params = new URLSearchParams('qr=abc123');
    expect(parseDeepLinkParams(params)).toEqual({ mode: 'qr', qrKey: 'abc123' });
  });

  it('reads legacy supplierCode/plotCode when qr is absent', () => {
    const params = new URLSearchParams('supplierCode=SUP001&plotCode=PLOT001');
    expect(parseDeepLinkParams(params)).toEqual({ mode: 'legacy', supplierCode: 'SUP001', plotCode: 'PLOT001' });
  });

  it('prefers qr over legacy params when both are present', () => {
    const params = new URLSearchParams('qr=abc123&supplierCode=SUP001&plotCode=PLOT001');
    expect(parseDeepLinkParams(params)).toEqual({ mode: 'qr', qrKey: 'abc123' });
  });

  it('returns null when nothing usable is present', () => {
    expect(parseDeepLinkParams(new URLSearchParams('supplierCode=SUP001'))).toBeNull();
    expect(parseDeepLinkParams(new URLSearchParams('plotCode=PLOT001'))).toBeNull();
    expect(parseDeepLinkParams(new URLSearchParams())).toBeNull();
  });

  it('returns null when a legacy param is present but blank', () => {
    expect(parseDeepLinkParams(new URLSearchParams('supplierCode=&plotCode=PLOT001'))).toBeNull();
  });

  it('trims whitespace', () => {
    const params = new URLSearchParams('supplierCode=%20SUP001%20&plotCode=%20PLOT001%20');
    expect(parseDeepLinkParams(params)).toEqual({ mode: 'legacy', supplierCode: 'SUP001', plotCode: 'PLOT001' });
  });
});
