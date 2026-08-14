/**
 * offline-inspection-sync — round 8-4C sequential sync engine. Pure,
 * testable orchestration extracted out of the UI: OfflineInspectionQueuePanel
 * calls syncOfflineDrafts once the user has re-authenticated with a fresh
 * phone-access token and confirmed a summary; PublicInspect.tsx never calls
 * this directly.
 *
 * Non-negotiable rules from the round brief:
 *   - ONE draft at a time, oldest capturedAt first — never Promise.all.
 *   - Only status === 'pending' drafts are ever attempted; a 'blocked_*'
 *     draft is NEVER auto-retried by this function (the user must delete it,
 *     or a future round could add an explicit single-draft retry action —
 *     not built here).
 *   - A fresh inspectionSessionToken is minted per draft via
 *     selectPublicInspectionPlot — no token is cached/reused across drafts.
 *   - A network error (no HTTP response) or a 401 STOPS the whole batch;
 *     every remaining draft (including the one in flight) stays exactly as
 *     it was — never guessed at, never silently marked sent.
 *   - Every draft sent from HERE carries its ORIGINAL captured fields
 *     (recordDate/fields/clientSubmissionId/capturedAt/capturedPlotCycleId)
 *     unchanged — never re-pointed at a new planting cycle.
 */
import axios from 'axios';
import {
  listOfflineInspectionDrafts,
  deleteOfflineInspectionDraft,
  updateOfflineInspectionDraftStatus,
  offlinePhotoToFile,
  type OfflineInspectionDraftV2,
  type OfflineInspectionDraftStatus,
} from './offline-inspection-store';
import {
  createPublicInspectionRecord,
  createPublicRecordWithPhotos,
  buildOfflinePublicRecordPayload,
} from '../api/publicInspection';
import { selectPublicInspectionPlot } from '../api/publicInspectionAccess';
import { extractOfflineErrorCode } from './offline-submission-errors';

/** Why a sync batch stopped before every eligible draft was attempted. */
export type SyncStopReason = 'unauthorized' | 'network' | 'rate_limited' | 'server_error' | 'unknown_error';

export interface SyncProgress {
  /** 1-based index of the draft currently being sent. */
  current: number;
  total: number;
  plotCode: string;
  plotName: string;
}

export interface SyncSummary {
  /** How many eligible drafts this batch actually attempted (sent + blocked
   * + the one in flight when it stopped, if any). */
  totalAttempted: number;
  sentCount: number;
  blockedCount: number;
  /** null when every eligible draft was attempted without the batch being
   * cut short (some may still have ended up blocked_* — that's not a stop). */
  stopReason: SyncStopReason | null;
}

type DraftOutcome =
  | { kind: 'sent' }
  | { kind: 'blocked' }
  | { kind: 'stopped'; reason: SyncStopReason };

/**
 * Sends every 'pending' draft whose plotId is in `authorizedPlotIds`, oldest
 * capturedAt first. `phoneAccessSessionToken` must already be a freshly
 * re-authenticated phone-access token — the caller (the queue panel) owns
 * the re-auth UI and the confirm step; this function only mints per-draft
 * inspectionSessionTokens from it.
 */
export async function syncOfflineDrafts(
  phoneAccessSessionToken: string,
  authorizedPlotIds: ReadonlySet<string>,
  onProgress: (progress: SyncProgress) => void,
): Promise<SyncSummary> {
  const all = await listOfflineInspectionDrafts();
  const eligible = all
    .filter((d) => d.status === 'pending' && authorizedPlotIds.has(d.plotId))
    .sort((a, b) => a.capturedAt.localeCompare(b.capturedAt)); // oldest first

  let sentCount = 0;
  let blockedCount = 0;

  for (let i = 0; i < eligible.length; i++) {
    const draft = eligible[i];
    onProgress({ current: i + 1, total: eligible.length, plotCode: draft.plotCode, plotName: draft.plotName });

    const outcome = await sendOneDraft(phoneAccessSessionToken, draft);

    if (outcome.kind === 'sent') {
      sentCount++;
      continue;
    }
    if (outcome.kind === 'blocked') {
      blockedCount++;
      continue;
    }
    return { totalAttempted: i + 1, sentCount, blockedCount, stopReason: outcome.reason };
  }

  return { totalAttempted: eligible.length, sentCount, blockedCount, stopReason: null };
}

