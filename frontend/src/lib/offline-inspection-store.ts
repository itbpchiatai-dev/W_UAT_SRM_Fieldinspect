/**
 * offline-inspection-store — IndexedDB persistence for public-inspection
 * drafts captured while offline (round 8-4B foundation; round 8-4C adds
 * schema V2 — a persisted lifecycle status per draft plus re-auth + sequential
 * sync, see lib/offline-inspection-sync.ts and PublicInspect.tsx for how a
 * draft gets here and what happens to it next; round 8-4H adds schema V3 — a
 * SECOND, independent object store caching the last successful phone lookup's
 * authorized-plot list + reference data, so /public/inspect can open and be
 * used entirely offline after a cold reload, see OfflinePublicAccessCacheV1
 * below).
 *
 * Storage contract:
 *   Database: srm-fieldinspect-offline (version 3)
 *   Object store: inspection_drafts, keyPath = clientSubmissionId
 *     Indexes: capturedAt, status, plotId
 *   Object store: public_access_cache, keyPath = id (round 8-4H)
 *     Single row, key is always the literal string 'latest' — a fresh
 *     successful lookup always REPLACES this row (put, never a second row).
 *
 * V1 -> V2 migration (round 8-4C): every existing V1 record is walked via a
 * cursor inside the SAME onupgradeneeded versionchange transaction and
 * rewritten in place with schemaVersion=2, status='pending' (V1 only ever had
 * one implicit status), lastAttemptAt=null, lastErrorCode=null — every other
 * field (identity triple, plot/cycle snapshot, fields, photos) is carried
 * through byte-for-byte. Deliberately NEVER persists a 'syncing' status: if
 * the browser crashes mid-sync a persisted 'syncing' row would be stuck
 * forever, so which draft is "currently sending" lives only in the sync
 * caller's own in-memory ref/state (lib/offline-inspection-sync.ts) — a
 * reload always finds every draft in a real, resumable status.
 *
 * V2 -> V3 migration (round 8-4H): adds the NEW public_access_cache store
 * only — inspection_drafts is untouched (no cursor walk, no field rewrite),
 * so every pending/blocked draft survives the upgrade byte-for-byte. A
 * brand-new (never-opened-before) database also lands directly on V3 with
 * both stores created in the same onupgradeneeded pass.
 *
 * PII/secret discipline (non-negotiable): a draft NEVER carries the raw
 * access phone number, phoneAccessSessionToken, inspectionSessionToken,
 * qrKey, or any password/access code — see OfflineInspectionDraftV2 below and
 * buildOfflineInspectionDraft, the ONLY function that may construct one. It
 * takes explicit named fields, never a spread of a raw component-state
 * object, so a field added to PublicInspect's state later can't silently
 * leak into the store. Nothing in this module logs a draft's contents. The
 * SAME discipline applies to OfflinePublicAccessCacheV1 (round 8-4H) — see
 * its own docstring and buildOfflinePublicAccessCache, the ONLY function that
 * may construct one.
 *
 * Photos are stored as Blobs directly inside the draft record (IndexedDB's
 * structured-clone algorithm supports Blob/File natively) — there is no
 * separate photo object store, so deleting a draft always deletes its photos
 * too; there's no second cleanup step to forget.
 */
import type { PublicInspectionFormFields } from '../api/publicInspection';
import type {
  PublicInspectorType,
  PublicPhoneAccessPlot,
} from '../api/publicInspectionAccess';
import type { InspectionProtocolResponse } from '../api/inspectionProtocols';
import type { PublicMasterDataItem } from '../api/publicMasterdata';

export const OFFLINE_DB_NAME = 'srm-fieldinspect-offline';
export const OFFLINE_DB_VERSION = 3;
export const OFFLINE_STORE_NAME = 'inspection_drafts';
/** Round 8-4H — second object store in the SAME database (see module
 * docstring). Independent of inspection_drafts: clearing one never touches
 * the other. */
export const OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME = 'public_access_cache';
/** The single fixed key every cache row is written/read under — a fresh
 * successful lookup always REPLACES this row (round 8-4H requirement: "cache
 * ล่าสุดต้อง replace cache ก่อนหน้า ไม่ append รวมกัน"). */
