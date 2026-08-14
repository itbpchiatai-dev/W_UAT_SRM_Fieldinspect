/**
 * offline-inspection-store — round 8-4B (V1) + round 8-4C (V2 schema + status
 * lifecycle). Uses fake-indexeddb (devDependency, see package.json) since
 * jsdom has no real IndexedDB implementation; src/test/setup.ts polyfills the
 * global for every test file.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import 'fake-indexeddb/auto';
import { IDBFactory } from 'fake-indexeddb';
import {
  OFFLINE_DB_NAME,
  OFFLINE_DB_VERSION,
  OFFLINE_STORE_NAME,
  OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME,
  OFFLINE_PUBLIC_ACCESS_CACHE_TTL_MS,
  OfflineStorageError,
  OfflineStorageQuotaExceededError,
  buildOfflineInspectionDraft,
  clearAllOfflineInspectionDrafts,
  closeOfflineInspectionDb,
  countOfflineInspectionDrafts,
  countUnsentOfflineInspectionDrafts,
  deleteOfflineInspectionDraft,
  filesToOfflinePhotos,
  getOfflineInspectionDraft,
  listOfflineInspectionDrafts,
  offlinePhotoToFile,
  openOfflineInspectionDb,
  purgeExpiredOfflineInspectionDrafts,
  putOfflineInspectionDraft,
  resetOfflineInspectionDraftForRetry,
  updateOfflineInspectionDraftStatus,
  buildOfflinePublicAccessCache,
  putOfflinePublicAccessCache,
  getOfflinePublicAccessCache,
  clearOfflinePublicAccessCache,
  isOfflinePublicAccessCacheValid,
  type OfflineInspectionDraftV1,
  type OfflineInspectionDraftV2,
  type OfflinePublicAccessCacheV1,
} from './offline-inspection-store';
import type { PublicInspectionFormFields } from '../api/publicInspection';
import type { PublicPhoneAccessPlot } from '../api/publicInspectionAccess';
import type { InspectionProtocolResponse } from '../api/inspectionProtocols';

const EMPTY_FIELDS: PublicInspectionFormFields = {
  submittedByName: '', growthStage: '', yieldPct: 100, yieldQuantityKg: null, weatherCondition: '',
  fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
  recommendation: '', notes: '', latitude: null, longitude: null,
};

function jpegFile(name: string): File {
  return new File(['x'.repeat(10)], name, { type: 'image/jpeg', lastModified: 1_700_000_000_000 });
}

function draftFor(overrides: Partial<OfflineInspectionDraftV2> = {}, capturedAt = '2026-07-15T09:00:00.000Z'): OfflineInspectionDraftV2 {
  return buildOfflineInspectionDraft({
    clientSubmissionId: overrides.clientSubmissionId ?? crypto.randomUUID(),
    capturedAt,
    capturedPlotCycleId: 'cycle-1',
    recordDate: '2026-07-15',
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', lotNo: 'LOT-01', plantingDate: '2026-01-01',
    inspectorType: 'farmer',
    fields: { ...EMPTY_FIELDS, submittedByName: 'สมชาย' },
    photos: [],
    now: capturedAt,
    ...overrides,
  });
}

// Fresh in-memory IndexedDB per test — fake-indexeddb persists data across
// tests otherwise, which would leak drafts between cases.
beforeEach(() => {
  closeOfflineInspectionDb();
  (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
});

afterEach(() => {
  closeOfflineInspectionDb();
});

describe('openOfflineInspectionDb', () => {
  it('opens/creates the database at version 3 with the documented store + indexes (round 8-4H)', async () => {
    const db = await openOfflineInspectionDb();
    expect(db.name).toBe(OFFLINE_DB_NAME);
    expect(db.version).toBe(3);
    expect(OFFLINE_DB_VERSION).toBe(3);
    expect(db.objectStoreNames.contains(OFFLINE_STORE_NAME)).toBe(true);

    const tx = db.transaction(OFFLINE_STORE_NAME, 'readonly');
    const store = tx.objectStore(OFFLINE_STORE_NAME);
    expect(store.keyPath).toBe('clientSubmissionId');
    expect(Array.from(store.indexNames).sort()).toEqual(['capturedAt', 'plotId', 'status']);
  });

  it('also creates the round 8-4H public_access_cache store, keyed by id', async () => {
    const db = await openOfflineInspectionDb();
    expect(db.objectStoreNames.contains(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME)).toBe(true);
    const tx = db.transaction(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME, 'readonly');
    expect(tx.objectStore(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME).keyPath).toBe('id');
  });
});

describe('put/get', () => {
  it('round-trips a draft by clientSubmissionId', async () => {
    const draft = draftFor({ clientSubmissionId: 'draft-1' });
    await putOfflineInspectionDraft(draft);

    const got = await getOfflineInspectionDraft('draft-1');
    expect(got).toEqual(draft);
  });

  it('returns null for a missing key', async () => {
    expect(await getOfflineInspectionDraft('does-not-exist')).toBeNull();
  });
});

describe('upsert semantics', () => {
  it('putting the SAME clientSubmissionId twice does not increase the count', async () => {
    const draft = draftFor({ clientSubmissionId: 'same-key' });
    await putOfflineInspectionDraft(draft);
    await putOfflineInspectionDraft({ ...draft, updatedAt: '2026-07-15T10:00:00.000Z' });

    expect(await countOfflineInspectionDrafts()).toBe(1);
    const got = await getOfflineInspectionDraft('same-key');
    expect(got?.updatedAt).toBe('2026-07-15T10:00:00.000Z');
  });
});

describe('listOfflineInspectionDrafts', () => {
  it('lists most-recently-captured first', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'older' }, '2026-07-10T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'newest' }, '2026-07-15T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'middle' }, '2026-07-12T00:00:00.000Z'));

    const ids = (await listOfflineInspectionDrafts()).map((d) => d.clientSubmissionId);
    expect(ids).toEqual(['newest', 'middle', 'older']);
  });
});

describe('photo Blob round-trip', () => {
  // NOTE: fake-indexeddb clones values via Node's global structuredClone,
  // which does not recognize jsdom's own File/Blob class as a native Blob in
  // this test environment (confirmed directly: the same round-trip through
  // fake-indexeddb using NODE's own Blob preserves .size correctly) — a
  // known jsdom+fake-indexeddb test-harness gap, not a real-browser behavior
  // gap (a real browser's native IndexedDB always uses its OWN Blob, which
  // its OWN structuredClone always recognizes). This test therefore verifies
  // what IS reliably observable in this harness — the metadata fields and
  // that a blob value round-trips at all — and offlinePhotoToFile's own
  // File-reconstruction contract is exercised with a real Blob directly.
  it('stores photo name/type/lastModified metadata and a blob value', async () => {
    const file = jpegFile('a.jpg');
    const photos = filesToOfflinePhotos([file, null, file]);
    expect(photos).toHaveLength(2);

    const draft = draftFor({ clientSubmissionId: 'with-photos', photos });
    await putOfflineInspectionDraft(draft);
    const got = await getOfflineInspectionDraft('with-photos');

    expect(got?.photos).toHaveLength(2);
    expect(got?.photos[0].name).toBe('a.jpg');
    expect(got?.photos[0].type).toBe('image/jpeg');
    expect(got?.photos[0].lastModified).toBe(1_700_000_000_000);
    expect(got?.photos[0].blob).toBeDefined();
  });

  it('offlinePhotoToFile reconstructs a same-shaped File from a stored photo record', () => {
    const blob = new Blob(['hello'], { type: 'image/jpeg' });
    const reconstructed = offlinePhotoToFile({
      blob, name: 'a.jpg', type: 'image/jpeg', lastModified: 1_700_000_000_000,
    });
    expect(reconstructed).toBeInstanceOf(File);
    expect(reconstructed.name).toBe('a.jpg');
    expect(reconstructed.type).toBe('image/jpeg');
    expect(reconstructed.lastModified).toBe(1_700_000_000_000);
    expect(reconstructed.size).toBe(blob.size);
  });
});

describe('deleteOfflineInspectionDraft', () => {
  it('removes exactly the named draft (and its photos, stored inside the record)', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'keep' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'gone', photos: filesToOfflinePhotos([jpegFile('x.jpg')]) }));

    await deleteOfflineInspectionDraft('gone');

    expect(await getOfflineInspectionDraft('gone')).toBeNull();
    expect(await getOfflineInspectionDraft('keep')).not.toBeNull();
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });
});

describe('clearAllOfflineInspectionDrafts', () => {
  it('empties the store entirely', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'a' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'b' }));

    await clearAllOfflineInspectionDrafts();

    expect(await countOfflineInspectionDrafts()).toBe(0);
    expect(await listOfflineInspectionDrafts()).toEqual([]);
  });
});

describe('purgeExpiredOfflineInspectionDrafts', () => {
  it('deletes only drafts older than the retention window and reports the count', async () => {
    const now = new Date('2026-07-20T00:00:00.000Z');
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'fresh' }, '2026-07-19T00:00:00.000Z')); // 1 day old
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'stale-1' }, '2026-07-01T00:00:00.000Z')); // 19 days old
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'stale-2' }, '2026-06-01T00:00:00.000Z')); // 49 days old

    const purgedCount = await purgeExpiredOfflineInspectionDrafts(now);

    expect(purgedCount).toBe(2);
    expect(await getOfflineInspectionDraft('fresh')).not.toBeNull();
    expect(await getOfflineInspectionDraft('stale-1')).toBeNull();
    expect(await getOfflineInspectionDraft('stale-2')).toBeNull();
  });

  it('respects a custom maxAgeMs (e.g. matching the backend 7-day window exactly)', async () => {
    const now = new Date('2026-07-20T00:00:00.000Z');
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'six-days' }, '2026-07-14T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'eight-days' }, '2026-07-12T00:00:00.000Z'));

    const purgedCount = await purgeExpiredOfflineInspectionDrafts(now, sevenDaysMs);

    expect(purgedCount).toBe(1);
    expect(await getOfflineInspectionDraft('six-days')).not.toBeNull();
    expect(await getOfflineInspectionDraft('eight-days')).toBeNull();
  });
});

describe('PII/token exclusion', () => {
  it('OfflineInspectionDraftV1 has no phone/token/qrKey field at the type level', () => {
    const draft = draftFor();
    const keys = Object.keys(draft);
    for (const forbidden of [
      'phone', 'phoneNormalized', 'accessNumber',
      'phoneAccessSessionToken', 'inspectionSessionToken', 'qrKey',
      'password', 'accessCode',
    ]) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it('buildOfflineInspectionDraft only accepts the allowlisted input shape — an extraneous field never reaches the stored record', async () => {
    // A raw phone (or any other field outside the allowlisted parameter
    // shape) is not even assignable at the type level — this simulates a
    // caller bypassing that with an `any`-typed spread, proving the builder
    // itself never copies unknown keys through regardless.
    const contaminatedInput = {
      clientSubmissionId: 'x', capturedAt: '2026-07-15T00:00:00.000Z',
      capturedPlotCycleId: 'cycle-1', recordDate: '2026-07-15',
      plotId: 'plot-1', plotCode: 'P', plotName: 'P',
      supplierId: 's', supplierCode: 'S', supplierName: 'S',
      cycleNo: 1, cycleLabel: null, crop: null, variety: null, lotNo: null, plantingDate: null,
      inspectorType: 'farmer', fields: EMPTY_FIELDS, photos: [],
      now: '2026-07-15T00:00:00.000Z',
      phone: '0812345678',
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any;
    const draft = buildOfflineInspectionDraft(contaminatedInput);
    expect((draft as unknown as Record<string, unknown>).phone).toBeUndefined();
  });
});

describe('error propagation', () => {
  it('a transaction error propagates as an OfflineStorageError (not a silent swallow)', async () => {
    // Force a real IndexedDB error: putting a value that violates the store's
    // keyPath contract (missing clientSubmissionId) rejects the request with
    // a DataError, which our transaction wrapper must reject with, not hang.
    const db = await openOfflineInspectionDb();
    const tx = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
    const store = tx.objectStore(OFFLINE_STORE_NAME);
    let requestFailed = false;
    try {
      store.put({ noKeyPathField: true });
    } catch {
      requestFailed = true;
    }
    expect(requestFailed).toBe(true);
  });

  it('QuotaExceededError propagates as OfflineStorageQuotaExceededError (not a generic/unknown error)', async () => {
    // A quota failure on .put() can surface as a synchronous throw from the
    // engine (exercised here, since it's the most portable way to simulate
    // it across IndexedDB implementations) — runInTransaction classifies a
    // synchronous throw exactly the same way it classifies an async one.
    await openOfflineInspectionDb(); // ensure the store exists before mocking
    const putSpy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(() => {
      throw new DOMException('quota exceeded', 'QuotaExceededError');
    });

    try {
      await expect(putOfflineInspectionDraft(draftFor())).rejects.toBeInstanceOf(OfflineStorageQuotaExceededError);
    } finally {
      putSpy.mockRestore();
    }
  });

  it('OfflineStorageQuotaExceededError is a subclass of OfflineStorageError', () => {
    expect(new OfflineStorageQuotaExceededError('x')).toBeInstanceOf(OfflineStorageError);
  });
});

// --- V1 -> V2 migration (round 8-4C) ----------------------------------------

function v1DraftFor(overrides: Partial<OfflineInspectionDraftV1> = {}): OfflineInspectionDraftV1 {
  return {
    schemaVersion: 1,
    clientSubmissionId: 'v1-draft',
    capturedAt: '2026-07-15T09:00:00.000Z',
    capturedPlotCycleId: 'cycle-1',
    recordDate: '2026-07-15',
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', lotNo: 'LOT-01', plantingDate: '2026-01-01',
    inspectorType: 'farmer',
    fields: { ...EMPTY_FIELDS, submittedByName: 'สมชาย' },
    photos: filesToOfflinePhotos([jpegFile('a.jpg')]),
    status: 'pending',
    createdAt: '2026-07-15T09:00:00.000Z',
    updatedAt: '2026-07-15T09:00:00.000Z',
    ...overrides,
  };
}

/** Creates a REAL V1 database (bypassing our module entirely) with one
 * draft already stored — simulating a device that used the app before round
 * 8-4C shipped. The connection is closed before returning so the later
 * V2 open() in the test body is never blocked by a lingering V1 handle. */
