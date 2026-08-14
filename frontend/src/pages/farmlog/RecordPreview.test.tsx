/**
 * RecordPreview — round 15.1: inspection photos must render through the
 * scoped AuthenticatedPhoto loader, never as a raw <img src={photoUrl}>
 * pointing at the (now unservable) /media/inspection-photos/... path.
 *
 * Round 8-14C — AuthenticatedPhoto is exercised for REAL here (not mocked)
 * so the click-to-view lightbox integration is actually proven end to end;
 * only the underlying getRecordPhotoBlob API call is mocked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RecordPreview } from './RecordPreview';

const getRecordMock = vi.fn();
const getRecordPhotoBlobMock = vi.fn();

vi.mock('../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/records')>();
  return {
    ...actual,
    getRecord: (...args: unknown[]) => getRecordMock(...args),
    getRecordPhotoBlob: (...args: unknown[]) => getRecordPhotoBlobMock(...args),
  };
});

const RAW_PHOTO_URL = `/media/inspection-photos/${'a'.repeat(32)}.jpg`;

function renderPreview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/farmlog/records/rec-1/preview']}>
        <Routes>
          <Route path="/farmlog/records/:id/preview" element={<RecordPreview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getRecordMock.mockReset();
  getRecordPhotoBlobMock.mockReset();
  getRecordPhotoBlobMock.mockResolvedValue(new Blob(['x'], { type: 'image/jpeg' }));
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fake-url');
  getRecordMock.mockResolvedValue({
    id: 'rec-1', plotId: 'plot-1', supplierId: 'sup-1',
    recordedById: 'user-1', recordedByEmail: 'x@example.com', recordedByName: 'Tester',
    submittedByCode: 'FIELD01', submittedByName: null,
    plotCode: 'SUP001-P001', plotName: 'แปลงทดสอบ', supplierName: 'Supplier One',
    recordDate: '2026-07-01', crop: 'พริก', variety: null, growthStage: null,
    plantingDate: null, yieldPct: '100', weatherCondition: null,
    fieldPrepScore: null, weatherScore: null, careScore: null, varietyResistanceScore: null,
    recommendation: null, notes: null,
    latitude: '13.7563', longitude: '100.5018',
    photoUrls: [RAW_PHOTO_URL],
    customFields: {}, isActive: true,
    createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-01T00:00:00Z',
  });
});

function baseRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: 'rec-1', plotId: 'plot-1', supplierId: 'sup-1',
    recordedById: 'user-1', recordedByEmail: 'x@example.com', recordedByName: 'Tester',
    submittedByCode: 'FIELD01', submittedByName: null,
    plotCode: 'SUP001-P001', plotName: 'แปลงทดสอบ', supplierName: 'Supplier One',
    recordDate: '2026-07-01', crop: 'พริก', variety: null,
    plantingDate: null, yieldPct: '100', weatherCondition: null,
    fieldPrepScore: 8, weatherScore: 7, careScore: 9, varietyResistanceScore: 6,
    recommendation: null, notes: null, latitude: null, longitude: null,
    photoUrls: [], customFields: {}, isActive: true,
    createdAt: '2026-07-01T00:00:00Z', updatedAt: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function snapshot(growthStage: string, labels: [string, string, string, string]) {
  const slots = ['fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore'];
  return {
    inspectionProtocolSnapshot: {
      version: 1, growthStage,
      criteria: slots.map((slot, i) => ({ slot, label: labels[i], score: [8, 7, 9, 6][i] })),
    },
  };
}

describe('RecordPreview — protocol snapshot labels (round 5.3)', () => {
  it('renders the criteria labels from the record snapshot (เจริญเติบโต)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      growthStage: 'เจริญเติบโต',
      customFields: snapshot('เจริญเติบโต', ['สภาพอากาศ', 'การดูแลรักษา', 'ความเสี่ยง', 'สภาพแปลง']),
    }));
    renderPreview();

    // Labels unique to this stage's protocol prove the snapshot is used …
    expect(await screen.findByText('ความเสี่ยง')).toBeTruthy();
    expect(screen.getByText('สภาพแปลง')).toBeTruthy();
    // … and the germination-only fallback label must NOT appear.
    expect(screen.queryByText('การเตรียมแปลง')).toBeNull();
  });

  it('renders ติดผล snapshot labels', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      growthStage: 'ติดผล',
      customFields: snapshot('ติดผล', ['การติดผล', 'ความสมบูรณ์ของผล', 'การดูแลรักษา', 'ความเสี่ยงโรคและแมลง']),
    }));
    renderPreview();

    expect(await screen.findByText('การติดผล')).toBeTruthy();
    expect(screen.getByText('ความสมบูรณ์ของผล')).toBeTruthy();
    expect(screen.getByText('ความเสี่ยงโรคและแมลง')).toBeTruthy();
  });

  it('falls back to the original labels for an old record with no snapshot (no crash)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({ growthStage: 'ระยะงอก', customFields: {} }));
    renderPreview();

    // The score section still renders with the fallback labels.
    expect(await screen.findByText('การเตรียมแปลง')).toBeTruthy();
    expect(screen.getByText('ความต้านทานของสายพันธุ์')).toBeTruthy();
  });
});

describe('RecordPreview — planting-cycle block (round 7.4)', () => {
  it('shows the record\'s OWN cycle (รอบที่ N + crop/plan), not the plot current', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      crop: 'พริกเก่า',            // the record's own snapshot
      plotCycleId: 'cycle-2', cycleNo: 2, cycleStatus: 'harvested',
      cycleCrop: 'ทุเรียน', cycleVariety: 'หมอนทอง', cycleLotNo: 'LOT-09',
      cyclePlantingDate: '2026-06-01', cyclePlantCount: 300,
      cycleExpectedYieldFull: '2000.00', cycleExpectedYieldUnit: 'kg',
    }));
    renderPreview();

    expect(await screen.findByText('รอบที่ 2')).toBeTruthy();
    // cycle crop (ทุเรียน) is shown, distinct from the record snapshot (พริกเก่า)
    expect(screen.getByText('ทุเรียน')).toBeTruthy();
    expect(screen.getByText('LOT-09')).toBeTruthy();
    expect(screen.getByText('2,000 kg')).toBeTruthy();
  });

  it('round 8-3K: labels the Lot No. field clearly as "เลขล็อต (Lot No.)"', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      plotCycleId: 'cycle-2', cycleNo: 2, cycleStatus: 'harvested', cycleLotNo: 'LOT-09',
    }));
    renderPreview();

    const dt = await screen.findByText('เลขล็อต (Lot No.)');
    expect(dt.nextElementSibling?.textContent).toBe('LOT-09');
  });

  it('falls back to "ไม่พบข้อมูลรอบปลูก" when the record has no cycle (no crash)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({ plotCycleId: null, cycleNo: null, cycleLabel: null }));
    renderPreview();

    expect(await screen.findByText('ไม่พบข้อมูลรอบปลูก')).toBeTruthy();
  });

  it('shows the cycleLabel instead of "รอบที่ N" when set (round 8.0.5)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      plotCycleId: 'cycle-2', cycleNo: 2, cycleLabel: 'jun2026',
    }));
    renderPreview();

    expect(await screen.findByText('jun2026')).toBeTruthy();
    expect(screen.queryByText('รอบที่ 2')).toBeNull();
  });

  it('falls back to "รอบที่ N" when cycleLabel is null but a cycle number exists', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      plotCycleId: 'cycle-2', cycleNo: 2, cycleLabel: null,
    }));
    renderPreview();

    expect(await screen.findByText('รอบที่ 2')).toBeTruthy();
  });

  it('reads the label from the record\'s OWN cycle, never the plot\'s current active cycle', async () => {
    // The record's own cycle is closed/older (harvested) — its label must
    // still show, proving this isn't sourced from any "current" plot state.
    getRecordMock.mockResolvedValue(baseRecord({
      plotCycleId: 'cycle-1', cycleNo: 1, cycleStatus: 'harvested', cycleLabel: 'may2026',
    }));
    renderPreview();

    expect(await screen.findByText('may2026')).toBeTruthy();
  });
});

describe('RecordPreview — photo rendering', () => {
  it('renders photos through AuthenticatedPhoto — fetches via the scoped endpoint using the record id + extracted filename', async () => {
    renderPreview();

    await waitFor(() => expect(getRecordPhotoBlobMock).toHaveBeenCalledWith('rec-1', `${'a'.repeat(32)}.jpg`));
  });

  it('never renders a raw <img> pointing directly at the stored photoUrl', async () => {
    renderPreview();
    await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });

    const imgs = document.querySelectorAll('img');
    expect(imgs.length).toBeGreaterThan(0);
    for (const img of imgs) {
      expect(img.getAttribute('src')).not.toBe(RAW_PHOTO_URL);
    }
  });
});

// --- round 8-14C: click-to-view lightbox integration ------------------------

describe('RecordPreview — round 8-14C: photo lightbox integration', () => {
  it('29. clicking a photo thumbnail opens the full-size lightbox', async () => {
    renderPreview();

    const thumbnail = await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });
    fireEvent.click(thumbnail);

    const dialog = await screen.findByRole('dialog');
    expect(dialog.querySelector('img')).toBeTruthy();
    // No second fetch — the lightbox reuses the thumbnail's own Blob.
    expect(getRecordPhotoBlobMock).toHaveBeenCalledTimes(1);
  });

  it('30. the print-only elements and print:hidden header are still present (print layout untouched)', async () => {
    renderPreview();
    await screen.findByRole('button', { name: 'เปิดดูภาพถ่ายแปลงขนาดใหญ่' });

    // The sticky action bar is still hidden in print.
    expect(document.querySelector('.print\\:hidden')).toBeTruthy();
    // The print-only timestamp line is still present with its print:block class.
    expect(screen.getByText(/พิมพ์เมื่อ/).className).toContain('print:block');
  });
});

// --- round 8-3E: phone-access attribution ("ข้อมูลการเข้าตรวจ") -----------

describe('RecordPreview — inspection attribution (round 8-3E)', () => {
  it('shows the formatted phone, phone type, and inspector-type role', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedPhoneSnapshot: '0845552162',
      submittedPhoneType: 'primary',
      inspectorType: 'farmer',
    }));
    renderPreview();

    expect(await screen.findByText('ข้อมูลการเข้าตรวจ')).toBeTruthy();
    expect(screen.getByText(/084-555-2162/)).toBeTruthy();
    expect(screen.getByText('(เบอร์หลัก)')).toBeTruthy();
    expect(screen.getByText('เกษตรกร')).toBeTruthy();
  });

  it('labels an additional-phone submission distinctly from primary', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedPhoneSnapshot: '0855551234',
      submittedPhoneType: 'additional',
      inspectorType: 'supplier',
    }));
    renderPreview();

    await screen.findByText('ข้อมูลการเข้าตรวจ');
    expect(screen.getByText('(เบอร์เสริม)')).toBeTruthy();
    // Round 8-11A — the inspector role reads "บริษัทผู้ผลิต", never the bare
    // word "Supplier" (which still names the Supplier ENTITY elsewhere).
    expect(screen.getByText('บริษัทผู้ผลิต')).toBeTruthy();
    expect(screen.queryByText('Supplier')).toBeNull();
  });

  // Item 20/21 — a historical DEV record that migration 0047 rewrote reads
  // back as 'chiatai' and renders as "Chiatai", never the retired "ส่งเสริม"
  // and never the raw enum.
  it('shows the Chiatai inspector-type label (round 8-11A, was extension)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedPhoneSnapshot: '0866661234',
      submittedPhoneType: 'primary',
      inspectorType: 'chiatai',
    }));
    renderPreview();

    await screen.findByText('ข้อมูลการเข้าตรวจ');
    expect(screen.getByText('Chiatai')).toBeTruthy();
    expect(screen.queryByText('ส่งเสริม')).toBeNull();
    expect(screen.queryByText('chiatai')).toBeNull();   // never the raw enum
  });

  it('shows a generic fallback and never crashes for a record with no phone binding', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedPhoneSnapshot: null, submittedPhoneType: null, inspectorType: null,
    }));
    renderPreview();

    expect(await screen.findByText('ผู้ใช้ในระบบ / ข้อมูลเดิม')).toBeTruthy();
  });

  it('never derives a phone from the plot — only reads the record snapshot fields', async () => {
    // No plotAccessPhoneId/current-plot-phone data is ever fetched by this
    // page at all (getRecord is the only call) — this test simply pins that
    // the snapshot on the RECORD itself is what renders, not anything else.
    getRecordMock.mockResolvedValue(baseRecord({
      submittedPhoneSnapshot: '0899991234',
      submittedPhoneType: 'primary',
      inspectorType: 'farmer',
    }));
    renderPreview();

    await screen.findByText(/089-999-1234/);
    expect(getRecordMock).toHaveBeenCalledTimes(1);
  });
});

// --- round 8-8C: kg-first Yield display + >150% informational note --------

describe('RecordPreview — Yield kg display (round 8-8C)', () => {
  it('kg-first record shows quantity, target snapshot, and percent', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '80.0', yieldQuantityKg: '800.00', yieldTargetKgSnapshot: '1000.00',
    }));
    renderPreview();

    expect(await screen.findByText('ปริมาณผลผลิตที่ประเมินได้')).toBeTruthy();
    expect(screen.getByText('800 kg')).toBeTruthy();
    expect(screen.getByText('เป้าหมายที่ใช้คำนวณ')).toBeTruthy();
    expect(screen.getByText('1,000 kg')).toBeTruthy();
    expect(screen.getByText('เปอร์เซ็นต์เทียบเป้าหมาย')).toBeTruthy();
    expect(screen.getByText('80%')).toBeTruthy();
  });

  it('legacy record (no kg) falls back to the original percent-only display', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '80.0', yieldQuantityKg: null, yieldTargetKgSnapshot: null,
    }));
    renderPreview();

    expect(await screen.findByText('% คาดว่าจะได้ผลผลิต')).toBeTruthy();
    expect(screen.getByText('80%')).toBeTruthy();
    expect(screen.queryByText('ปริมาณผลผลิตที่ประเมินได้')).toBeNull();
  });

  it('quantity=0 shows "0 kg", never the em dash', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '0.0', yieldQuantityKg: '0.00', yieldTargetKgSnapshot: '1000.00',
    }));
    renderPreview();

    expect(await screen.findByText('0 kg')).toBeTruthy();
  });

  it('quantity present but no comparable target shows the quantity + a "no target" note', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: null, yieldQuantityKg: '500.00', yieldTargetKgSnapshot: null,
    }));
    renderPreview();

    expect(await screen.findByText('500 kg')).toBeTruthy();
    expect(screen.getByText('ไม่มีเป้าหมายสำหรับคำนวณเปอร์เซ็นต์')).toBeTruthy();
  });

  it('renders no Yield section at all when there is no yield data whatsoever', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: null, yieldQuantityKg: null, yieldTargetKgSnapshot: null,
    }));
    renderPreview();

    await screen.findByText('บันทึกการตรวจแปลง');
    expect(screen.queryByText('ผลผลิต (Yield)')).toBeNull();
  });

  it('a real >150% value renders the TRUE number with an amber informational note, never red/error', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '510.0', yieldQuantityKg: '5100.00', yieldTargetKgSnapshot: '1000.00',
    }));
    renderPreview();

    expect(await screen.findByText('510%')).toBeTruthy();
    const note = screen.getByText(/ผลผลิตสูงกว่า 150%/);
    expect(note.className).toContain('amber');
    expect(note.className).not.toContain('red');
  });

  it('the progress bar fill never exceeds 100% width even at 510%', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '510.0', yieldQuantityKg: '5100.00', yieldTargetKgSnapshot: '1000.00',
    }));
    renderPreview();

    const fill = await screen.findByTestId('yield-bar-fill');
    expect(fill.style.width).toBe('100%');
    // …but the displayed NUMBER is never clamped to match the bar.
    expect(screen.getByText('510%')).toBeTruthy();
  });

  it('no amber note at exactly 150% (the warning threshold itself)', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      yieldPct: '150.0', yieldQuantityKg: '1500.00', yieldTargetKgSnapshot: '1000.00',
    }));
    renderPreview();

    expect(await screen.findByText('150%')).toBeTruthy();
    expect(screen.queryByText(/ผลผลิตสูงกว่า 150%/)).toBeNull();
  });
});

describe('RecordPreview — submittedByCode retirement (round 8-3G)', () => {
  it('shows the historical code + name for an old record that still has one', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedByCode: 'FIELD01', submittedByName: 'สมชาย',
    }));
    renderPreview();

    expect(await screen.findByText(/FIELD01 — สมชาย/)).toBeTruthy();
  });

  it('shows just the name (no code) when submittedByCode is null but a name was given', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedByCode: null, submittedByName: 'สมหญิง',
    }));
    renderPreview();

    expect(await screen.findByText(/ผู้กรอกข้อมูลหน้างาน/)).toBeTruthy();
    expect(screen.getByText(/สมหญิง/)).toBeTruthy();
  });

  it('omits the ผู้กรอกข้อมูลหน้างาน line entirely when both code and name are null', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedByCode: null, submittedByName: null,
      recordedByName: 'ผู้ใช้ระบบ',
    }));
    renderPreview();

    await screen.findByText(/ผู้ใช้ระบบ/);
    expect(screen.queryByText(/ผู้กรอกข้อมูลหน้างาน/)).toBeNull();
  });

  it('never renders the literal strings "null" or "undefined" anywhere on the page', async () => {
    getRecordMock.mockResolvedValue(baseRecord({
      submittedByCode: null, submittedByName: null,
    }));
    renderPreview();

    await screen.findByText('บันทึกการตรวจแปลง');
    expect(document.body.textContent).not.toContain('null');
    expect(document.body.textContent).not.toContain('undefined');
  });
});