async function sendOneDraft(
  phoneAccessSessionToken: string,
  draft: OfflineInspectionDraftV2,
): Promise<DraftOutcome> {
  let inspectionSessionToken: string;
  try {
    const selected = await selectPublicInspectionPlot(phoneAccessSessionToken, draft.plotId, draft.inspectorType);
    inspectionSessionToken = selected.inspectionSessionToken;
  } catch (err) {
    return classifyFailure(draft, err);
  }

  const payload = buildOfflinePublicRecordPayload(
    inspectionSessionToken,
    draft.recordDate,
    draft.fields,
    {
      clientSubmissionId: draft.clientSubmissionId,
      capturedAt: draft.capturedAt,
      capturedPlotCycleId: draft.capturedPlotCycleId,
    },
  );
  const photoFiles = draft.photos.map(offlinePhotoToFile);

  try {
    if (photoFiles.length > 0) {
      await createPublicRecordWithPhotos(payload, photoFiles);
    } else {
      await createPublicInspectionRecord(payload);
    }
    // 201 (created) or 200 (idempotent replay) both land here — axios only
    // throws for a non-2xx status, same convention PublicInspect's own
    // online submit relies on.
    await deleteOfflineInspectionDraft(draft.clientSubmissionId);
    return { kind: 'sent' };
  } catch (err) {
    return classifyFailure(draft, err);
  }
}

async function classifyFailure(draft: OfflineInspectionDraftV2, err: unknown): Promise<DraftOutcome> {
  const mark = (status: OfflineInspectionDraftStatus, lastErrorCode: string) =>
    updateOfflineInspectionDraftStatus(draft.clientSubmissionId, status, { lastErrorCode });

  if (!axios.isAxiosError(err) || !err.response) {
    // No HTTP response at all (dropped connection, DNS failure, timeout) —
    // the server's outcome is genuinely unknown. Never guess; stop the whole
    // batch and leave every remaining draft untouched.
    return { kind: 'stopped', reason: 'network' };
  }

  const status = err.response.status;

  if (status === 401) {
    // The phone-access token expired mid-batch — stop; the panel clears its
    // token and asks the user to re-enter the access number.
    return { kind: 'stopped', reason: 'unauthorized' };
  }

  if (status === 404) {
    // select-plot OR create returned 404 (assignment revoked / plot,
    // supplier, or cycle deactivated since capture) — this ONE draft is
    // blocked, but the batch continues to the next.
    await mark('blocked_access', 'not_found');
    return { kind: 'blocked' };
  }

  if (status === 409 || status === 422) {
    const code = extractOfflineErrorCode(err);
    if (code === 'planting_cycle_changed') {
      await mark('blocked_cycle_changed', code);
      return { kind: 'blocked' };
    }
    if (code === 'idempotency_conflict') {
      await mark('blocked_conflict', code);
      return { kind: 'blocked' };
    }
    if (code === 'offline_draft_expired' || code === 'offline_captured_at_invalid') {
      await mark('blocked_expired', code);
      return { kind: 'blocked' };
    }
    if (status === 409) {
      // An unrecognized 409 shape at this status — most likely select-plot's
      // plain {"code":"no_active_cycle"} (the plot's active cycle closed
      // since this draft was captured, with none reopened yet). Closest of
      // the 4 blocked statuses: blocked_cycle_changed — same user action
      // either way (review, then delete or wait for a new cycle to open).
      await mark('blocked_cycle_changed', code ?? 'no_active_cycle');
      return { kind: 'blocked' };
    }
    // An unrecognized 422 — never guess a status for it; stop the batch
    // rather than silently skip past an error we can't classify.
    await mark(draft.status, 'unknown_error');
    return { kind: 'stopped', reason: 'unknown_error' };
  }

  if (status === 429) {
    await mark(draft.status, 'rate_limited');
    return { kind: 'stopped', reason: 'rate_limited' };
  }

  if (status >= 500) {
    await mark(draft.status, 'server_error');
    return { kind: 'stopped', reason: 'server_error' };
  }

  // Any other/unrecognized 4xx (400/403/410/451/...) — round 8-4C Part F:
  // "อย่าเดาว่า success, เก็บ draft, แสดง generic safe error". Chosen
  // behavior (documented in the round's Final Report): STOP the whole batch
  // rather than silently continuing past an error we can't classify — the
  // draft stays 'pending' with a diagnostic lastErrorCode for support.
  await mark(draft.status, 'unknown_error');
  return { kind: 'stopped', reason: 'unknown_error' };
}
