/**
 * offline-inspection-sync — round 8-4C. Uses the REAL (fake-indexeddb
 * polyfilled) store module for drafts (put/list/delete/status), and mocks
 * only the network-facing API calls — same pattern as
 * OfflineInspectionQueuePanel.test.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { syncOfflineDrafts } from './offline-inspection-sync';
import {
  buildOfflineInspectionDraft,
  closeOfflineInspectionDb,
  getOfflineInspectionDraft,
  putOfflineInspectionDraft,
  updateOfflineInspectionDraftStatus,
  type OfflineInspectionDraftV2,
} from './offline-inspection-store';
import type { PublicInspectionFormFields } from '../api/publicInspection';

const selectPlotMock = vi.fn();
vi.mock('../api/publicInspectionAccess', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/publicInspectionAccess')>();
  return { ...actual, selectPublicInspectionPlot: (...args: unknown[]) => selectPlotMock(...args) };
});

const createJsonMock = vi.fn();
const createWithPhotosMock = vi.fn();
vi.mock('../api/publicInspection', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/publicInspection')>();
  return {
    ...actual,
    createPublicInspectionRecord: (...args: unknown[]) => createJsonMock(...args),
    createPublicRecordWithPhotos: (...args: unknown[]) => createWithPhotosMock(...args),
  };
});

const EMPTY_FIELDS: PublicInspectionFormFields = {
  submittedByName: '', growthStage: '', yieldPct: 100, yieldQuantityKg: null, weatherCondition: '',
  fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
  recommendation: '', notes: '', latitude: null, longitude: null,
};

function jpegFile(name: string): File {
  return new File(['x'], name, { type: 'image/jpeg' });
}

function draftFor(overrides: Partial<Parameters<typeof buildOfflineInspectionDraft>[0]> = {}, capturedAt = '2026-07-15T09:00:00.000Z'): OfflineInspectionDraftV2 {
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

beforeEach(() => {
  closeOfflineInspectionDb();
  (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
  selectPlotMock.mockReset();
  createJsonMock.mockReset();
  createWithPhotosMock.mockReset();
});

afterEach(() => {
  closeOfflineInspectionDb();
});

describe('syncOfflineDrafts — ordering and scope', () => {
  it('sends only pending drafts within the authorized plot set, oldest capturedAt first', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'newer' }, '2026-07-15T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'older' }, '2026-07-10T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'other-plot', plotId: 'plot-2' }, '2026-07-01T00:00:00.000Z'));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec' });

    const order: string[] = [];
    selectPlotMock.mockImplementation(async (_token: string, plotId: string) => {
      order.push(plotId);
      return { inspectionSessionToken: 'insp-tok' };
    });

    const progress: number[] = [];
    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), (p) => progress.push(p.current));

    expect(order).toEqual(['plot-1', 'plot-1']); // only plot-1 drafts, twice
    expect(summary.sentCount).toBe(2);
    expect(summary.totalAttempted).toBe(2);
    expect(summary.stopReason).toBeNull();
    expect(progress).toEqual([1, 2]);
    // The out-of-scope plot-2 draft was never touched.
    expect(await getOfflineInspectionDraft('other-plot')).not.toBeNull();
  });

  it('never runs two drafts concurrently — each selectPlot call only starts after the previous create resolved', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'a' }, '2026-07-10T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'b' }, '2026-07-11T00:00:00.000Z'));
    const events: string[] = [];
    selectPlotMock.mockImplementation(async () => {
      events.push('select');
      return { inspectionSessionToken: 'insp-tok' };
    });
    createJsonMock.mockImplementation(async () => {
      events.push('create-start');
      await new Promise((r) => setTimeout(r, 5));
      events.push('create-end');
      return { id: 'rec' };
    });

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(events).toEqual(['select', 'create-start', 'create-end', 'select', 'create-start', 'create-end']);
  });

  it('a draft with photos uses the with-photos endpoint; a draft without uses the plain JSON endpoint', async () => {
    await putOfflineInspectionDraft(draftFor({
      clientSubmissionId: 'with-photo',
      photos: [{ blob: jpegFile('a.jpg'), name: 'a.jpg', type: 'image/jpeg', lastModified: 1 }],
    }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'no-photo' }, '2026-07-01T00:00:00.000Z'));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec-json' });
    createWithPhotosMock.mockResolvedValue({ id: 'rec-photo' });

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(createJsonMock).toHaveBeenCalledTimes(1);
    expect(createWithPhotosMock).toHaveBeenCalledTimes(1);
    const [payload, files] = createWithPhotosMock.mock.calls[0];
    expect(payload.clientSubmissionId).toBe('with-photo');
    expect(files).toHaveLength(1);
    expect(files[0]).toBeInstanceOf(File);
    expect(files[0].name).toBe('a.jpg');
  });

  it('sends the draft\'s ORIGINAL captured fields — never re-pointed at a new cycle', async () => {
    await putOfflineInspectionDraft(draftFor({
      clientSubmissionId: 'orig', capturedPlotCycleId: 'cycle-original', capturedAt: '2026-07-10T09:00:00.000Z',
      recordDate: '2026-07-10',
    }, '2026-07-10T09:00:00.000Z'));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'fresh-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec' });

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    const [payload] = createJsonMock.mock.calls[0];
    expect(payload.inspectionSessionToken).toBe('fresh-tok'); // fresh token, per draft
    expect(payload.clientSubmissionId).toBe('orig');
    expect(payload.capturedAt).toBe('2026-07-10T09:00:00.000Z');
    expect(payload.capturedPlotCycleId).toBe('cycle-original');
    expect(payload.recordDate).toBe('2026-07-10');
  });

  it('a successful send deletes the draft from IndexedDB', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'sent-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec' });

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(await getOfflineInspectionDraft('sent-1')).toBeNull();
  });

  it('never attempts an already-blocked draft, even if its plot is authorized', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'blocked-1' }));
    await updateOfflineInspectionDraftStatus('blocked-1', 'blocked_cycle_changed', { lastErrorCode: 'planting_cycle_changed' });

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.totalAttempted).toBe(0);
    expect(selectPlotMock).not.toHaveBeenCalled();
    const still = await getOfflineInspectionDraft('blocked-1');
    expect(still?.status).toBe('blocked_cycle_changed'); // unchanged, never auto-retried
  });
});

function axiosError(status: number, code?: string) {
  return { isAxiosError: true, response: { status, data: code ? { detail: { code } } : {} } };
}

describe('syncOfflineDrafts — error matrix (Part F)', () => {
  it('401 stops the batch; the current and remaining drafts stay pending', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'first' }, '2026-07-01T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'second' }, '2026-07-02T00:00:00.000Z'));
    selectPlotMock.mockRejectedValue(axiosError(401));

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('unauthorized');
    expect(summary.sentCount).toBe(0);
    expect((await getOfflineInspectionDraft('first'))?.status).toBe('pending');
    expect((await getOfflineInspectionDraft('second'))?.status).toBe('pending');
    // The second draft's selectPlot was never even attempted.
    expect(selectPlotMock).toHaveBeenCalledTimes(1);
  });

  it('a network error (no response) stops the batch; drafts stay pending, no new key is created', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'net-1' }));
    selectPlotMock.mockRejectedValue({ isAxiosError: true, response: undefined });

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('network');
    const draft = await getOfflineInspectionDraft('net-1');
    expect(draft?.status).toBe('pending');
    expect(draft?.clientSubmissionId).toBe('net-1'); // same key, never rotated
  });

  it('429 stops the batch and records a diagnostic lastErrorCode, draft stays pending', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'rl-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(429));

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('rate_limited');
    const draft = await getOfflineInspectionDraft('rl-1');
    expect(draft?.status).toBe('pending');
    expect(draft?.lastErrorCode).toBe('rate_limited');
    expect(draft?.lastAttemptAt).not.toBeNull();
  });

  it('5xx stops the batch, draft stays pending with a diagnostic lastErrorCode', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'srv-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(500));

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('server_error');
    expect((await getOfflineInspectionDraft('srv-1'))?.status).toBe('pending');
  });

  it('404 during select-plot marks blocked_access and the batch CONTINUES to the next draft', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'revoked' }, '2026-07-01T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'fine' }, '2026-07-02T00:00:00.000Z'));
    selectPlotMock
      .mockRejectedValueOnce(axiosError(404))
      .mockResolvedValueOnce({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec' });

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.blockedCount).toBe(1);
    expect(summary.sentCount).toBe(1);
    expect(summary.stopReason).toBeNull();
    expect((await getOfflineInspectionDraft('revoked'))?.status).toBe('blocked_access');
    expect(await getOfflineInspectionDraft('fine')).toBeNull(); // sent, deleted
  });

  it('404 during create marks blocked_access, never deleted, and the draft is not resent within the same batch', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'gone-mid-create' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(404));

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(selectPlotMock).toHaveBeenCalledTimes(1);
    expect((await getOfflineInspectionDraft('gone-mid-create'))?.status).toBe('blocked_access');
  });

  it('409 planting_cycle_changed marks blocked_cycle_changed and never re-points the draft at a new cycle', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'cycle-1', capturedPlotCycleId: 'old-cycle' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(409, 'planting_cycle_changed'));

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    const draft = await getOfflineInspectionDraft('cycle-1');
    expect(draft?.status).toBe('blocked_cycle_changed');
    expect(draft?.lastErrorCode).toBe('planting_cycle_changed');
    expect(draft?.capturedPlotCycleId).toBe('old-cycle'); // untouched
  });

  it('409 idempotency_conflict marks blocked_conflict and keeps the same clientSubmissionId', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'dup-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(409, 'idempotency_conflict'));

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    const draft = await getOfflineInspectionDraft('dup-1');
    expect(draft?.status).toBe('blocked_conflict');
    expect(draft?.clientSubmissionId).toBe('dup-1');
  });

  it('an unrecognized 409 (e.g. select-plot\'s no_active_cycle) maps to blocked_cycle_changed and continues the batch', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'no-cycle-1' }, '2026-07-01T00:00:00.000Z'));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'fine-2' }, '2026-07-02T00:00:00.000Z'));
    selectPlotMock
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: { code: 'no_active_cycle' } } } })
      .mockResolvedValueOnce({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockResolvedValue({ id: 'rec' });

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBeNull();
    expect((await getOfflineInspectionDraft('no-cycle-1'))?.status).toBe('blocked_cycle_changed');
  });

  it('422 offline_draft_expired marks blocked_expired', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'exp-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(422, 'offline_draft_expired'));

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect((await getOfflineInspectionDraft('exp-1'))?.status).toBe('blocked_expired');
  });

  it('422 offline_captured_at_invalid marks blocked_expired', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'inv-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(422, 'offline_captured_at_invalid'));

    await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect((await getOfflineInspectionDraft('inv-1'))?.status).toBe('blocked_expired');
  });

  it('an unrecognized 4xx never guesses success — stops the batch, draft stays pending', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'weird-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(403));

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('unknown_error');
    const draft = await getOfflineInspectionDraft('weird-1');
    expect(draft?.status).toBe('pending');
    expect(draft?.lastErrorCode).toBe('unknown_error');
  });

  it('an unrecognized 422 (no known code) never guesses success — stops the batch', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'weird-422' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    createJsonMock.mockRejectedValue(axiosError(422));

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.stopReason).toBe('unknown_error');
    expect((await getOfflineInspectionDraft('weird-422'))?.status).toBe('pending');
  });

  it('an idempotent replay (200-style success, axios does not throw) also deletes the draft', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'replay-1' }));
    selectPlotMock.mockResolvedValue({ inspectionSessionToken: 'insp-tok' });
    // A replay resolves normally (axios only throws for non-2xx) — same as a
    // fresh 201 create, from this module's point of view.
    createJsonMock.mockResolvedValue({ id: 'rec-replayed', clientSubmissionId: 'replay-1' });

    const summary = await syncOfflineDrafts('phone-tok', new Set(['plot-1']), () => {});

    expect(summary.sentCount).toBe(1);
    expect(await getOfflineInspectionDraft('replay-1')).toBeNull();
  });
});