const PUBLIC_ACCESS_CACHE_KEY = 'latest' as const;
/** 24 hours — round 8-4H requirement: "TTL = 24 ชั่วโมงนับจาก successful
 * online lookup". Independent of OFFLINE_DRAFT_MAX_AGE_MS below (that one
 * governs queued inspection drafts, a completely different lifecycle). */
export const OFFLINE_PUBLIC_ACCESS_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

/** 7 days — MUST match the backend's max draft age (round 8-4A
 * _MAX_DRAFT_AGE in app/api/v1/public_records.py). A draft older than this
 * would be rejected server-side with offline_draft_expired anyway, so the
 * frontend purges it proactively rather than letting the user try. */
export const OFFLINE_DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * A draft's persisted lifecycle status (round 8-4C). 'pending' is the only
 * status a NEW draft starts in and the only one the sequential sync engine
 * (lib/offline-inspection-sync.ts) ever attempts automatically — every
 * 'blocked_*' status requires the user to review and explicitly delete (or,
 * for cycle changes, investigate) the draft; it is NEVER auto-retried on a
 * later sync run, matching the round's "never re-attempt a blocked draft
 * silently" rule. Deliberately no 'syncing' member — see module docstring.
 */
export type OfflineInspectionDraftStatus =
  | 'pending'
  | 'blocked_cycle_changed'
  | 'blocked_access'
  | 'blocked_conflict'
  | 'blocked_expired';

export interface OfflineInspectionPhotoV1 {
  blob: Blob;
  name: string;
  type: string;
  lastModified: number;
}

/** The V1 (round 8-4B) shape — kept ONLY as the documented migration input
 * type (constructed by tests to simulate a pre-existing V1 database) and as
 * the historical record of what round 8-4B originally shipped. Every store
 * function now operates on OfflineInspectionDraftV2; nothing in production
 * code constructs a V1 record anymore. */
export interface OfflineInspectionDraftV1 {
  schemaVersion: 1;
  clientSubmissionId: string;
  capturedAt: string;
  capturedPlotCycleId: string;
  recordDate: string;

  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;

  cycleNo: number;
  cycleLabel: string | null;
  crop: string | null;
  variety: string | null;
  lotNo: string | null;
  plantingDate: string | null;

  inspectorType: PublicInspectorType;
  fields: PublicInspectionFormFields;

  photos: OfflineInspectionPhotoV1[];

  status: 'pending';
  createdAt: string;
  updatedAt: string;
}

/** One offline draft = one queued inspection form, keyed by clientSubmissionId
 * (the same id sent to the backend as the idempotency key). `schemaVersion`
 * is bumped whenever this shape changes, so a future migration can tell old
 * records apart from new ones instead of guessing.
 *
 * Deliberately NOT present on this type (see module docstring): raw phone
 * number, phoneAccessSessionToken, inspectionSessionToken, qrKey, password/
 * access code. */
export interface OfflineInspectionDraftV2 {
  schemaVersion: 2;
  clientSubmissionId: string;
  capturedAt: string;
  capturedPlotCycleId: string;
  recordDate: string;

  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;

  cycleNo: number;
  cycleLabel: string | null;
  crop: string | null;
  variety: string | null;
  lotNo: string | null;
  plantingDate: string | null;

  inspectorType: PublicInspectorType;
  fields: PublicInspectionFormFields;

  photos: OfflineInspectionPhotoV1[];