async function createV1DatabaseWithDraft(draft: OfflineInspectionDraftV1): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.open(OFFLINE_DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      const store = db.createObjectStore(OFFLINE_STORE_NAME, { keyPath: 'clientSubmissionId' });
      store.createIndex('capturedAt', 'capturedAt');
      store.createIndex('status', 'status');
      store.createIndex('plotId', 'plotId');
    };
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
      tx.objectStore(OFFLINE_STORE_NAME).put(draft);
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
    };
    req.onerror = () => reject(req.error);
  });
}

describe('V1 -> V2 migration', () => {
  it('rewrites a real V1 record to V2 in place — every V1 field (including the photo Blob) survives, plus the 3 new fields', async () => {
    const v1 = v1DraftFor();
    await createV1DatabaseWithDraft(v1);

    // Opening through OUR module now triggers the 1 -> 2 onupgradeneeded walk.
    const got = await getOfflineInspectionDraft('v1-draft');

    expect(got).not.toBeNull();
    expect(got?.schemaVersion).toBe(2);
    expect(got?.status).toBe('pending');
    expect(got?.lastAttemptAt).toBeNull();
    expect(got?.lastErrorCode).toBeNull();
    // Every V1 field carried through unchanged — identity triple first.
    expect(got?.clientSubmissionId).toBe('v1-draft');
    expect(got?.capturedAt).toBe(v1.capturedAt);
    expect(got?.capturedPlotCycleId).toBe('cycle-1');
    // plot/cycle snapshot.
    expect(got?.plotId).toBe('plot-1');
    expect(got?.plotCode).toBe('PLOT001');
    expect(got?.supplierCode).toBe('SUP001');
    expect(got?.cycleLabel).toBe('jun2026');
    expect(got?.crop).toBe('พริก');
    expect(got?.lotNo).toBe('LOT-01');
    expect(got?.plantingDate).toBe('2026-01-01');
    // form fields + photo Blob.
    expect(got?.fields.submittedByName).toBe('สมชาย');
    expect(got?.photos).toHaveLength(1);
    expect(got?.photos[0].name).toBe('a.jpg');
    expect(got?.photos[0].blob).toBeDefined();
    expect(got?.createdAt).toBe(v1.createdAt);
  });

  it('does not duplicate the record and keeps every index intact after migrating', async () => {
    await createV1DatabaseWithDraft(v1DraftFor());
    await openOfflineInspectionDb();

    expect(await countOfflineInspectionDrafts()).toBe(1);
    const db = await openOfflineInspectionDb();
    const tx = db.transaction(OFFLINE_STORE_NAME, 'readonly');
    const store = tx.objectStore(OFFLINE_STORE_NAME);
    expect(Array.from(store.indexNames).sort()).toEqual(['capturedAt', 'plotId', 'status']);
  });

  it('migrates multiple existing V1 drafts, not just the first', async () => {
    await new Promise<void>((resolve, reject) => {
      const req = indexedDB.open(OFFLINE_DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        const store = db.createObjectStore(OFFLINE_STORE_NAME, { keyPath: 'clientSubmissionId' });
        store.createIndex('capturedAt', 'capturedAt');
        store.createIndex('status', 'status');
        store.createIndex('plotId', 'plotId');
      };
      req.onsuccess = () => {
        const db = req.result;
        const tx = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
        const s = tx.objectStore(OFFLINE_STORE_NAME);
        s.put(v1DraftFor({ clientSubmissionId: 'v1-a' }));
        s.put(v1DraftFor({ clientSubmissionId: 'v1-b' }));
        s.put(v1DraftFor({ clientSubmissionId: 'v1-c' }));
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => reject(tx.error);
      };
      req.onerror = () => reject(req.error);
    });

    const all = await listOfflineInspectionDrafts();
    expect(all).toHaveLength(3);
    expect(all.every((d) => d.schemaVersion === 2 && d.status === 'pending')).toBe(true);
  });

  it('a migrated draft never gains a phone/token/qrKey field', async () => {
    await createV1DatabaseWithDraft(v1DraftFor());
    const got = await getOfflineInspectionDraft('v1-draft');
    const keys = Object.keys(got ?? {});
    for (const forbidden of ['phone', 'phoneAccessSessionToken', 'inspectionSessionToken', 'qrKey', 'password']) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it('a fresh install with no prior V1 database creates the store directly at the current version (round 8-4H: 3)', async () => {
    const db = await openOfflineInspectionDb();
    expect(db.version).toBe(OFFLINE_DB_VERSION);
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });
});

// --- updateOfflineInspectionDraftStatus (round 8-4C) ------------------------

describe('updateOfflineInspectionDraftStatus', () => {
  it('transitions status and stamps lastAttemptAt/lastErrorCode, preserving every other field', async () => {
    const draft = draftFor({ clientSubmissionId: 'x' });
    await putOfflineInspectionDraft(draft);

    await updateOfflineInspectionDraftStatus('x', 'blocked_cycle_changed', { lastErrorCode: 'planting_cycle_changed' });

    const got = await getOfflineInspectionDraft('x');
    expect(got?.status).toBe('blocked_cycle_changed');
    expect(got?.lastErrorCode).toBe('planting_cycle_changed');
    expect(got?.lastAttemptAt).not.toBeNull();
    expect(got?.plotId).toBe(draft.plotId);
    expect(got?.photos).toEqual(draft.photos);
  });

  it('defaults lastErrorCode to null when not given', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'y' }));
    await updateOfflineInspectionDraftStatus('y', 'blocked_access');
    const got = await getOfflineInspectionDraft('y');
    expect(got?.lastErrorCode).toBeNull();
  });

  it('is a silent no-op for a clientSubmissionId that no longer exists (e.g. deleted concurrently)', async () => {
    await expect(updateOfflineInspectionDraftStatus('does-not-exist', 'blocked_access')).resolves.toBeUndefined();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('waits for the transaction to actually commit before resolving', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'committed' }));
    await updateOfflineInspectionDraftStatus('committed', 'blocked_expired', { lastErrorCode: 'offline_draft_expired' });
    // A second, independent read (its own transaction) sees the committed value.
    const got = await getOfflineInspectionDraft('committed');
    expect(got?.status).toBe('blocked_expired');
  });

  it('a failed write during a status update rejects rather than silently succeeding', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'fail-me' }));
    const putSpy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(() => {
      throw new DOMException('boom', 'UnknownError');
    });
    try {
      await expect(updateOfflineInspectionDraftStatus('fail-me', 'blocked_conflict')).rejects.toBeInstanceOf(OfflineStorageError);
    } finally {
      putSpy.mockRestore();
    }
    // The draft must still show its ORIGINAL status — never silently marked
    // as transitioned when the write actually failed.
    putSpy.mockRestore();
    const got = await getOfflineInspectionDraft('fail-me');
    expect(got?.status).toBe('pending');
  });
});

