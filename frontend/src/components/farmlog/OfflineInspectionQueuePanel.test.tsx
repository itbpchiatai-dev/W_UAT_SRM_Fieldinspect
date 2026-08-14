/**
 * OfflineInspectionQueuePanel — round 8-4B. Uses the real (fake-indexeddb
 * polyfilled) store module rather than mocking it, since the panel's whole
 * job is thin CRUD glue over that store — see src/test/setup.ts for the
 * global IndexedDB polyfill.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { IDBFactory } from 'fake-indexeddb';
import { OfflineInspectionQueuePanel } from './OfflineInspectionQueuePanel';
import * as offlineInspectionStore from '../../lib/offline-inspection-store';
import {
  buildOfflineInspectionDraft,
  closeOfflineInspectionDb,
  getOfflineInspectionDraft,
  putOfflineInspectionDraft,
  updateOfflineInspectionDraftStatus,
  type OfflineInspectionDraftV2,
} from '../../lib/offline-inspection-store';
import type { PublicInspectionFormFields } from '../../api/publicInspection';

const lookupMock = vi.fn();
vi.mock('../../api/publicInspectionAccess', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/publicInspectionAccess')>();
  return { ...actual, lookupPublicInspectionAccess: (...args: unknown[]) => lookupMock(...args) };
});

const syncMock = vi.fn();
vi.mock('../../lib/offline-inspection-sync', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/offline-inspection-sync')>();
  return { ...actual, syncOfflineDrafts: (...args: unknown[]) => syncMock(...args) };
});

const EMPTY_FIELDS: PublicInspectionFormFields = {
  submittedByName: '', growthStage: 'ออกดอก', yieldPct: 85, yieldQuantityKg: null, weatherCondition: '',
  fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
  recommendation: '', notes: '', latitude: null, longitude: null,
};

function draftFor(overrides: Partial<Parameters<typeof buildOfflineInspectionDraft>[0]> = {}): OfflineInspectionDraftV2 {
  return buildOfflineInspectionDraft({
    clientSubmissionId: crypto.randomUUID(),
    capturedAt: '2026-07-15T09:00:00.000Z',
    capturedPlotCycleId: 'cycle-1',
    recordDate: '2026-07-15',
    plotId: 'plot-1', plotCode: 'PLOT001', plotName: 'Plot One',
    supplierId: 'sup-1', supplierCode: 'SUP001', supplierName: 'Supplier One',
    cycleNo: 2, cycleLabel: 'jun2026',
    crop: 'พริก', variety: 'พริกขี้หนู', lotNo: 'LOT-01', plantingDate: '2026-01-01',
    inspectorType: 'farmer',
    fields: { ...EMPTY_FIELDS, submittedByName: 'สมชาย' },
    photos: [],
    now: '2026-07-15T09:00:00.000Z',
    ...overrides,
  });
}

beforeEach(() => {
  closeOfflineInspectionDb();
  (globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory();
  lookupMock.mockReset();
  syncMock.mockReset();
});

afterEach(() => {
  closeOfflineInspectionDb();
});

describe('OfflineInspectionQueuePanel — list', () => {
  it('shows an empty state when there are no drafts', async () => {
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    expect(await screen.findByText('ไม่มีรายการรอส่ง')).toBeTruthy();
  });

  it('lists a draft with plotCode/plotName, cycle, crop/variety, lot, captured time, photo count, and a "รอส่ง" badge', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);

    expect(await screen.findByText('PLOT001 — Plot One')).toBeTruthy();
    expect(screen.getByText(/jun2026/)).toBeTruthy();
    expect(screen.getByText(/พริก \(พริกขี้หนู\)/)).toBeTruthy();
    expect(screen.getByText('Lot: LOT-01')).toBeTruthy();
    expect(screen.getByText(/0 รูป/)).toBeTruthy();
    expect(screen.getByText('รอส่ง')).toBeTruthy();
  });

  it('falls back to "รอบที่ N" when the draft has no cycle label', async () => {
    await putOfflineInspectionDraft(draftFor({ cycleLabel: null, cycleNo: 5 }));
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    expect(await screen.findByText(/รอบที่ 5/)).toBeTruthy();
  });

  it('shows a read-only detail summary (growth stage, yield, submitted name) when a row is expanded', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    const row = await screen.findByText('PLOT001 — Plot One');

    fireEvent.click(row);

    expect(await screen.findByText('ออกดอก')).toBeTruthy();
    expect(screen.getByText('85%')).toBeTruthy();
    expect(screen.getByText('สมชาย')).toBeTruthy();
    // No fake "send" action anywhere on this panel this round.
    expect(screen.queryByRole('button', { name: /^ส่ง$/ })).toBeNull();
  });

  it('closes via the X button', async () => {
    const onClose = vi.fn();
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={onClose} onQueueChanged={() => {}} />);
    await screen.findByText('ไม่มีรายการรอส่ง');
    fireEvent.click(screen.getByLabelText('ปิด'));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('shows the "saved before offline was disabled" note when a draft exists (round 8-4H.1)', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    expect(await screen.findByText(
      'รายการนี้ถูกบันทึกไว้ก่อนปิดการใช้งาน Offline กรุณาเชื่อมต่ออินเทอร์เน็ตเพื่อส่งข้อมูล',
    )).toBeTruthy();
  });

  it('never shows the "saved before offline was disabled" note when the queue is empty (round 8-4H.1)', async () => {
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('ไม่มีรายการรอส่ง');
    expect(screen.queryByText(/ถูกบันทึกไว้ก่อนปิดการใช้งาน Offline/)).toBeNull();
  });
});

describe('OfflineInspectionQueuePanel — delete one', () => {
  it('deletes a draft after confirm, refreshes the list, and notifies the parent', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onQueueChanged = vi.fn();
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={onQueueChanged} />);
    await screen.findByText('PLOT001 — Plot One');

    fireEvent.click(screen.getByText('ลบออกจากเครื่อง'));

    await waitFor(() => expect(screen.queryByText('PLOT001 — Plot One')).toBeNull());
    expect(await screen.findByText('ไม่มีรายการรอส่ง')).toBeTruthy();
    expect(onQueueChanged).toHaveBeenCalledOnce();
  });

  it('cancelling the confirm keeps the draft', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const onQueueChanged = vi.fn();
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={onQueueChanged} />);
    await screen.findByText('PLOT001 — Plot One');

    fireEvent.click(screen.getByText('ลบออกจากเครื่อง'));

    expect(screen.getByText('PLOT001 — Plot One')).toBeTruthy();
    expect(onQueueChanged).not.toHaveBeenCalled();
  });
});

describe('OfflineInspectionQueuePanel — clear all', () => {
  it('clears every draft after confirm, mentioning the count', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onQueueChanged = vi.fn();
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'a' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'b', plotCode: 'PLOT002' }));
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={onQueueChanged} />);
    await screen.findByText('ล้างรายการรอส่งทั้งหมด (2)');

    fireEvent.click(screen.getByText('ล้างรายการรอส่งทั้งหมด (2)'));

    expect(confirmSpy.mock.calls[0][0]).toContain('2');
    await waitFor(() => expect(screen.queryByText('PLOT001 — Plot One')).toBeNull());
    expect(await screen.findByText('ไม่มีรายการรอส่ง')).toBeTruthy();
    expect(onQueueChanged).toHaveBeenCalledOnce();
  });

  it('does not render the clear-all button when the queue is empty', async () => {
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('ไม่มีรายการรอส่ง');
    expect(screen.queryByText(/ล้างรายการรอส่งทั้งหมด/)).toBeNull();
  });
});

// --- round 8-4C: status badges (Part G) -------------------------------------

describe('OfflineInspectionQueuePanel — status badges', () => {
  it.each([
    ['pending', 'รอส่ง'],
    ['blocked_cycle_changed', 'รอบปลูกเปลี่ยน'],
    ['blocked_access', 'ไม่มีสิทธิ์เข้าถึง'],
    ['blocked_conflict', 'ข้อมูลซ้ำขัดแย้ง'],
    ['blocked_expired', 'รายการหมดอายุ'],
  ] as const)('shows the "%s" badge as "%s"', async (status, label) => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'badge-draft' }));
    if (status !== 'pending') {
      await updateOfflineInspectionDraftStatus('badge-draft', status);
    }
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    expect(await screen.findByText(label)).toBeTruthy();
  });

  it('a blocked draft (other than blocked_cycle_changed) shows a safe Thai error description, never a raw error object', async () => {
    const draft = draftFor({ clientSubmissionId: 'blocked-1' });
    await putOfflineInspectionDraft(draft);
    await updateOfflineInspectionDraftStatus('blocked-1', 'blocked_conflict', { lastErrorCode: 'idempotency_conflict' });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);

    expect(await screen.findByText('ข้อมูลซ้ำขัดแย้ง')).toBeTruthy();
    expect(await screen.findByText('รหัสรายการนี้ถูกใช้กับข้อมูลอื่นแล้ว กรุณาเก็บรายการใหม่')).toBeTruthy();
  });

  it('never shows the access number, a token, a qrKey, or GPS coordinates anywhere in the panel', async () => {
    const draft = draftFor({ clientSubmissionId: 'safe-1', fields: { ...draftFor().fields, latitude: 13.75, longitude: 100.5 } });
    await putOfflineInspectionDraft(draft);
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    fireEvent.click(screen.getByText('PLOT001 — Plot One'));

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toMatch(/13\.75/);
    expect(bodyText).not.toMatch(/100\.5/);
    expect(bodyText).not.toMatch(/token/i);
    expect(bodyText).not.toMatch(/qrKey/i);
  });
});

// --- round 8-4C: re-authentication + sync trigger visibility (Part D) ------

describe('OfflineInspectionQueuePanel — send button visibility', () => {
  it('shows "ส่งรายการรอส่ง" when online and at least one pending draft exists', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    expect(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ })).toBeTruthy();
  });

  it('hides the send button while offline, even with pending drafts', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={false} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    expect(screen.queryByRole('button', { name: /ส่งรายการรอส่ง/ })).toBeNull();
  });

  it('hides the send button when every draft is already blocked (nothing pending)', async () => {
    const draft = draftFor({ clientSubmissionId: 'only-blocked' });
    await putOfflineInspectionDraft(draft);
    await updateOfflineInspectionDraftStatus('only-blocked', 'blocked_expired', { lastErrorCode: 'offline_draft_expired' });
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    expect(screen.queryByRole('button', { name: /ส่งรายการรอส่ง/ })).toBeNull();
  });

  it('never auto-syncs — no lookup/sync call happens just from mounting online with pending drafts', async () => {
    await putOfflineInspectionDraft(draftFor());
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ });
    expect(lookupMock).not.toHaveBeenCalled();
    expect(syncMock).not.toHaveBeenCalled();
  });

  it('reconnecting (isOnline flips false -> true) never auto-syncs by itself — only reveals the send button', async () => {
    await putOfflineInspectionDraft(draftFor());
    const { rerender } = render(
      <OfflineInspectionQueuePanel isOnline={false} onClose={() => {}} onQueueChanged={() => {}} />,
    );
    await screen.findByText('PLOT001 — Plot One');
    expect(screen.queryByRole('button', { name: /ส่งรายการรอส่ง/ })).toBeNull();

    rerender(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);

    await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ });
    expect(lookupMock).not.toHaveBeenCalled();
    expect(syncMock).not.toHaveBeenCalled();
  });
});

// --- round 8-4C: re-auth -> summary -> confirm -> sync flow (Part D/E) -----

describe('OfflineInspectionQueuePanel — re-auth + sync flow', () => {
  async function openReauthStep() {
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ }));
    await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก');
  }

  it('never shows "เบอร์โทร" in the re-auth number step copy', async () => {
    await putOfflineInspectionDraft(draftFor());
    await openReauthStep();
    expect(document.body.textContent).not.toMatch(/เบอร์โทร/);
  });

  it('an invalid number shows a Thai error and never calls lookup', async () => {
    await putOfflineInspectionDraft(draftFor());
    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '123' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    expect(await screen.findByText(/หมายเลขไม่ถูกต้อง/)).toBeTruthy();
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it('a valid number authorized for this draft\'s plot shows "พร้อมส่ง 1 รายการ" in the summary, and clears the raw number from the DOM', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));

    expect(await screen.findByText('พร้อมส่ง 1 รายการ')).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/0845552162/);
  });

  it('a plot NOT in the re-authenticated list is counted as "not authorized this number" in the summary (round 8-4C.1 Part A)', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'other-number-1', plotId: 'plot-other' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }], // different plot — 'plot-other' belongs to a different number
    });
    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));

    expect(await screen.findByText('พร้อมส่ง 0 รายการ')).toBeTruthy();
    expect(await screen.findByText('ไม่อยู่ในสิทธิ์ของหมายเลขนี้ 1 รายการ — รายการยังเก็บอยู่ในเครื่อง')).toBeTruthy();
  });

  it('readyCount=0 replaces the confirm button with a clear "กรอกหมายเลขอื่น" path back to the number step', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'other-number-1', plotId: 'plot-other' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    await screen.findByText('พร้อมส่ง 0 รายการ');

    expect(screen.queryByRole('button', { name: 'ยืนยันเริ่มส่ง' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'กรอกหมายเลขอื่น' }));

    await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก');
  });

  it('a draft not covered by this number stays pending — never permanently blocked — and can be sent after re-auth with the correct number', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'number-b-1', plotId: 'plot-other' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    syncMock.mockResolvedValue({ totalAttempted: 1, sentCount: 1, blockedCount: 0, stopReason: null });

    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));

    await waitFor(() => expect(syncMock).toHaveBeenCalledTimes(1));
    expect(syncMock.mock.calls[0][0]).toBe('phone-tok-xyz');
    expect(syncMock.mock.calls[0][1]).toEqual(new Set(['plot-1']));

    expect(await screen.findByText('ส่งสำเร็จ 1 รายการ')).toBeTruthy();

    // Back to the list: the other-number draft is STILL 'pending' — never
    // touched, never marked blocked_access just because this lookup didn't
    // cover it.
    fireEvent.click(screen.getByRole('button', { name: 'เสร็จสิ้น' }));
    expect(screen.queryByText('ไม่มีสิทธิ์เข้าถึง')).toBeNull();
    const stillPending = await getOfflineInspectionDraft('number-b-1');
    expect(stillPending?.status).toBe('pending');
    expect(stillPending?.clientSubmissionId).toBe('number-b-1');
    expect(stillPending?.photos).toEqual(draftFor().photos);
  });

  it('a double-click on confirm starts exactly one sync batch', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    let resolveSync: (v: unknown) => void = () => {};
    syncMock.mockReturnValue(new Promise((resolve) => { resolveSync = resolve; }));

    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    const confirmBtn = await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' });
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(syncMock).toHaveBeenCalledTimes(1));
    resolveSync({ totalAttempted: 1, sentCount: 1, blockedCount: 0, stopReason: null });
    await screen.findByText('ส่งสำเร็จ 1 รายการ');
  });

  it('the close button is disabled while syncing', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    syncMock.mockReturnValue(new Promise(() => {})); // never resolves — stay in 'syncing'

    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));

    await screen.findByText('กรุณาอย่าปิดหน้านี้จนกว่าจะส่งเสร็จ');
    expect((screen.getByLabelText('ปิด') as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows "กำลังส่ง X จาก Y" progress reported by the sync engine', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    syncMock.mockImplementation(async (_token: string, _plots: Set<string>, onProgress: (p: unknown) => void) => {
      onProgress({ current: 1, total: 2, plotCode: 'PLOT001', plotName: 'Plot One' });
      return { totalAttempted: 1, sentCount: 1, blockedCount: 0, stopReason: null };
    });

    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));

    expect(await screen.findByText('กำลังส่ง 1 จาก 2 — PLOT001 Plot One')).toBeTruthy();
  });

  it('a 401 mid-batch clears the token and reports the session expired', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'ready-1', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    syncMock.mockResolvedValue({ totalAttempted: 1, sentCount: 0, blockedCount: 0, stopReason: 'unauthorized' });

    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));

    expect(await screen.findByText(/เซสชันหมดอายุระหว่างส่ง/)).toBeTruthy();
  });

  it('cancelling at the number step clears the input and returns to the list', async () => {
    await putOfflineInspectionDraft(draftFor());
    await openReauthStep();
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));
    expect(await screen.findByText('PLOT001 — Plot One')).toBeTruthy();
  });
});

// --- round 8-4C.1/8-4C.2 Part B: explicit retry for recoverable drafts -----

describe('OfflineInspectionQueuePanel — explicit retry (round 8-4C.2 Part A)', () => {
  it.each([
    ['blocked_access', true],
    // Round 8-4C.2: blocked_cycle_changed is NEVER retryable — the sync
    // engine always resends the draft's ORIGINAL capturedPlotCycleId, and
    // the backend fail-closed rejects it again with 409
    // planting_cycle_changed every single time. A plain status flip can
    // never make this draft succeed.
    ['blocked_cycle_changed', false],
    ['blocked_conflict', false],
    ['blocked_expired', false],
    ['pending', false],
  ] as const)('shows "ลองส่งอีกครั้ง" for %s: %s', async (status, shouldShow) => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'retry-target' }));
    if (status !== 'pending') {
      await updateOfflineInspectionDraftStatus('retry-target', status, { lastErrorCode: 'x' });
    }
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');

    if (shouldShow) {
      expect(screen.getByRole('button', { name: /ลองส่งอีกครั้ง/ })).toBeTruthy();
    } else {
      expect(screen.queryByRole('button', { name: /ลองส่งอีกครั้ง/ })).toBeNull();
    }
  });

  it('retrying (confirmed) resets status to pending, clears lastErrorCode, keeps identity/fields/photos, and preserves lastAttemptAt for audit', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const draft = draftFor({ clientSubmissionId: 'recoverable-1' });
    await putOfflineInspectionDraft(draft);
    await updateOfflineInspectionDraftStatus('recoverable-1', 'blocked_access', { lastErrorCode: 'not_found' });
    const beforeRetry = await getOfflineInspectionDraft('recoverable-1');

    const onQueueChanged = vi.fn();
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={onQueueChanged} />);
    await screen.findByText('PLOT001 — Plot One');
    fireEvent.click(screen.getByRole('button', { name: /ลองส่งอีกครั้ง/ }));

    await waitFor(async () => expect((await getOfflineInspectionDraft('recoverable-1'))?.status).toBe('pending'));
    const afterRetry = await getOfflineInspectionDraft('recoverable-1');
    expect(afterRetry?.lastErrorCode).toBeNull();
    expect(afterRetry?.lastAttemptAt).toBe(beforeRetry?.lastAttemptAt); // audit trail kept
    expect(afterRetry?.clientSubmissionId).toBe('recoverable-1');
    expect(afterRetry?.capturedAt).toBe(draft.capturedAt);
    expect(afterRetry?.capturedPlotCycleId).toBe(draft.capturedPlotCycleId);
    expect(afterRetry?.fields).toEqual(draft.fields);
    expect(afterRetry?.photos).toEqual(draft.photos);
    expect(onQueueChanged).toHaveBeenCalled();
    // Badge now shows "รอส่ง" again.
    expect(await screen.findByText('รอส่ง')).toBeTruthy();
  });

  it('cancelling the retry confirm leaves a blocked_access draft blocked, unchanged', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'stay-blocked' }));
    await updateOfflineInspectionDraftStatus('stay-blocked', 'blocked_access', { lastErrorCode: 'not_found' });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    fireEvent.click(screen.getByRole('button', { name: /ลองส่งอีกครั้ง/ }));

    const draft = await getOfflineInspectionDraft('stay-blocked');
    expect(draft?.status).toBe('blocked_access');
    expect(draft?.lastErrorCode).toBe('not_found');
  });

  it('a blocked_cycle_changed row shows the actionable Thai guidance message, never the generic error description', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'cycle-changed-1' }));
    await updateOfflineInspectionDraftStatus('cycle-changed-1', 'blocked_cycle_changed', { lastErrorCode: 'planting_cycle_changed' });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);

    expect(await screen.findByText(
      'รอบปลูกเปลี่ยนแล้ว รายการนี้ไม่สามารถส่งเข้ารอบใหม่ได้ กรุณาบันทึกการตรวจใหม่ในรอบปัจจุบัน และลบรายการเดิมเมื่อไม่ต้องการแล้ว',
    )).toBeTruthy();
    // No retry button, but delete is still there and still confirms.
    expect(screen.queryByRole('button', { name: /ลองส่งอีกครั้ง/ })).toBeNull();
    expect(screen.getByRole('button', { name: /ลบออกจากเครื่อง/ })).toBeTruthy();
  });

  it('a blocked_cycle_changed row never calls lookup/sync — deleting it is the only action available', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'cycle-changed-2' }));
    await updateOfflineInspectionDraftStatus('cycle-changed-2', 'blocked_cycle_changed', { lastErrorCode: 'planting_cycle_changed' });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    fireEvent.click(screen.getByRole('button', { name: /ลบออกจากเครื่อง/ }));

    await waitFor(async () => expect(await getOfflineInspectionDraft('cycle-changed-2')).toBeNull());
    expect(lookupMock).not.toHaveBeenCalled();
    expect(syncMock).not.toHaveBeenCalled();
  });

  it('retrying never sends anything by itself — no lookup/sync call happens', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'no-auto-send' }));
    await updateOfflineInspectionDraftStatus('no-auto-send', 'blocked_access', { lastErrorCode: 'not_found' });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    await screen.findByText('PLOT001 — Plot One');
    fireEvent.click(screen.getByRole('button', { name: /ลองส่งอีกครั้ง/ }));

    await waitFor(async () => expect((await getOfflineInspectionDraft('no-auto-send'))?.status).toBe('pending'));
    expect(lookupMock).not.toHaveBeenCalled();
    expect(syncMock).not.toHaveBeenCalled();
  });
});

// --- round 8-4C.1 Part C: local failure never leaves a blank result screen -

describe('OfflineInspectionQueuePanel — local failure handling (round 8-4C.1 Part C)', () => {
  async function reachSummaryAndConfirm() {
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ }));
    await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก');
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
    fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));
  }

  it('syncOfflineDrafts throwing shows a clear local-error message, never a blank result screen', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x1', plotId: 'plot-1' }));
    syncMock.mockRejectedValue(new Error('IndexedDB write failed'));

    await reachSummaryAndConfirm();

    expect(await screen.findByText('ไม่สามารถอัปเดตรายการในเครื่องได้ กรุณาปิดหน้าต่างนี้แล้วลองใหม่')).toBeTruthy();
    // The result step always shows SOMETHING plus a way forward.
    expect(screen.getByRole('button', { name: 'เสร็จสิ้น' })).toBeTruthy();
  });

  it('the draft that was never actually sent is still there after a local failure', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x2', plotId: 'plot-1' }));
    syncMock.mockRejectedValue(new Error('boom'));

    await reachSummaryAndConfirm();
    await screen.findByText(/ไม่สามารถอัปเดตรายการในเครื่องได้/);

    expect(await getOfflineInspectionDraft('x2')).not.toBeNull();
  });

  it('reports an evidence-based sent count when some drafts were confirmed sent before the failure', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'sent-before-fail', plotId: 'plot-1' }));
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'never-attempted', plotId: 'plot-1' }));
    syncMock.mockImplementation(async () => {
      // Simulate the engine having actually deleted ONE draft (a real send)
      // before hitting a local failure on the next one.
      const { deleteOfflineInspectionDraft } = await import('../../lib/offline-inspection-store');
      await deleteOfflineInspectionDraft('sent-before-fail');
      throw new Error('local failure after one real send');
    });

    await reachSummaryAndConfirm();

    expect(await screen.findByText(/ส่งสำเร็จ 1 รายการก่อนเกิดปัญหา/)).toBeTruthy();
    expect(await getOfflineInspectionDraft('sent-before-fail')).toBeNull();
    expect(await getOfflineInspectionDraft('never-attempted')).not.toBeNull();
  });

  it('clears the phoneAccessSessionToken and re-enables the close button after a local failure', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x3', plotId: 'plot-1' }));
    syncMock.mockRejectedValue(new Error('boom'));

    await reachSummaryAndConfirm();
    await screen.findByText(/ไม่สามารถอัปเดตรายการในเครื่องได้/);

    expect((screen.getByLabelText('ปิด') as HTMLButtonElement).disabled).toBe(false);
  });

  it('after a local failure, pressing "เสร็จสิ้น" and "ส่งรายการรอส่ง" again requires re-entering the number (token was cleared)', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x4', plotId: 'plot-1' }));
    syncMock.mockRejectedValueOnce(new Error('boom'));

    await reachSummaryAndConfirm();
    await screen.findByText(/ไม่สามารถอัปเดตรายการในเครื่องได้/);
    fireEvent.click(screen.getByRole('button', { name: 'เสร็จสิ้น' }));

    fireEvent.click(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ }));
    expect(await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก')).toBeTruthy();
  });

  it('syncInFlightRef is released after a local failure — the SAME panel instance can start a second sync attempt', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x5', plotId: 'plot-1' }));
    syncMock.mockRejectedValueOnce(new Error('boom'));
    syncMock.mockResolvedValueOnce({ totalAttempted: 1, sentCount: 1, blockedCount: 0, stopReason: null });
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);

    async function sendOnce() {
      fireEvent.click(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ }));
      await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก');
      fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
      fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));
      fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));
    }

    await sendOnce();
    await screen.findByText(/ไม่สามารถอัปเดตรายการในเครื่องได้/);
    fireEvent.click(screen.getByRole('button', { name: 'เสร็จสิ้น' }));

    await sendOnce();

    expect(await screen.findByText('ส่งสำเร็จ 1 รายการ')).toBeTruthy();
    expect(syncMock).toHaveBeenCalledTimes(2);
  });
});

// --- round 8-4C.2 Part C: TOTAL local storage failure (nested read also fails)

describe('OfflineInspectionQueuePanel — total local storage failure (round 8-4C.2 Part C)', () => {
  it('shows the generic message (never a guessed "sent" count) when even the evidence-gathering read fails', async () => {
    await putOfflineInspectionDraft(draftFor({ clientSubmissionId: 'x6', plotId: 'plot-1' }));
    lookupMock.mockResolvedValue({
      phoneAccessSessionToken: 'phone-tok-xyz', expiresIn: 28800, qrMatchedPlotId: null,
      plots: [{ plotId: 'plot-1' }],
    });
    syncMock.mockRejectedValue(new Error('sync failed'));

    render(<OfflineInspectionQueuePanel isOnline={true} onClose={() => {}} onQueueChanged={() => {}} />);
    // Mount's own load() must succeed first (real store) — the total-failure
    // simulation only kicks in from here on, at the point of confirming sync,
    // matching a device whose storage degrades mid-session rather than one
    // that was already broken before the panel ever opened.
    fireEvent.click(await screen.findByRole('button', { name: /ส่งรายการรอส่ง/ }));
    await screen.findByPlaceholderText('กรอกหมายเลข 10 หลัก');
    fireEvent.change(screen.getByPlaceholderText('กรอกหมายเลข 10 หลัก'), { target: { value: '0845552162' } });
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันหมายเลข' }));

    const listSpy = vi.spyOn(offlineInspectionStore, 'listOfflineInspectionDrafts')
      .mockRejectedValue(new Error('total IndexedDB failure'));
    try {
      fireEvent.click(await screen.findByRole('button', { name: 'ยืนยันเริ่มส่ง' }));

      expect(await screen.findByText('ไม่สามารถอัปเดตรายการในเครื่องได้ กรุณาปิดหน้าต่างนี้แล้วลองใหม่')).toBeTruthy();
      expect(screen.queryByText(/ส่งสำเร็จ.*รายการก่อนเกิดปัญหา/)).toBeNull();
      // The result step is never blank — a way forward is always present.
      expect(screen.getByRole('button', { name: 'เสร็จสิ้น' })).toBeTruthy();
      expect((screen.getByLabelText('ปิด') as HTMLButtonElement).disabled).toBe(false);
    } finally {
      listSpy.mockRestore();
    }
  });
});