  status: OfflineInspectionDraftStatus;
  /** ISO timestamp of the most recent sync attempt for this draft, or null
   * if it has never been attempted. Round 8-4C — set only by
   * updateOfflineInspectionDraftStatus, never by a raw put. */
  lastAttemptAt: string | null;
  /** A stable machine code describing why the last attempt didn't succeed
   * (a backend structured error code, or one of the client-only codes in
   * lib/offline-inspection-sync.ts) — NEVER a raw error message/stack. Null
   * when there has been no failed attempt. */
  lastErrorCode: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Base class for every error this module raises — lets a caller catch
 * "anything went wrong with offline storage" with one instanceof check, or
 * narrow to a specific typed subclass for a tailored message. */
export class OfflineStorageError extends Error {}

/** IndexedDB doesn't exist at all in this browsing context (very old
 * browser) OR opening it failed outright (private-mode blocks it in some
 * browsers) — offline storage simply isn't usable here. */
export class OfflineStorageUnavailableError extends OfflineStorageError {}

/** The device's storage quota was exceeded while writing — typically a
 * draft with several large photos on a nearly-full device. */
export class OfflineStorageQuotaExceededError extends OfflineStorageError {}

function isIndexedDbSupported(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

/** Maps a DOMException from an IndexedDB request/transaction into one of our
 * typed errors — callers branch on the typed class, never on error.message
 * (which is browser-dependent and not localized). */
function classifyDomException(err: DOMException | Error | null | undefined): OfflineStorageError {
  const name = err && 'name' in err ? err.name : undefined;
  if (name === 'QuotaExceededError') {
    return new OfflineStorageQuotaExceededError(err?.message || 'Storage quota exceeded');
  }
  return new OfflineStorageError(err?.message || 'IndexedDB error');
}

/** True for a genuine V1 draft (schemaVersion 1) — used only by the
 * onupgradeneeded migration walk below. */
function isV1Draft(value: unknown): value is OfflineInspectionDraftV1 {
  return !!value && typeof value === 'object' && (value as { schemaVersion?: unknown }).schemaVersion === 1;
}

/** Rewrites one V1 record in place as V2 — every field V1 had is carried
 * through unchanged (identity triple, plot/cycle snapshot, fields, photo
 * Blobs included); only the three new fields are added. V1's single implicit
 * status ('pending') maps directly since offline sync didn't exist yet, so
 * every V1 draft was, by definition, still unsent. */
function migrateV1ToV2(draft: OfflineInspectionDraftV1): OfflineInspectionDraftV2 {
  return {
    ...draft,
    schemaVersion: 2,
    status: 'pending',
    lastAttemptAt: null,
    lastErrorCode: null,
  };
}

// Cached open-connection promise — every store function shares ONE
// connection rather than opening a new one per call.
let dbPromise: Promise<IDBDatabase> | null = null;

function openDatabase(): Promise<IDBDatabase> {
  if (!isIndexedDbSupported()) {
    return Promise.reject(
      new OfflineStorageUnavailableError('IndexedDB ไม่พร้อมใช้งานในอุปกรณ์นี้'),
    );
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(OFFLINE_DB_NAME, OFFLINE_DB_VERSION);
    } catch {
      reject(new OfflineStorageUnavailableError('เปิดพื้นที่จัดเก็บในเครื่องไม่สำเร็จ'));
      return;
    }

    request.onupgradeneeded = (event) => {
      const db = request.result;
      let store: IDBObjectStore;
      if (!db.objectStoreNames.contains(OFFLINE_STORE_NAME)) {
        store = db.createObjectStore(OFFLINE_STORE_NAME, { keyPath: 'clientSubmissionId' });
        store.createIndex('capturedAt', 'capturedAt');
        store.createIndex('status', 'status');
        store.createIndex('plotId', 'plotId');
      } else {
        // Existing store (upgrading from V1/V2) — indexes already exist and
        // are NOT recreated; they stay valid across the migration below since
        // IndexedDB updates every index automatically on cursor.update().
        store = request.transaction!.objectStore(OFFLINE_STORE_NAME);
      }

      if (event.oldVersion > 0 && event.oldVersion < 2) {
        // V1 -> V2: walk every existing record and rewrite the V1 ones in
        // place. Runs inside this SAME versionchange transaction, so it's
        // atomic with the store/index creation above — either the whole
        // upgrade lands or none of it does.
        const cursorReq = store.openCursor();
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result;
          if (!cursor) return;
          const value = cursor.value as unknown;
          if (isV1Draft(value)) {
            cursor.update(migrateV1ToV2(value));
          }
          cursor.continue();
        };
      }

      // Round 8-4H — V2 -> V3 (and a brand-new DB going straight to V3) adds
      // ONLY this new store. inspection_drafts (above) is never touched by
      // this branch — no cursor walk, no field rewrite — every pending/
      // blocked draft survives byte-for-byte across this upgrade.
      if (!db.objectStoreNames.contains(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME)) {
        db.createObjectStore(OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME, { keyPath: 'id' });
      }
    };

    request.onsuccess = () => {
      if (settled) return;
      settled = true;
      const db = request.result;
      // Another tab/context is upgrading to a newer version — close this
      // connection so it can proceed, and drop the cache so the NEXT call
      // reopens a fresh connection against the new version.
      db.onversionchange = () => {
        db.close();
        dbPromise = null;
      };
      resolve(db);
    };

    request.onerror = () => {
      if (settled) return;
      settled = true;
      dbPromise = null;
      reject(classifyDomException(request.error));
    };

    // Another tab holds an older-version connection open, blocking this
    // upgrade — surface it rather than leaving the caller waiting forever.
    request.onblocked = () => {
      if (settled) return;
      settled = true;
      dbPromise = null;
      reject(
        new OfflineStorageUnavailableError(
          'พื้นที่จัดเก็บในเครื่องถูกใช้งานโดยแท็บอื่น กรุณาปิดแท็บอื่นแล้วลองใหม่',
        ),
      );
    };
  });
}

