/**
 * OfflineInspectionQueuePanel — queued offline drafts (round 8-4B: read-only
 * list/delete/clear). Round 8-4C adds the re-authenticate + sequential-sync
 * flow: "ส่งรายการรอส่ง" -> enter the access number again (a fresh
 * authorization gate, never trusting the locally-cached plotId alone) ->
 * summary of what will/won't be sent -> confirm -> sequential send via
 * lib/offline-inspection-sync.ts.
 *
 * The raw access number is a transient input value ONLY — cleared from state
 * the instant the lookup succeeds, and the resulting phoneAccessSessionToken
 * never leaves this component's memory (never localStorage/sessionStorage/
 * IndexedDB), matching PublicInspect.tsx's own phone-session discipline.
 */
import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, RefreshCw, Send, Trash2, X } from 'lucide-react';
import {
  listOfflineInspectionDrafts,
  deleteOfflineInspectionDraft,
  clearAllOfflineInspectionDrafts,
  resetOfflineInspectionDraftForRetry,
  type OfflineInspectionDraftV2,
  type OfflineInspectionDraftStatus,
} from '../../lib/offline-inspection-store';
import { syncOfflineDrafts, type SyncProgress, type SyncSummary } from '../../lib/offline-inspection-sync';
import { lookupPublicInspectionAccess } from '../../api/publicInspectionAccess';
import { normalizeThaiMobile } from '../../lib/phone';
import { describeDraftErrorCode } from '../../lib/offline-submission-errors';

function formatThaiDateTime(iso: string): string {
  return new Date(iso).toLocaleString('th-TH', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function cycleName(draft: OfflineInspectionDraftV2): string {
  return draft.cycleLabel?.trim() || `รอบที่ ${draft.cycleNo}`;
}

const STATUS_BADGES: Record<OfflineInspectionDraftStatus, { label: string; className: string }> = {
  pending: { label: 'รอส่ง', className: 'bg-amber-50 text-amber-700' },
  blocked_cycle_changed: { label: 'รอบปลูกเปลี่ยน', className: 'bg-orange-50 text-orange-700' },
  blocked_access: { label: 'ไม่มีสิทธิ์เข้าถึง', className: 'bg-red-50 text-red-700' },
  blocked_conflict: { label: 'ข้อมูลซ้ำขัดแย้ง', className: 'bg-red-50 text-red-700' },
  blocked_expired: { label: 'รายการหมดอายุ', className: 'bg-gray-100 text-gray-600' },
};

/** Round 8-4C.2 Part A — ONLY blocked_access is recoverable by a plain retry
 * (an admin may have reopened the assignment, and the draft's active cycle
 * hasn't changed). blocked_cycle_changed can NEVER become sendable again by
 * flipping its status back: the sync engine always resends a draft's
 * ORIGINAL capturedPlotCycleId unchanged (lib/offline-inspection-sync.ts),
 * and the backend fail-closed rejects any capturedPlotCycleId that no longer
 * matches the plot's current active cycle with 409 planting_cycle_changed
 * (app/api/v1/public_records.py) — there is no code path that ever lets this
 * draft succeed again as-is. blocked_conflict/blocked_expired are likewise
 * never recoverable by a status flip. All three require the user to record
 * a fresh inspection and delete the stale draft. */
const RETRYABLE_STATUSES: ReadonlySet<OfflineInspectionDraftStatus> = new Set(['blocked_access']);

function StatusBadge({ status }: { status: OfflineInspectionDraftStatus }) {
  const badge = STATUS_BADGES[status];
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}>
      {badge.label}
    </span>
  );
}

const inputCls = 'w-full rounded-md border border-gray-300 px-3 py-3 text-base shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500';
const primaryBtnCls = 'flex w-full items-center justify-center gap-2 rounded-md bg-green-600 px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-green-700 disabled:opacity-50';
const secondaryBtnCls = 'w-full rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50';