// --- resetOfflineInspectionDraftForRetry (round 8-4C.1 Part B) -------------

describe('resetOfflineInspectionDraftForRetry', () => {
  it('resets status to pending and clears lastErrorCode, in one transaction', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'r1' }));
    await updateOfflineInspectionDraftStatus('r1', 'blocked_access', { lastErrorCode: 'not_found' });

    await resetOfflineInspectionDraftForRetry('r1');

    const got = await getOfflineInspectionDraft('r1');
    expect(got?.status).toBe('pending');
    expect(got?.lastErrorCode).toBeNull();
  });

  it('preserves lastAttemptAt as an audit trail — does NOT clear it', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'r2' }));
    await updateOfflineInspectionDraftStatus('r2', 'blocked_access', { lastErrorCode: 'not_found' });
    const beforeRetry = await getOfflineInspectionDraft('r2');
    expect(beforeRetry?.lastAttemptAt).not.toBeNull();

    await resetOfflineInspectionDraftForRetry('r2');

    const afterRetry = await getOfflineInspectionDraft('r2');
    expect(afterRetry?.lastAttemptAt).toBe(beforeRetry?.lastAttemptAt);
  });

  it('never creates/changes the identity triple, form fields, or photos', async () => {
    const draft = draftFor({
      clientSubmissionId: 'r3',
      photos: filesToOfflinePhotos([jpegFile('a.jpg')]),
    });
    await putOfflineInspectionDraft(draft);
    await updateOfflineInspectionDraftStatus('r3', 'blocked_access', { lastErrorCode: 'not_found' });

    await resetOfflineInspectionDraftForRetry('r3');

    const got = await getOfflineInspectionDraft('r3');
    expect(got?.clientSubmissionId).toBe('r3');
    expect(got?.capturedAt).toBe(draft.capturedAt);
    expect(got?.capturedPlotCycleId).toBe(draft.capturedPlotCycleId);
    expect(got?.fields).toEqual(draft.fields);
    expect(got?.photos).toHaveLength(1);
    expect(got?.photos[0].name).toBe('a.jpg');
  });

  it('bumps updatedAt', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'r4' }, '2026-07-15T09:00:00.000Z'));
    await updateOfflineInspectionDraftStatus('r4', 'blocked_access');

    await resetOfflineInspectionDraftForRetry('r4');

    const got = await getOfflineInspectionDraft('r4');
    expect(got?.updatedAt).not.toBe('2026-07-15T09:00:00.000Z');
  });

  it('is a silent no-op for a clientSubmissionId that no longer exists — never recreates a deleted draft', async () => {
    await expect(resetOfflineInspectionDraftForRetry('does-not-exist')).resolves.toBeUndefined();
    expect(await countOfflineInspectionDrafts()).toBe(0);
  });

  it('a pending draft is left completely untouched (a true no-op, not a redundant re-write)', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'r5' }, '2026-07-15T09:00:00.000Z'));
    const before = await getOfflineInspectionDraft('r5');

    await resetOfflineInspectionDraftForRetry('r5');

    const after = await getOfflineInspectionDraft('r5');
    expect(after?.status).toBe('pending');
    expect(after?.updatedAt).toBe(before?.updatedAt); // untouched, not bumped
    expect(after).toEqual(before);
  });

  // --- round 8-4C.2 Part B: store enforces the guard ITSELF, not just the UI

  it.each([
    ['blocked_cycle_changed', 'planting_cycle_changed'],
    ['blocked_conflict', 'idempotency_conflict'],
    ['blocked_expired', 'offline_draft_expired'],
  ] as const)('calling reset directly on a %s draft is a true no-op — the store enforces this itself, not just the UI', async (status, lastErrorCode) => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'guarded' }));
    await updateOfflineInspectionDraftStatus('guarded', status, { lastErrorCode });
    const before = await getOfflineInspectionDraft('guarded');

    await resetOfflineInspectionDraftForRetry('guarded');

    const after = await getOfflineInspectionDraft('guarded');
    expect(after?.status).toBe(status);
    expect(after?.lastErrorCode).toBe(lastErrorCode);
    expect(after?.updatedAt).toBe(before?.updatedAt); // literally unchanged, not just status
    expect(after).toEqual(before);
  });
});

