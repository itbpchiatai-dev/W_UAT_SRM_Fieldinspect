import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiClient } from './client';
import {
  buildRecordWithPhotosFormData,
  extractPhotoFilename,
  getRecordPhotoBlob,
  listRecords,
  type RecordCreatePayload,
} from './records';

function jpegFile(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

const BASE_PAYLOAD: RecordCreatePayload = {
  plotId: 'plot-1',
  supplierId: 'supplier-1',
  recordDate: '2026-07-01',
};

describe('RecordCreatePayload contract (round 8-3G)', () => {
  it('rejects submittedByCode — retired, never a create-payload field', () => {
    // @ts-expect-error submittedByCode must not be assignable to RecordCreatePayload
    const payload: RecordCreatePayload = { ...BASE_PAYLOAD, submittedByCode: 'FIELD01' };
    expect(payload).toBeTruthy();
  });
});

describe('buildRecordWithPhotosFormData', () => {
  it('puts the JSON-encoded payload under the "payload" field', () => {
    const formData = buildRecordWithPhotosFormData(BASE_PAYLOAD, []);
    const raw = formData.get('payload');
    expect(typeof raw).toBe('string');
    expect(JSON.parse(raw as string)).toEqual(BASE_PAYLOAD);
  });

  it('appends exactly the given files under the "photos" field, in order', () => {
    const photos = [jpegFile('a.jpg'), jpegFile('b.jpg'), jpegFile('c.jpg'), jpegFile('d.jpg')];
    const formData = buildRecordWithPhotosFormData(BASE_PAYLOAD, photos);

    const got = formData.getAll('photos');
    expect(got).toHaveLength(4);
    expect((got[0] as File).name).toBe('a.jpg');
    expect((got[3] as File).name).toBe('d.jpg');
  });

  it('never puts plotId/supplierId inside the file parts (only in the payload field)', () => {
    const formData = buildRecordWithPhotosFormData(BASE_PAYLOAD, [jpegFile('a.jpg')]);
    const keys = Array.from(formData.keys());
    expect(keys.filter((k) => k === 'payload')).toHaveLength(1);
    expect(keys.filter((k) => k === 'photos')).toHaveLength(1);
  });
});

describe('extractPhotoFilename', () => {
  it('pulls the filename out of a well-formed stored photoUrl', () => {
    const filename = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.jpg';
    expect(extractPhotoFilename(`/media/inspection-photos/${filename}`)).toBe(filename);
  });

  it('accepts png and webp too', () => {
    expect(extractPhotoFilename('/media/inspection-photos/' + '0'.repeat(32) + '.png')).toBe('0'.repeat(32) + '.png');
    expect(extractPhotoFilename('/media/inspection-photos/' + '0'.repeat(32) + '.webp')).toBe('0'.repeat(32) + '.webp');
  });

  it('rejects path-traversal-shaped input', () => {
    expect(extractPhotoFilename('/media/inspection-photos/../../../etc/passwd')).toBeNull();
    expect(extractPhotoFilename('../../../etc/passwd')).toBeNull();
  });

  it('rejects non-uuid-shaped or wrong-extension filenames', () => {
    expect(extractPhotoFilename('/media/inspection-photos/not-a-uuid.jpg')).toBeNull();
    expect(extractPhotoFilename('/media/inspection-photos/' + '0'.repeat(32) + '.exe')).toBeNull();
    expect(extractPhotoFilename('')).toBeNull();
  });
});

describe('getRecordPhotoBlob', () => {
  const validFilename = '0'.repeat(32) + '.jpg';

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('calls GET /api/v1/records/{recordId}/photos/{filename} with responseType blob', async () => {
    const fakeBlob = new Blob(['x'], { type: 'image/jpeg' });
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: fakeBlob });

    const blob = await getRecordPhotoBlob('record-1', validFilename);

    expect(blob).toBe(fakeBlob);
    expect(getSpy).toHaveBeenCalledWith(
      `/api/v1/records/record-1/photos/${validFilename}`,
      { responseType: 'blob' },
    );
  });

  it('rejects an invalid filename without ever calling the API', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: new Blob() });

    await expect(getRecordPhotoBlob('record-1', '../../../etc/passwd')).rejects.toThrow();

    expect(getSpy).not.toHaveBeenCalled();
  });
});

describe('listRecords query params', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  // Regression: the filters must reach FastAPI as snake_case. Sending
  // camelCase (plotId/supplierId) makes FastAPI ignore them and return every
  // record in scope — which surfaced as a plot's "ประวัติการตรวจ" showing
  // records from other plots.
  it('sends plot/supplier/date filters as snake_case keys', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: [] });

    await listRecords({
      plotId: 'plot-1',
      supplierId: 'supplier-1',
      dateFrom: '2026-06-01',
      dateTo: '2026-06-30',
      activeOnly: true,
      limit: 30,
      offset: 60,
    });

    expect(getSpy).toHaveBeenCalledWith('/api/v1/records', {
      params: {
        plot_id: 'plot-1',
        supplier_id: 'supplier-1',
        date_from: '2026-06-01',
        date_to: '2026-06-30',
        active_only: true,
        limit: 30,
        offset: 60,
      },
    });
  });
});