interface Props {
  onClose: () => void;
  /** Called after any mutation (delete/clear/sync) so the caller can refresh
   * its own pending-count badge without this panel needing to own that state. */
  onQueueChanged: () => void;
  /** Round 8-4C — "ส่งรายการรอส่ง" only ever shows while online; never
   * auto-triggered by an 'online' browser event, only by this explicit
   * button (see lib/offline-inspection-sync.ts docstring). */
  isOnline: boolean;
}

/** The re-auth + sync mini flow's own step, independent of the list view
 * above it. 'idle' is the normal list-only view. */
type ReauthStep = 'idle' | 'entering-number' | 'summary' | 'syncing' | 'result';

export function OfflineInspectionQueuePanel({ onClose, onQueueChanged, isOnline }: Props) {
  const [drafts, setDrafts] = useState<OfflineInspectionDraftV2[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const [reauthStep, setReauthStep] = useState<ReauthStep>('idle');
  const [accessNumberInput, setAccessNumberInput] = useState('');
  const [reauthLoading, setReauthLoading] = useState(false);
  const [reauthError, setReauthError] = useState('');
  const [phoneAccessSessionToken, setPhoneAccessSessionToken] = useState<string | null>(null);
  const [authorizedPlotIds, setAuthorizedPlotIds] = useState<Set<string> | null>(null);
  const [syncProgress, setSyncProgress] = useState<SyncProgress | null>(null);
  const [syncSummary, setSyncSummary] = useState<SyncSummary | null>(null);
  // Round 8-4C.1 Part C — a LOCAL failure (IndexedDB error surfacing out of
  // syncOfflineDrafts itself, as opposed to a classified HTTP outcome the
  // engine already turned into a SyncSummary) — mutually exclusive with
  // syncSummary; the 'result' step is never entered with BOTH null.
  const [localSyncError, setLocalSyncError] = useState('');
  // Guards against a double-click starting two overlapping sync batches
  // before React re-renders the disabled/step-changed button.
  const syncInFlightRef = useRef(false);

  async function load() {
    setLoading(true);
    setLoadError('');
    try {
      setDrafts(await listOfflineInspectionDrafts());
    } catch {
      setLoadError('ไม่สามารถโหลดรายการรอส่งได้');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function handleDelete(id: string) {
    if (!confirm('ลบรายการนี้ออกจากเครื่องหรือไม่? ข้อมูลนี้จะหายไปถาวรและไม่สามารถกู้คืนได้')) return;
    await deleteOfflineInspectionDraft(id);
    await load();
    onQueueChanged();
  }

  async function handleClearAll() {
    if (drafts.length === 0) return;
    if (!confirm(`ลบรายการรอส่งทั้งหมด ${drafts.length} รายการออกจากเครื่องหรือไม่? ข้อมูลนี้จะหายไปถาวรและไม่สามารถกู้คืนได้`)) return;
    await clearAllOfflineInspectionDrafts();
    await load();
    onQueueChanged();
  }

  /** Round 8-4C.1 Part B — explicit, confirmed retry for a recoverable
   * blocked draft. Never re-sends by itself: the draft goes back to
   * 'pending' only, the user still has to press "ส่งรายการรอส่ง" and
   * re-authenticate through the normal flow afterwards. */
  async function handleRetry(id: string) {
    if (!confirm('ลองส่งรายการนี้อีกครั้งหรือไม่? รายการจะกลับไปเป็น "รอส่ง" — กรุณากด "ส่งรายการรอส่ง" อีกครั้งเพื่อส่งจริง')) return;
    await resetOfflineInspectionDraftForRetry(id);
    await load();
    onQueueChanged();
  }

  const pendingDrafts = drafts.filter((d) => d.status === 'pending');
  const blockedDrafts = drafts.filter((d) => d.status !== 'pending');

  function startReauth() {
    setReauthError('');
    setAccessNumberInput('');
    setReauthStep('entering-number');
  }

  function cancelReauthFlow() {
    setPhoneAccessSessionToken(null);
    setAuthorizedPlotIds(null);
    setAccessNumberInput('');
    setReauthError('');
    setSyncProgress(null);
    setSyncSummary(null);
    setLocalSyncError('');
    setReauthStep('idle');
  }

  async function handleReauthSubmit(e: React.FormEvent) {
    e.preventDefault();
    let normalized: string;
    try {
      normalized = normalizeThaiMobile(accessNumberInput);
    } catch {
      // Never surface normalizeThaiMobile's own message — same neutral
      // "หมายเลขสำหรับเข้าตรวจ" copy as the main phone step, never "เบอร์โทร".
      setReauthError('หมายเลขไม่ถูกต้อง กรุณาตรวจสอบตัวเลข 10 หลัก');
      return;
    }
    setReauthLoading(true);
    setReauthError('');
    try {
      // Round 8-9D — object parameter. This LEFTOVER-draft re-auth panel is a
      // phone-only path by design: it never collects a plot password, so with
      // enforcement on it simply fails with the normal generic error and the
      // user syncs from the main flow instead. Reviving it for passwords would
      // mean holding a secret in a second component (see the round brief's
      // "offline feature revival" prohibition).
      const result = await lookupPublicInspectionAccess({ phone: normalized });
      // The raw number is never needed again past this point.
      setAccessNumberInput('');
      setPhoneAccessSessionToken(result.phoneAccessSessionToken);
      setAuthorizedPlotIds(new Set(result.plots.map((p) => p.plotId)));
      setReauthStep('summary');
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      if (status === 404) setReauthError('ไม่พบแปลงที่หมายเลขนี้ได้รับอนุญาตให้เข้าตรวจ');
      else if (status === 429) setReauthError('มีการลองหลายครั้งเกินไป กรุณารอสักครู่แล้วลองใหม่');
      else if (axios.isAxiosError(err) && !err.response) setReauthError('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่');
      else setReauthError('ไม่สามารถตรวจสอบสิทธิ์ได้ กรุณาลองใหม่');
    } finally {
      setReauthLoading(false);
    }
  }

  /** Round 8-4C.1 Part A/C. A pending draft whose plot is NOT in this
   * authorization is simply left OUT of the batch — never marked
   * blocked_access just because THIS number's lookup didn't cover it (it may
   * belong to a different access number entirely, which the user can
   * re-auth with later). syncOfflineDrafts itself only ever attempts drafts
   * inside `authorizedPlotIds`, so no local status write happens here before
   * the batch starts at all.
   *
   * Part C (hardened round 8-4C.2): wrapped so a genuine LOCAL failure (an
   * IndexedDB error surfacing out of the sync engine, as opposed to a
   * classified HTTP outcome it already turned into a SyncSummary) can never
   * leave the panel on reauthStep='result' with nothing to show — every exit
   * path here sets either syncSummary or localSyncError before advancing.
   * The evidence-gathering read inside the catch block has its OWN nested
   * try/catch: if IndexedDB has failed so completely that even THAT read
   * throws, localSyncError still gets the generic message (never a guessed
   * "sent" count with zero evidence). The `finally` block below is safe by
   * construction — `load()` and the caller's `onQueueChanged` both already
   * swallow their own errors internally, so nothing here can throw and skip
   * past setReauthStep('result'). The phoneAccessSessionToken is cleared
   * unconditionally once the batch ends (success, an early stop, OR an
   * error) — a finished attempt always requires a fresh re-auth for the next
   * one, no exceptions. */
  async function handleConfirmSync() {
    if (syncInFlightRef.current) return;
    const token = phoneAccessSessionToken;
    if (!token || !authorizedPlotIds) return;
    syncInFlightRef.current = true;
    setReauthStep('syncing');
    setSyncProgress(null);
    setSyncSummary(null);
    setLocalSyncError('');
    // Snapshot which drafts we're ABOUT to attempt, before the batch can
    // mutate/delete any of them — used only if we have to fall back to an
    // evidence-based count after a local failure below.
    const attemptedIds = new Set(
      pendingDrafts.filter((d) => authorizedPlotIds.has(d.plotId)).map((d) => d.clientSubmissionId),
    );
    try {
      const summary = await syncOfflineDrafts(token, authorizedPlotIds, setSyncProgress);
      setSyncSummary(summary);
    } catch {
      // A local storage failure interrupted the batch — never claim success,
      // never leave the screen blank. Some drafts may already have been sent
      // (their rows deleted) before the failure; reload and count only what
      // we can actually verify is gone, never a guess.
      let localMessage = 'ไม่สามารถอัปเดตรายการในเครื่องได้ กรุณาปิดหน้าต่างนี้แล้วลองใหม่';
      try {
        const stillThere = new Set((await listOfflineInspectionDrafts()).map((d) => d.clientSubmissionId));
        const confirmedSent = [...attemptedIds].filter((id) => !stillThere.has(id)).length;
        if (confirmedSent > 0) {
          localMessage = `ส่งสำเร็จ ${confirmedSent} รายการก่อนเกิดปัญหา — ไม่สามารถอัปเดตรายการในเครื่องได้ กรุณาปิดหน้าต่างนี้แล้วลองใหม่`;
        }
      } catch {
        // Reading the evidence itself ALSO failed (total local storage
        // failure) — never guess a "sent" count with zero evidence; the
        // generic message set above already covers this, and the outer
        // finally below still guarantees the result step is reached with a
        // non-empty localSyncError, never blank.
      }
      setLocalSyncError(localMessage);
    } finally {
      syncInFlightRef.current = false;
      setPhoneAccessSessionToken(null);
      setAuthorizedPlotIds(null);
      setReauthStep('result');
      await load();
      onQueueChanged();
    }
  }

  const readyCount = authorizedPlotIds
    ? pendingDrafts.filter((d) => authorizedPlotIds.has(d.plotId)).length
    : 0;
  const notAuthorizedCount = authorizedPlotIds ? pendingDrafts.length - readyCount : 0;
  const closeDisabled = reauthStep === 'syncing';

  function finishSyncAndClose() {
    cancelReauthFlow();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
      <div className="flex max-h-[85vh] w-full max-w-md flex-col rounded-t-xl bg-white shadow-xl sm:rounded-xl">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <h2 className="text-base font-semibold text-gray-900">รายการรอส่ง</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={closeDisabled}
            aria-label="ปิด"
            className="text-gray-400 hover:text-gray-600 disabled:opacity-30"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {reauthStep === 'idle' && (
          <>
            <div className="flex-1 overflow-y-auto p-4">
              {/* Round 8-4H.1 — offline inspection creation is disabled; every
                  draft this panel can ever show is a LEFTOVER saved before
                  that change. This note is the only place that context is
                  explained (the top-level badge just says a plain count). */}
              {!loading && !loadError && drafts.length > 0 && (
                <p className="mb-3 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">
                  รายการนี้ถูกบันทึกไว้ก่อนปิดการใช้งาน Offline กรุณาเชื่อมต่ออินเทอร์เน็ตเพื่อส่งข้อมูล
                </p>
              )}
              {loading && (
                <p className="flex items-center justify-center gap-2 py-8 text-sm text-gray-400">
                  <Loader2 className="h-4 w-4 animate-spin" /> กำลังโหลด...
                </p>
              )}
              {loadError && <p role="alert" className="text-sm text-red-600">{loadError}</p>}
              {!loading && !loadError && drafts.length === 0 && (
                <p className="py-8 text-center text-sm text-gray-400">ไม่มีรายการรอส่ง</p>
              )}
              <ul className="space-y-3">
                {drafts.map((d) => (
                  <li key={d.clientSubmissionId} className="rounded-lg border border-gray-200 p-3 shadow-sm">
                    <div className="flex items-start justify-between gap-2">
                      <button
                        type="button"
                        onClick={() => setExpandedId((cur) => (cur === d.clientSubmissionId ? null : d.clientSubmissionId))}
                        className="min-w-0 flex-1 text-left"
                        aria-expanded={expandedId === d.clientSubmissionId}
                      >
                        <p className="truncate font-semibold text-gray-900">{d.plotCode} — {d.plotName}</p>
                        <p className="truncate text-xs text-gray-500">
                          {cycleName(d)}
                          {d.crop ? ` · ${d.crop}` : ''}{d.variety ? ` (${d.variety})` : ''}
                        </p>
                        {d.lotNo && <p className="text-xs text-gray-500">Lot: {d.lotNo}</p>}
                        <p className="mt-1 text-xs text-gray-400">
                          เก็บเมื่อ {formatThaiDateTime(d.capturedAt)} · {d.photos.length} รูป
                        </p>
                        {d.lastAttemptAt && (
                          <p className="text-xs text-gray-400">
                            ส่งล่าสุด {formatThaiDateTime(d.lastAttemptAt)}
                          </p>
                        )}
                        {d.status === 'blocked_cycle_changed' ? (
                          <p className="mt-1 text-xs text-orange-700">
                            รอบปลูกเปลี่ยนแล้ว รายการนี้ไม่สามารถส่งเข้ารอบใหม่ได้ กรุณาบันทึกการตรวจใหม่ในรอบปัจจุบัน
                            และลบรายการเดิมเมื่อไม่ต้องการแล้ว
                          </p>
                        ) : (
                          d.status !== 'pending' && d.lastErrorCode && (
                            <p className="mt-1 text-xs text-red-600">{describeDraftErrorCode(d.lastErrorCode)}</p>
                          )
                        )}
                      </button>
                      <StatusBadge status={d.status} />
                    </div>

                    {expandedId === d.clientSubmissionId && (
                      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-gray-100 pt-2 text-xs text-gray-500">
                        <div>
                          <dt className="text-gray-400">Supplier</dt>
                          <dd>{d.supplierCode} — {d.supplierName}</dd>
                        </div>
                        <div>
                          <dt className="text-gray-400">ระยะการเจริญเติบโต</dt>
                          <dd>{d.fields.growthStage || '—'}</dd>
                        </div>
                        <div>
                          <dt className="text-gray-400">ผลผลิต (Yield)</dt>
                          <dd>{d.fields.yieldPct}%</dd>
                        </div>
                        <div>
                          <dt className="text-gray-400">ผู้กรอกข้อมูล</dt>
                          <dd>{d.fields.submittedByName || '—'}</dd>
                        </div>
                      </dl>
                    )}

                    <div className="mt-2 flex items-center gap-3">
                      {RETRYABLE_STATUSES.has(d.status) && (
                        <button
                          type="button"
                          onClick={() => handleRetry(d.clientSubmissionId)}
                          className="inline-flex items-center gap-1 text-xs font-medium text-green-700 hover:underline"
                        >
                          <RefreshCw className="h-3.5 w-3.5" /> ลองส่งอีกครั้ง
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDelete(d.clientSubmissionId)}
                        className="inline-flex items-center gap-1 text-xs font-medium text-red-600 hover:underline"
                      >
                        <Trash2 className="h-3.5 w-3.5" /> ลบออกจากเครื่อง
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            {drafts.length > 0 && (
              <div className="space-y-2 border-t border-gray-200 p-4">
                {isOnline && pendingDrafts.length > 0 && (
                  <button type="button" onClick={startReauth} className={primaryBtnCls}>
                    <Send className="h-4 w-4" /> ส่งรายการรอส่ง ({pendingDrafts.length})
                  </button>
                )}
                <button type="button" onClick={handleClearAll} className={`${secondaryBtnCls} border-red-200 text-red-600 hover:bg-red-50`}>
                  ล้างรายการรอส่งทั้งหมด ({drafts.length})
                </button>
              </div>
            )}
          </>
        )}

        {reauthStep === 'entering-number' && (
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            <p className="text-sm text-gray-600">
              กรุณากรอกหมายเลขสำหรับเข้าตรวจอีกครั้งเพื่อยืนยันสิทธิ์ก่อนส่งรายการ
            </p>
            <form onSubmit={handleReauthSubmit} className="space-y-3">
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-gray-700">
                  หมายเลขสำหรับเข้าตรวจ<span className="ml-1 text-red-500">*</span>
                </span>
                <input
                  type="text" inputMode="numeric" autoComplete="off" autoFocus
                  value={accessNumberInput}
                  onChange={(e) => { setAccessNumberInput(e.target.value); setReauthError(''); }}
                  className={inputCls}
                  placeholder="กรอกหมายเลข 10 หลัก"
                />
              </label>
              {reauthError && <p role="alert" className="text-sm text-red-600">{reauthError}</p>}
              <button type="submit" disabled={reauthLoading} className={primaryBtnCls}>
                {reauthLoading && <Loader2 className="h-4 w-4 animate-spin" />} ยืนยันหมายเลข
              </button>
              <button type="button" onClick={cancelReauthFlow} className={secondaryBtnCls}>ยกเลิก</button>
            </form>
          </div>
        )}

        {reauthStep === 'summary' && (
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            <div className="space-y-2 rounded-md bg-gray-50 p-3 text-sm">
              <p className="text-green-700">พร้อมส่ง {readyCount} รายการ</p>
              {notAuthorizedCount > 0 && (
                <p className="text-amber-700">
                  ไม่อยู่ในสิทธิ์ของหมายเลขนี้ {notAuthorizedCount} รายการ — รายการยังเก็บอยู่ในเครื่อง
                </p>
              )}
              {blockedDrafts.length > 0 && (
                <p className="text-gray-500">ถูกบล็อกจากการส่งครั้งก่อน {blockedDrafts.length} รายการ</p>
              )}
            </div>
            {readyCount === 0 ? (
              <button type="button" onClick={startReauth} className={primaryBtnCls}>กรอกหมายเลขอื่น</button>
            ) : (
              <button type="button" onClick={handleConfirmSync} className={primaryBtnCls}>ยืนยันเริ่มส่ง</button>
            )}
            <button type="button" onClick={cancelReauthFlow} className={secondaryBtnCls}>ยกเลิก</button>
          </div>
        )}

        {reauthStep === 'syncing' && (
          <div className="flex-1 space-y-4 p-4 text-center">
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-green-600" />
            {syncProgress ? (
              <p className="text-sm text-gray-700">
                กำลังส่ง {syncProgress.current} จาก {syncProgress.total} — {syncProgress.plotCode} {syncProgress.plotName}
              </p>
            ) : (
              <p className="text-sm text-gray-700">กำลังเตรียมส่ง...</p>
            )}
            <p className="text-xs text-gray-400">กรุณาอย่าปิดหน้านี้จนกว่าจะส่งเสร็จ</p>
          </div>
        )}

        {reauthStep === 'result' && (syncSummary || localSyncError) && (
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {localSyncError ? (
              <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {localSyncError}
              </p>
            ) : syncSummary && (
              <div className="space-y-2 rounded-md bg-gray-50 p-3 text-sm">
                <p className="text-green-700">ส่งสำเร็จ {syncSummary.sentCount} รายการ</p>
                {syncSummary.blockedCount > 0 && (
                  <p className="text-red-600">ส่งไม่ได้ {syncSummary.blockedCount} รายการ (ดูรายละเอียดในรายการ)</p>
                )}
                {syncSummary.stopReason === 'unauthorized' && (
                  <p className="text-amber-700">เซสชันหมดอายุระหว่างส่ง กรุณากรอกหมายเลขใหม่เพื่อส่งรายการที่เหลือ</p>
                )}
                {syncSummary.stopReason === 'network' && (
                  <p className="text-amber-700">เชื่อมต่อเครือข่ายไม่สำเร็จระหว่างส่ง รายการที่เหลือยังอยู่ในเครื่อง</p>
                )}
                {(syncSummary.stopReason === 'rate_limited' || syncSummary.stopReason === 'server_error' || syncSummary.stopReason === 'unknown_error') && (
                  <p className="text-amber-700">หยุดส่งชั่วคราว รายการที่เหลือยังอยู่ในเครื่อง กรุณาลองใหม่ภายหลัง</p>
                )}
              </div>
            )}
            <button type="button" onClick={finishSyncAndClose} className={primaryBtnCls}>เสร็จสิ้น</button>
          </div>
        )}
      </div>
    </div>
  );
}