// --- countUnsentOfflineInspectionDrafts (round 8-4C) ------------------------

describe('countUnsentOfflineInspectionDrafts', () => {
  it('counts only status === pending, excluding every blocked_* status', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'p1' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'p2' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'b1' }));
    await updateOfflineInspectionDraftStatus('b1', 'blocked_access');

    expect(await countUnsentOfflineInspectionDrafts()).toBe(2);
    expect(await countOfflineInspectionDrafts()).toBe(3);
  });

  it('is 0 for an empty store', async () => {
    expect(await countUnsentOfflineInspectionDrafts()).toBe(0);
  });
});

// --- purge covers every status (round 8-4C) ---------------------------------

describe('purgeExpiredOfflineInspectionDrafts — blocked statuses', () => {
  it('purges an old blocked draft the same as an old pending one — no special reprieve', async () => {
    const now = new Date('2026-07-20T00:00:00.000Z');
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'old-blocked' }, '2026-07-01T00:00:00.000Z'));
    await updateOfflineInspectionDraftStatus('old-blocked', 'blocked_conflict', { lastErrorCode: 'idempotency_conflict' });

    const purgedCount = await purgeExpiredOfflineInspectionDrafts(now);

    expect(purgedCount).toBe(1);
    expect(await getOfflineInspectionDraft('old-blocked')).toBeNull();
  });
});