/** Opens (or reuses) the shared connection. Safe to call from every store
 * function — concurrent callers share the same in-flight promise. */
export function openOfflineInspectionDb(): Promise<IDBDatabase> {
  if (!dbPromise) {
    dbPromise = openDatabase();
  }
  return dbPromise;
}

/** Closes and drops the cached connection — mainly for tests that need a
 * clean slate between cases, or a caller that wants to release the handle
 * explicitly (e.g. before a version bump in a future round). */
export function closeOfflineInspectionDb(): void {
  if (dbPromise) {
    dbPromise.then((db) => db.close()).catch(() => {});
    dbPromise = null;
  }
}

/** Runs one request inside a transaction and resolves with that request's
 * result once the transaction fully commits (not just when the request
 * succeeds) — the standard "wait for tx.oncomplete" pattern, so a caller
 * never observes a write that later got rolled back. Handles onerror (the
 * request), onabort and onerror (the transaction) — a QuotaExceededError
 * during a write can surface either as a SYNCHRONOUS throw from the store
 * method itself or asynchronously through the transaction's abort,
 * depending on the engine, so both paths are classified the same way. */
async function runInTransaction<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
  storeName: string = OFFLINE_STORE_NAME,
): Promise<T> {
  const db = await openOfflineInspectionDb();
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    let result: T;
    const tx = db.transaction(storeName, mode);
    const store = tx.objectStore(storeName);
    let request: IDBRequest<T>;
    try {
      request = run(store);
    } catch (err) {
      reject(classifyDomException(err instanceof DOMException || err instanceof Error ? err : null));
      return;
    }

    request.onsuccess = () => {
      result = request.result;
    };
    request.onerror = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(request.error));
    };
    tx.oncomplete = () => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    tx.onerror = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(tx.error));
    };
    tx.onabort = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(tx.error));
    };
  });
}

/** Upsert by clientSubmissionId — a retry that reuses the same identity
 * overwrites the same record rather than creating a duplicate draft. */
export async function putOfflineInspectionDraft(draft: OfflineInspectionDraftV2): Promise<void> {
  await runInTransaction('readwrite', (store) => store.put(draft));
}

export async function getOfflineInspectionDraft(
  clientSubmissionId: string,
): Promise<OfflineInspectionDraftV2 | null> {
  const result = await runInTransaction<OfflineInspectionDraftV2 | undefined>(
    'readonly',
    (store) => store.get(clientSubmissionId),
  );
  return result ?? null;
}

/** Most-recently-captured first — the queue panel's display order. */
export async function listOfflineInspectionDrafts(): Promise<OfflineInspectionDraftV2[]> {
  const all = await runInTransaction<OfflineInspectionDraftV2[]>(
    'readonly',
    (store) => store.getAll(),
  );
  return [...all].sort((a, b) => b.capturedAt.localeCompare(a.capturedAt));
}

/** Total drafts in the store, regardless of status — used where "how big is
 * the whole queue" matters (e.g. the clear-all confirm). */
export async function countOfflineInspectionDrafts(): Promise<number> {
  return runInTransaction<number>('readonly', (store) => store.count());
}

/** Round 8-4C — drafts still eligible for automatic sync (status === 'pending'
 * only). A 'blocked_*' draft is NOT unsent-in-this-sense: it needs a human to
 * look at it, so it's deliberately excluded from this count — the top-of-page
 * "รายการรอส่ง N" pill uses countOfflineInspectionDrafts (the whole queue,
 * since a blocked draft still occupies device storage and still needs
 * attention), while the queue panel's sync summary uses THIS count for "how
 * many will actually be sent". */
