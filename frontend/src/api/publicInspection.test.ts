import { describe, it, expect } from 'vitest';
import {
  buildPublicRecordPayload,
  buildPublicRecordWithPhotosFormData,
  buildOfflinePublicRecordPayload,
  type PublicInspectionFormFields,
  type PublicRecordCreateResult,
} from './publicInspection';

const EMPTY_FIELDS: PublicInspectionFormFields = {
  submittedByName: '',
  growthStage: '',
  yieldPct: 100,
  yieldQuantityKg: null,
  weatherCondition: '',
  fieldPrepScore: null,
  weatherScore: null,
  careScore: null,
  varietyResistanceScore: null,
  recommendation: '',
  notes: '',
  latitude: null,
  longitude: null,
};

describe('buildPublicRecordPayload', () => {
  it('carries the token and record date through unchanged', () => {
    const payload = buildPublicRecordPayload('tok-123', '2026-07-01', EMPTY_FIELDS);
    expect(payload.inspectionSessionToken).toBe('tok-123');
    expect(payload.recordDate).toBe('2026-07-01');
  });

  it('converts blank optional strings to null', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    expect(payload.submittedByName).toBeNull();
    expect(payload.growthStage).toBeNull();
    expect(payload.weatherCondition).toBeNull();
    expect(payload.recommendation).toBeNull();
    expect(payload.notes).toBeNull();
  });

  it('converts whitespace-only optional strings to null too', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', {
      ...EMPTY_FIELDS,
      submittedByName: '   ',
      notes: '   ',
    });
    expect(payload.submittedByName).toBeNull();
    expect(payload.notes).toBeNull();
  });

  it('preserves provided values without trimming meaningful content', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', {
      ...EMPTY_FIELDS,
      submittedByName: 'Somchai',
      growthStage: 'ออกดอก',
      yieldPct: 80,
      weatherCondition: 'แจ่มใส',
      fieldPrepScore: 8,
      weatherScore: 7,
      careScore: 6,
      varietyResistanceScore: 5,
      recommendation: 'rec',
      notes: 'note',
      latitude: 13.7563,
      longitude: 100.5018,
    });
    expect(payload).toMatchObject({
      growthStage: 'ออกดอก',
      yieldPct: 80,
      weatherCondition: 'แจ่มใส',
      fieldPrepScore: 8,
      weatherScore: 7,
      careScore: 6,
      varietyResistanceScore: 5,
      recommendation: 'rec',
      notes: 'note',
      latitude: 13.7563,
      longitude: 100.5018,
    });
  });

  it('passes null scores/gps through unchanged (not coerced to 0)', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    expect(payload.fieldPrepScore).toBeNull();
    expect(payload.weatherScore).toBeNull();
    expect(payload.careScore).toBeNull();
    expect(payload.varietyResistanceScore).toBeNull();
    expect(payload.latitude).toBeNull();
    expect(payload.longitude).toBeNull();
  });

  it('never produces plotId, supplierId, or recordedById keys', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS) as unknown as Record<string, unknown>;
    expect(payload).not.toHaveProperty('plotId');
    expect(payload).not.toHaveProperty('supplierId');
    expect(payload).not.toHaveProperty('recordedById');
    expect(payload).not.toHaveProperty('crop');
    expect(payload).not.toHaveProperty('variety');
    expect(payload).not.toHaveProperty('plantingDate');
  });

  it('never produces a submittedByCode key (retired round 8-3G)', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS) as unknown as Record<string, unknown>;
    expect(payload).not.toHaveProperty('submittedByCode');
  });

  // --- round 8-8B: kg-first Yield field ------------------------------------

  it('carries yieldQuantityKg through unchanged', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', {
      ...EMPTY_FIELDS, yieldQuantityKg: 750, yieldPct: 75,
    });
    expect(payload.yieldQuantityKg).toBe(750);
    expect(payload.yieldPct).toBe(75);
  });

  it('a null yieldQuantityKg (no comparable target) still passes through as null, never a faked 100', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', {
      ...EMPTY_FIELDS, yieldQuantityKg: null, yieldPct: null,
    });
    expect(payload.yieldQuantityKg).toBeNull();
    expect(payload.yieldPct).toBeNull();
  });

  it('never produces a yieldTargetKgSnapshot key — server-derived only, never client-writable', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS) as unknown as Record<string, unknown>;
    expect(payload).not.toHaveProperty('yieldTargetKgSnapshot');
  });
});

describe('PublicInspectionFormFields / PublicRecordCreatePayload contract (round 8-3G)', () => {
  it('rejects submittedByCode on the form-fields type', () => {
    // @ts-expect-error submittedByCode must not be assignable to PublicInspectionFormFields
    const fields: PublicInspectionFormFields = { ...EMPTY_FIELDS, submittedByCode: 'FIELD01' };
    expect(fields).toBeTruthy();
  });
});