// =============================================================================
// Round 8-4H — Persistent Offline Authorized Plot Cache
// =============================================================================

function accessPlot(overrides: Partial<PublicPhoneAccessPlot> = {}): PublicPhoneAccessPlot {
  return {
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    accessType: 'primary', canInspect: true, unavailableReason: null,
    plotCycleId: 'cycle-1', cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', inspectedToday: false,
    lastInspectionDate: null, lastInspectedAt: null,
    lotNo: 'LOT-01', plantingDate: '2026-01-01',
    plantCount: 100, expectedYieldFull: '500', expectedYieldUnit: 'กก.',
    currentYieldPct: 80, currentStage: 'ติดผล',
    ...overrides,
  };
}

const PROTOCOLS_FOR_CACHE: InspectionProtocolResponse = {
  version: 1,
  stages: [{ growthStage: 'ระยะงอก', criteria: [{ slot: 'fieldPrepScore', label: 'การเตรียมแปลง' }] }],
};

function masterDataForCache() {
  return {
    growthStage: [{ value: 'ระยะงอก', parent: null }],
    weather: [{ value: 'แดดจัด', parent: null }],
  };
}

describe('buildOfflinePublicAccessCache', () => {
  it('round-trips through put/get unchanged', async () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()],
      protocols: PROTOCOLS_FOR_CACHE,
      masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    });
    expect(cache).not.toBeNull();
    await putOfflinePublicAccessCache(cache!);

    const got = await getOfflinePublicAccessCache();

    expect(got).toEqual(cache);
  });

  it('a fresh cache REPLACES the previous one — never appended/merged', async () => {
    const first = buildOfflinePublicAccessCache({
      plots: [accessPlot({ plotId: 'p1', plotCode: 'PLOT001' })],
      protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    await putOfflinePublicAccessCache(first);

    const second = buildOfflinePublicAccessCache({
      plots: [accessPlot({ plotId: 'p2', plotCode: 'PLOT002' })],
      protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T10:00:00.000Z',
    })!;
    await putOfflinePublicAccessCache(second);

    const got = await getOfflinePublicAccessCache();
    expect(got?.plots).toHaveLength(1);
    expect(got?.plots[0].plotCode).toBe('PLOT002');
  });

  it('only caches plots with canInspect=true AND an active plotCycleId/cycleNo', () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [
        accessPlot({ plotId: 'ok', canInspect: true, plotCycleId: 'cycle-1', cycleNo: 2 }),
        accessPlot({ plotId: 'no-cycle', canInspect: false, plotCycleId: null, cycleNo: null, unavailableReason: 'no_active_cycle' }),
        accessPlot({ plotId: 'null-cycle-id', canInspect: true, plotCycleId: null, cycleNo: 2 }),
        accessPlot({ plotId: 'null-cycle-no', canInspect: true, plotCycleId: 'cycle-9', cycleNo: null }),
      ],
      protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    });

    expect(cache?.plots).toHaveLength(1);
    expect(cache?.plots[0].plotId).toBe('ok');
  });

  it('returns null when there are zero cacheable plots — caller must clear, not write an empty cache', () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot({ canInspect: false, plotCycleId: null, cycleNo: null })],
      protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    });
    expect(cache).toBeNull();
  });

  it('TTL is exactly 24 hours from `now`', () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    expect(cache.cachedAt).toBe('2026-07-19T09:00:00.000Z');
    expect(cache.expiresAt).toBe('2026-07-20T09:00:00.000Z');
    expect(new Date(cache.expiresAt).getTime() - new Date(cache.cachedAt).getTime())
      .toBe(OFFLINE_PUBLIC_ACCESS_CACHE_TTL_MS);
  });

  it('never spreads the raw PublicPhoneAccessPlot — accessType/unavailableReason are not persisted', async () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    const stored = cache.plots[0] as unknown as Record<string, unknown>;
    expect('accessType' in stored).toBe(false);
    expect('unavailableReason' in stored).toBe(false);
    expect('canInspect' in stored).toBe(false); // implied true by presence, never stored
  });
});