export async function countUnsentOfflineInspectionDrafts(): Promise<number> {
  return runInTransaction<number>(
    'readonly',
    (store) => store.index('status').count(IDBKeyRange.only('pending')),
  );
}

/** Deletes one draft (and, since photos live inside the record, its photo
 * blobs with it — no separate cleanup step). */
export async function deleteOfflineInspectionDraft(clientSubmissionId: string): Promise<void> {
  await runInTransaction('readwrite', (store) => store.delete(clientSubmissionId));
}

export async function clearAllOfflineInspectionDrafts(): Promise<void> {
  await runInTransaction('readwrite', (store) => store.clear());
}

/** Deletes every draft older than the retention window (default: the same
 * 7-day window the backend enforces) and returns how many were removed, so
 * the caller can show a plain count — never the purged drafts' own data
 * (plot/crop/etc. would be PII-adjacent for a deleted record). Applies to
 * EVERY status, including blocked ones — an old blocked draft is purged the
 * same as an old pending one; it never gets a special reprieve. Purging never
 * distinguishes or announces which status a purged draft had, so a caller
 * can never mistake "purged" for "successfully sent" (they're deleted the
 * same way, but the purge count is reported with its own distinct copy). */
export async function purgeExpiredOfflineInspectionDrafts(
  now: Date,
  maxAgeMs: number = OFFLINE_DRAFT_MAX_AGE_MS,
): Promise<number> {
  const drafts = await listOfflineInspectionDrafts();
  const cutoff = now.getTime() - maxAgeMs;
  const expired = drafts.filter((d) => new Date(d.capturedAt).getTime() < cutoff);
  for (const draft of expired) {
    await deleteOfflineInspectionDraft(draft.clientSubmissionId);
  }
  return expired.length;
}

/** Shared read-then-write primitive for every "mutate one existing draft in
 * place" operation below — a single readwrite transaction so the caller only
 * observes the update once it's durably committed (`tx.oncomplete`), and a
 * draft deleted concurrently (e.g. the user removed it mid-sync, or two tabs
 * race) is silently a no-op rather than recreating it. `mutate` must be pure
 * (no side effects) — it may run again if the surrounding transaction retries. */
async function mutateExistingDraft(
  clientSubmissionId: string,
  mutate: (existing: OfflineInspectionDraftV2) => OfflineInspectionDraftV2,
): Promise<void> {
  const db = await openOfflineInspectionDb();
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const tx = db.transaction(OFFLINE_STORE_NAME, 'readwrite');
    const store = tx.objectStore(OFFLINE_STORE_NAME);
    const getReq = store.get(clientSubmissionId);

    getReq.onsuccess = () => {
      const existing = getReq.result as OfflineInspectionDraftV2 | undefined;
      if (!existing) return; // already deleted — nothing to update
      try {
        store.put(mutate(existing));
      } catch (err) {
        if (settled) return;
        settled = true;
        reject(classifyDomException(err instanceof DOMException || err instanceof Error ? err : null));
      }
    };
    getReq.onerror = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(getReq.error));
    };
    tx.oncomplete = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    tx.onerror = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(tx.error));
    };
    tx.onabort = () => {
      if (settled) return;
      settled = true;
      reject(classifyDomException(tx.error));
    };
  });
}

/** Round 8-4C — transitions one draft's persisted status (e.g. after a sync
 * attempt fails with a classifiable error) and stamps lastAttemptAt/
 * lastErrorCode. Never transitions to a 'syncing' status — see module
 * docstring for why that's never persisted. */
export async function updateOfflineInspectionDraftStatus(
  clientSubmissionId: string,
  status: OfflineInspectionDraftStatus,
  options: { lastErrorCode?: string | null } = {},
): Promise<void> {
  const now = new Date().toISOString();
  await mutateExistingDraft(clientSubmissionId, (existing) => ({
    ...existing,
    status,
    lastAttemptAt: now,
    lastErrorCode: options.lastErrorCode ?? null,
    updatedAt: now,
  }));
}