function jpegFile(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

describe('buildPublicRecordWithPhotosFormData', () => {
  it('puts the JSON-encoded payload (including the token) under "payload"', () => {
    const payload = buildPublicRecordPayload('tok-123', '2026-07-01', EMPTY_FIELDS);
    const formData = buildPublicRecordWithPhotosFormData(payload, []);

    const raw = formData.get('payload');
    expect(typeof raw).toBe('string');
    const parsed = JSON.parse(raw as string);
    expect(parsed.inspectionSessionToken).toBe('tok-123');
  });

  it('appends exactly the given files under "photos"', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    const photos = [jpegFile('a.jpg'), jpegFile('b.jpg'), jpegFile('c.jpg'), jpegFile('d.jpg')];
    const formData = buildPublicRecordWithPhotosFormData(payload, photos);

    expect(formData.getAll('photos')).toHaveLength(4);
  });

  it('the JSON payload field never carries plotId, supplierId, or recordedById', () => {
    const payload = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    const formData = buildPublicRecordWithPhotosFormData(payload, []);
    const parsed = JSON.parse(formData.get('payload') as string) as Record<string, unknown>;

    expect(parsed).not.toHaveProperty('plotId');
    expect(parsed).not.toHaveProperty('supplierId');
    expect(parsed).not.toHaveProperty('recordedById');
    expect(parsed).not.toHaveProperty('crop');
    expect(parsed).not.toHaveProperty('variety');
    expect(parsed).not.toHaveProperty('plantingDate');
  });
});

// --- buildOfflinePublicRecordPayload (round 8-4B) ---------------------------

describe('buildOfflinePublicRecordPayload', () => {
  const IDENTITY = {
    clientSubmissionId: 'sub-123',
    capturedAt: '2026-07-15T09:30:00.000Z',
    capturedPlotCycleId: 'cycle-456',
  };

  it('carries every field buildPublicRecordPayload would, unchanged', () => {
    const online = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    const offline = buildOfflinePublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS, IDENTITY);
    expect(offline).toMatchObject(online);
  });

  it('maps the offline identity to the correct camelCase request fields', () => {
    const payload = buildOfflinePublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS, IDENTITY);
    expect(payload.clientSubmissionId).toBe('sub-123');
    expect(payload.capturedAt).toBe('2026-07-15T09:30:00.000Z');
    expect(payload.capturedPlotCycleId).toBe('cycle-456');
  });

  it('an online-only payload built the OLD way (no offline fields) is still a valid PublicRecordCreatePayload', () => {
    // Backward compatibility: a payload with all three fields omitted must
    // still satisfy the type — proves the fields are genuinely optional.
    const online = buildPublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS);
    expect(online.clientSubmissionId).toBeUndefined();
    expect(online.capturedAt).toBeUndefined();
    expect(online.capturedPlotCycleId).toBeUndefined();
  });

  it('the multipart JSON body carries the full offline triple when built from an offline payload', () => {
    const payload = buildOfflinePublicRecordPayload('tok', '2026-07-01', EMPTY_FIELDS, IDENTITY);
    const formData = buildPublicRecordWithPhotosFormData(payload, []);
    const parsed = JSON.parse(formData.get('payload') as string) as Record<string, unknown>;

    expect(parsed.clientSubmissionId).toBe('sub-123');
    expect(parsed.capturedAt).toBe('2026-07-15T09:30:00.000Z');
    expect(parsed.capturedPlotCycleId).toBe('cycle-456');
  });
});

describe('PublicRecordCreateResult (round 8-4A/8-4B receipt fields)', () => {
  it('clientSubmissionId/capturedAt are nullable and round-trip null for an online-style result', () => {
    const result: PublicRecordCreateResult = {
      id: 'rec-1', plotId: 'plot-1', plotCode: 'P001', plotName: 'Plot One',
      supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      recordDate: '2026-07-01', submittedByName: null, createdAt: '2026-07-01T00:00:00Z',
      clientSubmissionId: null, capturedAt: null,
    };
    expect(result.clientSubmissionId).toBeNull();
    expect(result.capturedAt).toBeNull();
  });

  it('round-trips a real offline receipt with both fields populated', () => {
    const result: PublicRecordCreateResult = {
      id: 'rec-2', plotId: 'plot-1', plotCode: 'P001', plotName: 'Plot One',
      supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
      recordDate: '2026-07-01', submittedByName: 'สมชาย', createdAt: '2026-07-16T00:00:00Z',
      clientSubmissionId: 'sub-123', capturedAt: '2026-07-15T09:30:00.000Z',
    };
    expect(result.clientSubmissionId).toBe('sub-123');
    expect(result.capturedAt).toBe('2026-07-15T09:30:00.000Z');
  });
});