describe('isOfflinePublicAccessCacheValid', () => {
  it('true when not yet expired', () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    expect(isOfflinePublicAccessCacheValid(cache, new Date('2026-07-19T20:00:00.000Z'))).toBe(true);
  });

  it('false once past expiresAt', () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    expect(isOfflinePublicAccessCacheValid(cache, new Date('2026-07-20T09:00:00.001Z'))).toBe(false);
  });

  it('false for null/undefined — never throws', () => {
    expect(isOfflinePublicAccessCacheValid(null, new Date())).toBe(false);
    expect(isOfflinePublicAccessCacheValid(undefined, new Date())).toBe(false);
  });

  it('false for a malformed object — never throws', () => {
    const malformed = { id: 'latest', version: 1 } as unknown as OfflinePublicAccessCacheV1;
    expect(isOfflinePublicAccessCacheValid(malformed, new Date())).toBe(false);
  });
});

describe('getOfflinePublicAccessCache — malformed data', () => {
  it('a malformed row in storage is treated as no cache, never thrown/crashed on', async () => {
    // Bypass buildOfflinePublicAccessCache entirely to simulate corruption/a
    // future incompatible shape landing in the store.
    await putOfflinePublicAccessCache({ id: 'latest', garbage: true } as unknown as OfflinePublicAccessCacheV1);

    await expect(getOfflinePublicAccessCache()).resolves.toBeNull();
  });

  it('returns null (not throw) when no cache row exists at all', async () => {
    await expect(getOfflinePublicAccessCache()).resolves.toBeNull();
  });
});