/** Round 8-4C.2 Part B — resets a RECOVERABLE blocked draft back to
 * 'pending' so the next sync run will attempt it again. ONLY 'blocked_access'
 * is recoverable this way (an admin may have reopened the assignment) — the
 * store enforces this itself rather than trusting the UI caller: a draft in
 * any OTHER status (including 'blocked_cycle_changed', which can never
 * succeed again with its original capturedPlotCycleId — see
 * RETRYABLE_STATUSES in OfflineInspectionQueuePanel for the full reasoning)
 * is returned completely UNCHANGED, a true no-op. Deliberately does NOT touch
 * `lastAttemptAt` (kept as an audit trail — "when did we last actually try
 * this") and does NOT create a new clientSubmissionId/capturedAt/
 * capturedPlotCycleId — retrying means giving the SAME identity another
 * chance, which also lets an uncertain prior attempt (one the server may
 * have already committed) resolve as a safe idempotent replay rather than
 * risk a duplicate. Never re-sends by itself — the caller still needs a
 * fresh re-auth + an explicit "ส่งรายการรอส่ง" through the normal flow. */
export async function resetOfflineInspectionDraftForRetry(clientSubmissionId: string): Promise<void> {
  const now = new Date().toISOString();
  await mutateExistingDraft(clientSubmissionId, (existing) => {
    if (existing.status !== 'blocked_access') return existing;
    return {
      ...existing,
      status: 'pending',
      lastErrorCode: null,
      updatedAt: now,
    };
  });
}

/**
 * The ONLY function that may construct an OfflineInspectionDraftV2 — takes
 * explicit named inputs (never a spread of a raw component-state object), so
 * every field that ends up in IndexedDB is visibly accounted for here. Adding
 * a new field to PublicInspect's React state does NOT automatically leak it
 * into the draft; a field must be deliberately wired through this signature.
 * Always builds a fresh 'pending' draft — a draft's status only ever changes
 * afterwards via updateOfflineInspectionDraftStatus.
 */
export function buildOfflineInspectionDraft(input: {
  clientSubmissionId: string;
  capturedAt: string;
  capturedPlotCycleId: string;
  recordDate: string;
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  cycleNo: number;
  cycleLabel: string | null;
  crop: string | null;
  variety: string | null;
  lotNo: string | null;
  plantingDate: string | null;
  inspectorType: PublicInspectorType;
  fields: PublicInspectionFormFields;
  photos: OfflineInspectionPhotoV1[];
  now: string;
}): OfflineInspectionDraftV2 {
  return {
    schemaVersion: 2,
    clientSubmissionId: input.clientSubmissionId,
    capturedAt: input.capturedAt,
    capturedPlotCycleId: input.capturedPlotCycleId,
    recordDate: input.recordDate,
    plotId: input.plotId,
    plotCode: input.plotCode,
    plotName: input.plotName,
    supplierId: input.supplierId,
    supplierCode: input.supplierCode,
    supplierName: input.supplierName,
    cycleNo: input.cycleNo,
    cycleLabel: input.cycleLabel,
    crop: input.crop,
    variety: input.variety,
    lotNo: input.lotNo,
    plantingDate: input.plantingDate,
    inspectorType: input.inspectorType,
    // A plain object copy of the (already narrowly-typed) form fields — this
    // is NOT a raw component-state spread; PublicInspectionFormFields is
    // itself the finite allowlisted shape.
    fields: { ...input.fields },
    photos: input.photos,
    status: 'pending',
    lastAttemptAt: null,
    lastErrorCode: null,
    createdAt: input.now,
    updatedAt: input.now,
  };
}

/** Converts the picked photo Files into the Blob-based shape a draft stores.
 * A File already IS a Blob (with name/type/lastModified attached), so this
 * is a plain re-projection, not a re-encode. */
export function filesToOfflinePhotos(files: readonly (File | null)[]): OfflineInspectionPhotoV1[] {
  return files
    .filter((f): f is File => f !== null)
    .map((f) => ({ blob: f, name: f.name, type: f.type, lastModified: f.lastModified }));
}

/** Reconstructs a File from a stored photo — needed when a draft is
 * resubmitted (round 8-4C sequential sync, lib/offline-inspection-sync.ts)
 * through the same with-photos endpoint, which takes File objects. */
export function offlinePhotoToFile(photo: OfflineInspectionPhotoV1): File {
  return new File([photo.blob], photo.name, { type: photo.type, lastModified: photo.lastModified });
}

