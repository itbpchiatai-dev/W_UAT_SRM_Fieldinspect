import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  findProtocolForStage,
  missingProtocolScores,
  fetchInspectionProtocols,
  fetchPublicInspectionProtocols,
  type InspectionProtocolResponse,
} from './inspectionProtocols';

const getLoggedInMock = vi.fn();
const getPublicMock = vi.fn();

vi.mock('./client', () => ({
  apiClient: { get: (...args: unknown[]) => getLoggedInMock(...args) },
}));
vi.mock('./publicInspection', () => ({
  publicApiClient: { get: (...args: unknown[]) => getPublicMock(...args) },
}));

const PROTOCOLS: InspectionProtocolResponse = {
  version: 1,
  stages: [
    {
      growthStage: 'ระยะงอก',
      criteria: [
        { slot: 'fieldPrepScore', label: 'การเตรียมแปลง' },
        { slot: 'weatherScore', label: 'สภาพอากาศ' },
        { slot: 'careScore', label: 'การดูแลรักษา' },
        { slot: 'varietyResistanceScore', label: 'ความต้านทานของสายพันธุ์' },
      ],
    },
    {
      growthStage: 'เจริญเติบโต',
      criteria: [
        { slot: 'fieldPrepScore', label: 'สภาพอากาศ' },
        { slot: 'weatherScore', label: 'การดูแลรักษา' },
        { slot: 'careScore', label: 'ความเสี่ยง' },
        { slot: 'varietyResistanceScore', label: 'สภาพแปลง' },
      ],
    },
  ],
};

beforeEach(() => {
  getLoggedInMock.mockReset();
  getPublicMock.mockReset();
});

describe('findProtocolForStage', () => {
  it('returns the matching stage protocol', () => {
    const p = findProtocolForStage(PROTOCOLS, 'เจริญเติบโต');
    expect(p?.growthStage).toBe('เจริญเติบโต');
    expect(p?.criteria.map((c) => c.label)).toEqual(['สภาพอากาศ', 'การดูแลรักษา', 'ความเสี่ยง', 'สภาพแปลง']);
  });

  it('returns null for a stage with no protocol, for null/empty, and when protocols are undefined', () => {
    expect(findProtocolForStage(PROTOCOLS, 'ตั้งตัว')).toBeNull(); // supplement stage
    expect(findProtocolForStage(PROTOCOLS, null)).toBeNull();
    expect(findProtocolForStage(PROTOCOLS, '')).toBeNull();
    expect(findProtocolForStage(undefined, 'ระยะงอก')).toBeNull();
  });
});

describe('missingProtocolScores', () => {
  const proto = PROTOCOLS.stages[0]; // ระยะงอก, all 4 slots

  it('is empty when there is no protocol (no requirement)', () => {
    expect(missingProtocolScores(null, {})).toEqual([]);
  });

  it('is empty when every protocol slot has a score', () => {
    expect(missingProtocolScores(proto, {
      fieldPrepScore: 8, weatherScore: 7, careScore: 9, varietyResistanceScore: 6,
    })).toEqual([]);
  });

  it('names exactly the slots whose score is null/undefined', () => {
    expect(missingProtocolScores(proto, {
      fieldPrepScore: 8, weatherScore: null, careScore: 9, varietyResistanceScore: undefined,
    })).toEqual(['weatherScore', 'varietyResistanceScore']);
  });
});

describe('fetch helpers hit the right endpoints', () => {
  it('logged-in fetch calls the records.read-gated path via apiClient', async () => {
    getLoggedInMock.mockResolvedValue({ data: PROTOCOLS });
    await expect(fetchInspectionProtocols()).resolves.toEqual(PROTOCOLS);
    expect(getLoggedInMock).toHaveBeenCalledWith('/api/v1/inspection-protocols');
  });

  it('public fetch calls the public path via publicApiClient', async () => {
    getPublicMock.mockResolvedValue({ data: PROTOCOLS });
    await expect(fetchPublicInspectionProtocols()).resolves.toEqual(PROTOCOLS);
    expect(getPublicMock).toHaveBeenCalledWith('/api/v1/public/inspection-protocols');
  });
});