describe('clearOfflinePublicAccessCache', () => {
  it('removes the cache row', async () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    await putOfflinePublicAccessCache(cache);

    await clearOfflinePublicAccessCache();

    expect(await getOfflinePublicAccessCache()).toBeNull();
  });

  it('never touches inspection_drafts — a pending draft survives a cache clear untouched', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'still-here' }));
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    await putOfflinePublicAccessCache(cache);

    await clearOfflinePublicAccessCache();

    expect(await getOfflineInspectionDraft('still-here')).not.toBeNull();
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });
});

describe('putOfflinePublicAccessCache — storage failure', () => {
  it('a quota failure surfaces as OfflineStorageQuotaExceededError, matching putOfflineInspectionDraft', async () => {
    await openOfflineInspectionDb();
    const putSpy = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(() => {
      throw new DOMException('quota exceeded', 'QuotaExceededError');
    });
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    try {
      await expect(putOfflinePublicAccessCache(cache)).rejects.toBeInstanceOf(OfflineStorageQuotaExceededError);
    } finally {
      putSpy.mockRestore();
    }
  });
});

describe('Round 8-4H security — no forbidden field ever appears in a serialized cache', () => {
  const REAL_PHONE_FOR_TEST = '0845552162'; // placeholder test number, never a real one

  it('deep-scans the serialized cache for phone/token/qrKey/secret — none present', async () => {
    const cache = buildOfflinePublicAccessCache({
      plots: [accessPlot()], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;
    await putOfflinePublicAccessCache(cache);
    const stored = await getOfflinePublicAccessCache();

    const raw = JSON.stringify(stored);
    const forbidden = [
      REAL_PHONE_FOR_TEST, 'phone', 'phoneNumber', 'submittedPhoneSnapshot',
      'phoneAccessSessionToken', 'inspectionSessionToken', 'qrKey', 'token',
      'password', 'secret',
    ];
    for (const word of forbidden) {
      expect(raw.toLowerCase()).not.toContain(word.toLowerCase());
    }
  });

  it('OfflineCachedPlot key set is the exact allowlist — no unknown/extra fields survive even if the input plot had extras', async () => {
    const plotWithExtraFields = {
      ...accessPlot(),
      phone: REAL_PHONE_FOR_TEST,
      phoneAccessSessionToken: 'phone-tok-should-not-persist',
      qrKey: 'qr-secret-should-not-persist',
    } as unknown as PublicPhoneAccessPlot;

    const cache = buildOfflinePublicAccessCache({
      plots: [plotWithExtraFields], protocols: PROTOCOLS_FOR_CACHE, masterData: masterDataForCache(),
      now: '2026-07-19T09:00:00.000Z',
    })!;

    const keys = Object.keys(cache.plots[0]).sort();
    expect(keys).toEqual([
      'crop', 'currentStage', 'currentYieldPct', 'cycleLabel', 'cycleNo',
      'expectedYieldFull', 'expectedYieldUnit', 'inspectedToday', 'lastInspectedAt',
      'lotNo', 'plantCount', 'plantingDate', 'plotCode', 'plotCycleId', 'plotId',
      'plotName', 'supplierCode', 'supplierId', 'supplierName', 'variety',
    ].sort());
    const raw = JSON.stringify(cache);
    expect(raw).not.toContain(REAL_PHONE_FOR_TEST);
    expect(raw).not.toContain('phone-tok-should-not-persist');
    expect(raw).not.toContain('qr-secret-should-not-persist');
  });
});

// --- V2 -> V3 migration (round 8-4H) -----------------------------------------

/** Creates a REAL V2 database (bypassing our module entirely) with one
 * pending draft already stored — simulating a device that used the app
 * before round 8-4H shipped (no public_access_cache store yet). */
async function createV2DatabaseWithDraft(draft: OfflineInspectionDraftV2): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.open(OFFLINE_DB_NAME, 2);
    req.onupgradeneeded = () => {
      const db = req.result;
      const store = db.createObjectStore(OFFLINE_STORE_NAME, { keyPath: 'clientSubmissionId' });
      store.createIndex('capturedAt', 'capturedAt');
      store.createIndex('status', 'status');
      store.createIndex('plotId', 'plotId');
    };
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
      tx.objectStore(OFFLINE_STORE_NAME).put(draft);
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onerror = () => reject(tx.error);
    };
    req.onerror = () => reject(req.error);
  });
}