// ============================================================================
// Round 8-4H — Persistent Offline Authorized Plot Cache
// ============================================================================

/** One plot from the last successful phone lookup, narrowed to exactly what
 * an offline form needs to render + submit an inspection — an explicit
 * ALLOWLIST (never a spread of PublicPhoneAccessPlot), so a field added to
 * that API type later does NOT silently leak into persistent storage.
 * Deliberately excludes: any phone number, any token, qrKey, accessType,
 * unavailableReason (always canInspect=true by construction — see
 * buildOfflinePublicAccessCache — so there is no "reason" to store). */
export interface OfflineCachedPlot {
  plotId: string;
  plotCode: string;
  plotName: string;
  supplierId: string;
  supplierCode: string;
  supplierName: string;
  plotCycleId: string;
  cycleNo: number;
  cycleLabel: string | null;
  crop: string | null;
  variety: string | null;
  lotNo: string | null;
  plantingDate: string | null;
  plantCount: number | null;
  expectedYieldFull: string | number | null;
  expectedYieldUnit: string | null;
  currentYieldPct: string | number | null;
  currentStage: string | null;
  lastInspectedAt: string | null;
  inspectedToday: boolean;
}

/** The persisted snapshot of the last successful ONLINE phone lookup — plots
 * this device is authorized to inspect, plus the reference data (protocol +
 * growth_stage/weather master data) an offline form needs to render without
 * guessing. Single row, key 'latest', replaced whole on every fresh
 * successful lookup (round 8-4H requirement — never appended/merged with a
 * previous number's list). Deliberately carries NO phone number, NO
 * phoneAccessSessionToken/inspectionSessionToken, NO qrKey — this is a
 * DEVICE-LOCAL "last seen" convenience cache, not an authentication
 * credential; the backend re-authorizes from scratch on every sync. */
export interface OfflinePublicAccessCacheV1 {
  id: 'latest';
  version: 1;
  cachedAt: string;
  expiresAt: string;
  plots: OfflineCachedPlot[];
  protocols: InspectionProtocolResponse;
  masterData: {
    growthStage: PublicMasterDataItem[];
    weather: PublicMasterDataItem[];
  };
}

/** True for a structurally-valid V1 cache row — malformed/foreign data found
 * in the store (a future incompatible version, browser storage corruption,
 * etc.) must be treated as "unavailable", never thrown/crashed on. Checks
 * shape only, not expiry (see isOfflinePublicAccessCacheValid for that). */
function isWellFormedPublicAccessCache(value: unknown): value is OfflinePublicAccessCacheV1 {
  if (!value || typeof value !== 'object') return false;
  const v = value as Partial<OfflinePublicAccessCacheV1>;
  return (
    v.id === 'latest'
    && v.version === 1
    && typeof v.cachedAt === 'string'
    && typeof v.expiresAt === 'string'
    && Array.isArray(v.plots)
    && !!v.protocols && typeof v.protocols === 'object' && Array.isArray(v.protocols.stages)
    && !!v.masterData && Array.isArray(v.masterData.growthStage) && Array.isArray(v.masterData.weather)
  );
}

/**
 * The ONLY function that may construct an OfflinePublicAccessCacheV1 — takes
 * the raw plot list from a successful lookup/list response and the raw
 * protocol/master-data responses, and builds the persisted allowlisted shape.
 *
 * Filters to plots that are actually usable offline (round 8-4H requirement:
 * "cache เฉพาะ plot ที่ canInspect=true และมี active plotCycleId/cycleNo") —
 * a plot with no active cycle can never open an inspection form anyway (see
 * PublicInspect's buildOfflinePlotInfo), so caching it would be dead weight
 * that could also go stale/misleading while offline.
 *
 * Returns null when there is nothing cacheable (no canInspect plots this
 * lookup) — the caller (PublicInspect, round 8-4H Part D) must then CLEAR any
 * previous cache rather than write an empty one, so a later device session
 * never sees a stale prior number's plot list under a "no results" cache.
 */