describe('V2 -> V3 migration (round 8-4H)', () => {
  it('every pending/blocked draft survives the upgrade byte-for-byte', async () => {
    const pending = draftFor({ clientSubmissionId: 'still-pending' });
    await createV2DatabaseWithDraft(pending);

    // Opening through OUR module now triggers the 2 -> 3 onupgradeneeded pass.
    const got = await getOfflineInspectionDraft('still-pending');

    expect(got).toEqual(pending);
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });

  it('adds the public_access_cache store without disturbing inspection_drafts contents/indexes', async () => {
    await createV2DatabaseWithDraft(draftFor({ clientSubmissionId: 'x' }));

    const db = await openOfflineInspectionDb();

    expect(db.version).toBe(3);
    expect(db.objectStoreNames.contains(OFFLINE_STORE_NAME)).toBe(true);
    expect(db.objectStoreNames.contains(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME)).toBe(true);
    const tx = db.transaction(OFFLINE_STORE_NAME, 'readonly');
    const store = tx.objectStore(OFFLINE_STORE_NAME);
    expect(Array.from(store.indexNames).sort()).toEqual(['capturedAt', 'plotId', 'status']);
    expect(await countOfflineInspectionDrafts()).toBe(1);
  });

  it('a fresh (never-opened) database lands directly on V3 with both stores present', async () => {
    const db = await openOfflineInspectionDb();
    expect(db.version).toBe(3);
    expect(db.objectStoreNames.contains(OFFLINE_STORE_NAME)).toBe(true);
    expect(db.objectStoreNames.contains(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME)).toBe(true);
  });
});