export function buildOfflinePublicAccessCache(input: {
  plots: PublicPhoneAccessPlot[];
  protocols: InspectionProtocolResponse;
  masterData: { growthStage: PublicMasterDataItem[]; weather: PublicMasterDataItem[] };
  now: string;
}): OfflinePublicAccessCacheV1 | null {
  const cacheablePlots: OfflineCachedPlot[] = input.plots
    .filter((p) => p.canInspect && p.plotCycleId != null && p.cycleNo != null)
    .map((p) => ({
      plotId: p.plotId,
      plotCode: p.plotCode,
      plotName: p.plotName,
      supplierId: p.supplierId,
      supplierCode: p.supplierCode,
      supplierName: p.supplierName,
      // Non-null by construction — filtered above.
      plotCycleId: p.plotCycleId as string,
      cycleNo: p.cycleNo as number,
      cycleLabel: p.cycleLabel,
      crop: p.crop,
      variety: p.variety,
      lotNo: p.lotNo,
      plantingDate: p.plantingDate,
      plantCount: p.plantCount,
      expectedYieldFull: p.expectedYieldFull,
      expectedYieldUnit: p.expectedYieldUnit,
      currentYieldPct: p.currentYieldPct,
      currentStage: p.currentStage,
      lastInspectedAt: p.lastInspectedAt,
      inspectedToday: p.inspectedToday,
    }));

  if (cacheablePlots.length === 0) return null;

  const cachedAtMs = new Date(input.now).getTime();
  return {
    id: 'latest',
    version: 1,
    cachedAt: input.now,
    expiresAt: new Date(cachedAtMs + OFFLINE_PUBLIC_ACCESS_CACHE_TTL_MS).toISOString(),
    plots: cacheablePlots,
    protocols: input.protocols,
    masterData: {
      growthStage: input.masterData.growthStage,
      weather: input.masterData.weather,
    },
  };
}

/** True when a cache row exists, is well-formed, and has not passed its
 * expiresAt as of `now`. Never throws — a malformed row is simply "invalid"
 * (see isWellFormedPublicAccessCache), matching the round's "malformed cache
 * ต้องถือว่า unavailable และไม่ทำให้หน้า crash" requirement. */
export function isOfflinePublicAccessCacheValid(
  cache: OfflinePublicAccessCacheV1 | null | undefined,
  now: Date,
): boolean {
  if (!cache || !isWellFormedPublicAccessCache(cache)) return false;
  return new Date(cache.expiresAt).getTime() > now.getTime();
}

/** Upsert — key is always the fixed 'latest', so this REPLACES any previous
 * row (round 8-4H: "cache ล่าสุดต้อง replace cache ก่อนหน้า ไม่ append
 * รวมกัน"). Storage/quota failures surface as the same typed
 * OfflineStorage*Error classes putOfflineInspectionDraft uses, so an existing
 * caller pattern (catch OfflineStorageQuotaExceededError) works unchanged. */
export async function putOfflinePublicAccessCache(cache: OfflinePublicAccessCacheV1): Promise<void> {
  await runInTransaction(
    'readwrite',
    (store) => store.put(cache),
    OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME,
  );
}

/** Reads the 'latest' cache row. Returns null when absent OR malformed — a
 * malformed row is treated as "no cache" rather than thrown, so a caller can
 * always safely branch on null without a try/catch of its own for this
 * specific case (genuine IndexedDB-unavailable errors still reject the
 * promise, same as every other function in this module). */
export async function getOfflinePublicAccessCache(): Promise<OfflinePublicAccessCacheV1 | null> {
  const result = await runInTransaction<OfflinePublicAccessCacheV1 | undefined>(
    'readonly',
    (store) => store.get(PUBLIC_ACCESS_CACHE_KEY),
    OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME,
  );
  if (!result || !isWellFormedPublicAccessCache(result)) return null;
  return result;
}

/** Deletes only the public_access_cache row — the inspection_drafts store
 * (a completely separate IndexedDB object store) is never touched by this
 * call, so pending/blocked offline queue drafts are always unaffected (round
 * 8-4H requirement: "การล้าง cache ห้ามลบ inspection drafts ที่รอส่ง"). */
export async function clearOfflinePublicAccessCache(): Promise<void> {
  await runInTransaction(
    'readwrite',
    (store) => store.delete(PUBLIC_ACCESS_CACHE_KEY),
    OFFLINE_PUBLIC_ACCESS_CACHE_STORE_NAME,
  );
}
