/**
 * PlotCycleModals — round 8-5B PO/P.Code + Auto/Manual lot UX. Unit tests for
 * the exported payload/preview/badge helpers (the deterministic core of the
 * form logic) plus a render test that the Auto/Manual segmented control drives
 * the lotNo input. The create/start/rollover integration flows are covered by
 * Plots.test.tsx / PlotDetail.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  toPayload,
  toEditPayload,
  autoLotPreview,
  lotSourceBadge,
  cycleFormSchema,
  cycleEditFormSchema,
  CyclePlanFields,
  StartCycleModal,
  EditCycleModal,
  RolloverCycleModal,
  ReactivatePlotWithCycleModal,
  type CycleFormValues,
  type CycleEditFormValues,
} from './PlotCycleModals';
import type { PlotCycle } from '../../api/plots';

// react-query is imported transitively by ./PlotCycleModals; the helpers under
// test don't touch it, and CyclePlanFields renders standalone.
//
// Round 8-26C — this stub is now INTERACTIVE. P.Code stopped being a typed
// field and is derived from the chosen พันธุ์, so a display-only stub would
// leave no way to reach it at all. One <select> per `type`, addressed by
// data-testid.
vi.mock('./MasterDataSelect', () => ({
  MasterDataSelect: ({ type, value, onChange }: {
    type: string; value: string | null; onChange: (v: string | null) => void;
  }) => (
    <select
      data-testid={`master-select-${type}`}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">—</option>
      <option value="พริก">พริก</option>
      <option value="พริกขี้หนู">พริกขี้หนู</option>
      <option value="พริกไม่มีรหัส">พริกไม่มีรหัส</option>
    </select>
  ),
}));

// The variety -> active P.Code mapping the form derives from. พริกขี้หนู
// resolves to WM-141 so the payload assertions below keep asserting the same
// value they did when P.Code was typed by hand; พริกไม่มีรหัส deliberately
// resolves to nothing, for the "this variety has no P.Code yet" path.
const P_CODE_BY_VARIETY: Record<string, string> = { 'พริกขี้หนู': 'WM-141' };
const listMasterDataMock = vi.fn();
vi.mock('../../api/masterdata', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/masterdata')>()),
  listMasterData: (...a: unknown[]) => listMasterDataMock(...a),
}));

beforeEach(() => {
  listMasterDataMock.mockReset();
  listMasterDataMock.mockImplementation(({ type, parent }: { type: string; parent?: string }) => {
    if (type !== 'p_code') return Promise.resolve([]);
    const value = parent ? P_CODE_BY_VARIETY[parent] : undefined;
    return Promise.resolve(value ? [{
      id: `pc-${value}`, type: 'p_code', value, parent: parent ?? null,
      orderIndex: 0, active: true,
      createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    }] : []);
  });
});

/** Pick a พันธุ์ and wait for the derived P.Code to land in the form —
 * replaces the old "type into the P.Code box" step everywhere below. */
async function pickVariety(variety = 'พริกขี้หนู') {
  fireEvent.change(screen.getByTestId('master-select-variety'), { target: { value: variety } });
  const expected = P_CODE_BY_VARIETY[variety] ?? '';
  await waitFor(() =>
    expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe(expected),
  );
}

const updatePlotCycleMock = vi.fn();
const createPlotCycleMock = vi.fn();
const rolloverPlotCycleMock = vi.fn();
const reactivatePlotWithCycleMock = vi.fn();
vi.mock('../../api/plots', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/plots')>()),
  updatePlotCycle: (...a: unknown[]) => updatePlotCycleMock(...a),
  createPlotCycle: (...a: unknown[]) => createPlotCycleMock(...a),
  rolloverPlotCycle: (...a: unknown[]) => rolloverPlotCycleMock(...a),
  reactivatePlotWithCycle: (...a: unknown[]) => reactivatePlotWithCycleMock(...a),
}));

function base(): CycleFormValues {
  return { poNumber: 'po25001', pCode: 'Melon-A', cycleLabel: '2605', lotMode: 'auto' } as CycleFormValues;
}

describe('toPayload (CREATE) — round 8-5B', () => {
  it('always sends trimmed PO (as entered) + pCode; Auto sends lotNo=null (never a client-guessed number)', () => {
    const p = toPayload({ ...base(), poNumber: '  po25001 ', pCode: '  Melon-A ', lotMode: 'auto' });
    expect(p.poNumber).toBe('po25001');
    expect(p.pCode).toBe('Melon-A');
    expect(p.lotNo).toBeNull(); // Auto → backend generates it; never a running number from the client
    // never leaks the server-derived fields
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });

  it('Manual sends the entered lotNo verbatim (Manual wins)', () => {
    const p = toPayload({ ...base(), lotMode: 'manual', lotNo: '  HAND-01 ' });
    expect(p.lotNo).toBe('HAND-01');
  });
});

describe('toEditPayload (EDIT) — preserve / regenerate semantics', () => {
  function editBase(): CycleEditFormValues {
    return { poNumber: '', pCode: '', cycleLabel: '2605', lotMode: 'keep' } as CycleEditFormValues;
  }

  it('keep → omits lotNo entirely (preserve existing lot)', () => {
    const p = toEditPayload({ ...editBase(), lotMode: 'keep' });
    expect(Object.prototype.hasOwnProperty.call(p, 'lotNo')).toBe(false);
  });

  it('auto WITH a PO → sends lotNo=null (explicit regenerate)', () => {
    // Round 8-5B.1 — Auto regenerate requires a PO; with one, lotNo=null is sent.
    const p = toEditPayload({ ...editBase(), lotMode: 'auto', poNumber: 'PO25001' });
    expect(p.lotNo).toBeNull();
  });

  it('manual → sends the entered lotNo', () => {
    const p = toEditPayload({ ...editBase(), lotMode: 'manual', lotNo: 'NEW-9' });
    expect(p.lotNo).toBe('NEW-9');
  });

  it('round 8-13B: blank pCode → omitted (preserve); blank poNumber → sent as null (CLEAR, never omitted)', () => {
    const preserved = toEditPayload({ ...editBase(), poNumber: '', pCode: '' });
    expect(preserved).toHaveProperty('poNumber');
    expect(preserved.poNumber).toBeNull();
    expect(preserved).not.toHaveProperty('pCode');
    const changed = toEditPayload({ ...editBase(), poNumber: ' po-9 ', pCode: ' Melon-B ' });
    expect(changed.poNumber).toBe('po-9');
    expect(changed.pCode).toBe('Melon-B');
  });

  it('never leaks server-derived lot fields', () => {
    const p = toEditPayload({ ...editBase(), lotMode: 'manual', lotNo: 'X' });
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });
});

// Round 8-12B — formula V2: {cycleLabel}-{supplierCode}-{pCode}-###.
describe('autoLotPreview — V2 formula, ### for the server-assigned running no', () => {
  it('formats {cycleLabel}-{supplierCode}-{pCode}-###', () => {
    expect(autoLotPreview('2605', 'SUP010', 'WM-141')).toBe('2605-SUP010-WM-141-###');
  });

  it('keeps an arbitrary cycle label verbatim — never parsed as a date', () => {
    expect(autoLotPreview('26-may', 'SUP010', 'WM-141')).toBe('26-may-SUP010-WM-141-###');
    expect(autoLotPreview('MAY26', 'SUP010', 'ABC')).toBe('MAY26-SUP010-ABC-###');
    expect(autoLotPreview('รอบทดลอง', 'SUP010', 'WM-141')).toBe('รอบทดลอง-SUP010-WM-141-###');
  });

  it('keeps the P.Code in full — never clipped to three characters', () => {
    expect(autoLotPreview('2605', 'SUP010', 'WM-141')).toContain('WM-141');
  });

  it('trims each component but does not change its case', () => {
    expect(autoLotPreview('  26-may  ', ' SUP010 ', '  WM-141 ')).toBe('26-may-SUP010-WM-141-###');
  });

  it('shows a readable placeholder for each component still blank', () => {
    expect(autoLotPreview('', 'SUP010', 'WM-141')).toBe('<ชื่อรอบปลูก>-SUP010-WM-141-###');
    expect(autoLotPreview('2605', '', 'WM-141')).toBe('2605-<รหัส Supplier>-WM-141-###');
    expect(autoLotPreview('2605', 'SUP010', '')).toBe('2605-SUP010-<P.Code>-###');
    expect(autoLotPreview('', '', '')).toBe('<ชื่อรอบปลูก>-<รหัส Supplier>-<P.Code>-###');
  });

  it('never contains a PO or a plot code — V1 used both, V2 uses neither', () => {
    const preview = autoLotPreview('2605', 'SUP010', 'WM-141');
    expect(preview).not.toContain('PO25001');
    expect(preview).not.toContain('P001');
    expect(preview).not.toContain('XX');
  });

  it('the running segment is always the literal ### (never computed client-side)', () => {
    expect(autoLotPreview('2605', 'SUP010', 'WM-141').endsWith('-###')).toBe(true);
    expect(autoLotPreview('a', 'b', 'c')).not.toMatch(/-\d+$/);
  });
});

describe('lotSourceBadge', () => {
  it('maps source → Thai label/tone; legacy-with-lot → "ข้อมูลเดิม"; nothing when no lot', () => {
    expect(lotSourceBadge('auto', true)?.label).toBe('อัตโนมัติ');
    expect(lotSourceBadge('manual', true)?.label).toBe('กรอกเอง');
    expect(lotSourceBadge('legacy', true)?.label).toBe('ข้อมูลเดิม');
    expect(lotSourceBadge(null, true)?.label).toBe('ข้อมูลเดิม'); // has a lot but no source tag
    expect(lotSourceBadge(null, false)).toBeNull();
  });
});

describe('schema required rules', () => {
  it('CREATE rejects blank pCode (still required); EDIT allows blank pCode (preserve)', () => {
    // Round 8-17A.1 — cycleLabel supplied here so this isolates the pCode
    // rule specifically (cycleLabel's OWN requirement is covered separately
    // below, under "refineEditCyclePlan"/"cycleFormSchema").
    expect(cycleFormSchema.safeParse({ lotMode: 'auto', poNumber: '', pCode: '', cycleLabel: '2605', variety: 'พริกขี้หนู' }).success).toBe(false);
    expect(cycleEditFormSchema.safeParse({ lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605' }).success).toBe(true);
  });
  it('round 8-13B: CREATE accepts a blank PO alone, as long as pCode is present', () => {
    const r = cycleFormSchema.safeParse({
      lotMode: 'manual', lotNo: 'HAND-1', poNumber: '', pCode: 'X', cycleLabel: '2605', variety: 'พริกขี้หนู',
    });
    expect(r.success).toBe(true);
  });
  it('Manual lot with a blank value is rejected', () => {
    const r = cycleFormSchema.safeParse({
      poNumber: 'PO', pCode: 'PC', cycleLabel: '2605', lotMode: 'manual', lotNo: '', variety: 'พริกขี้หนู',
    });
    expect(r.success).toBe(false);
  });
});

// --- render: the segmented control drives the lot input --------------------

function Harness({ supplierCode = 'SUP010', mode = 'create' as const }: {
  supplierCode?: string; mode?: 'create' | 'edit';
}) {
  const { register, watch, setValue, formState: { errors } } = useForm<CycleFormValues>({
    resolver: zodResolver(cycleFormSchema),
    defaultValues: {
      lotMode: 'auto', poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      crop: 'พริก', variety: 'พริกขี้หนู',
    },
  });
  return (
    <CyclePlanFields
      register={register} errors={errors} watch={watch} setValue={setValue}
      supplierCode={supplierCode} mode={mode}
    />
  );
}

/** Round 8-26C — CyclePlanFields resolves the P.Code through react-query, so
 * a standalone render needs a provider. */
function renderPlan(props: { supplierCode?: string; mode?: 'create' | 'edit' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
}

describe('CyclePlanFields — Auto/Manual segmented control (create)', () => {
  it('defaults to Auto: shows the V2 preview, no editable lot input', () => {
    renderPlan();
    expect(screen.getByText('2605-SUP010-WM-141-###')).toBeTruthy();
    expect(screen.queryByPlaceholderText('เช่น LOT-01')).toBeNull();
  });

  it('switching to "กรอก Lot เอง" reveals the manual lot input', () => {
    renderPlan();
    fireEvent.click(screen.getByRole('button', { name: 'กรอก Lot เอง' }));
    expect(screen.getByPlaceholderText('เช่น LOT-01')).toBeTruthy();
    expect(screen.queryByText('2605-SUP010-WM-141-###')).toBeNull();
  });

  it('the Supplier Lot No input sits outside the Auto/Manual control and stays visible in every mode', () => {
    renderPlan();
    expect(screen.getByPlaceholderText('เช่น SUP-LOT-A123')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'กรอก Lot เอง' }));
    expect(screen.getByPlaceholderText('เช่น SUP-LOT-A123')).toBeTruthy();
  });

  it('labels the system lot so it is not confused with the supplier lot', () => {
    renderPlan();
    expect(screen.getByText('Lot No ระบบ')).toBeTruthy();
    expect(screen.getByText('Supplier Lot No')).toBeTruthy();
  });
});

// --- round 8-12B: Auto Lot requires cycleLabel + P.Code (NOT a PO) ---------

describe('refineEditCyclePlan — edit Auto requires cycleLabel + P.Code', () => {
  it('edit + auto + blank cycleLabel/pCode -> rejected, naming BOTH missing fields', () => {
    const r = cycleEditFormSchema.safeParse({ lotMode: 'auto', poNumber: '', pCode: '' });
    expect(r.success).toBe(false);
    if (!r.success) {
      const paths = r.error.issues.map((i) => i.path[0]);
      expect(paths).toContain('cycleLabel');
      expect(paths).toContain('pCode');
      // the retired PO rule must be gone
      expect(paths).not.toContain('poNumber');
    }
  });

  it('edit + auto + a PO but no cycleLabel/pCode -> STILL rejected (a PO does not satisfy V2)', () => {
    const r = cycleEditFormSchema.safeParse({ lotMode: 'auto', poNumber: 'PO25001', pCode: '' });
    expect(r.success).toBe(false);
  });

  it('edit + auto + cycleLabel + pCode and NO PO -> ok (V2 never needs the PO)', () => {
    const r = cycleEditFormSchema.safeParse({
      lotMode: 'auto', poNumber: '', pCode: 'WM-141', cycleLabel: '2605',
    });
    expect(r.success).toBe(true);
  });

  it('edit + keep + blank cycleLabel -> rejected (round 8-17A.1: required in every edit, including keep/preserve mode)', () => {
    expect(cycleEditFormSchema.safeParse({ lotMode: 'keep', poNumber: '', pCode: '' }).success).toBe(false);
  });

  it('edit + keep + blank everything ELSE but cycleLabel present -> ok (preserve still works for pCode/PO)', () => {
    expect(
      cycleEditFormSchema.safeParse({ lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605' }).success,
    ).toBe(true);
  });

  it('edit + manual + value -> ok, Auto components (pCode) not required — cycleLabel still is', () => {
    expect(
      cycleEditFormSchema.safeParse({
        lotMode: 'manual', lotNo: 'X', poNumber: '', pCode: '', cycleLabel: '2605',
      }).success,
    ).toBe(true);
  });
});

describe('cycleFormSchema (create) — Auto requires cycleLabel + P.Code', () => {
  // variety is required on CREATE since round 8-26C (P.Code derives from it).
  const base = { poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605', variety: 'พริกขี้หนู' };

  it('auto + all components -> ok', () => {
    expect(cycleFormSchema.safeParse({ ...base, lotMode: 'auto' }).success).toBe(true);
  });

  it('auto + missing cycleLabel -> blocked', () => {
    const r = cycleFormSchema.safeParse({ ...base, cycleLabel: '', lotMode: 'auto' });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('cycleLabel');
  });

  it('auto + missing pCode -> blocked', () => {
    const r = cycleFormSchema.safeParse({ ...base, pCode: '', lotMode: 'auto' });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('pCode');
  });

  it('manual + a lot value + no cycleLabel -> blocked (round 8-17A.1: cycleLabel required in every mode, not just Auto)', () => {
    const r = cycleFormSchema.safeParse({
      ...base, cycleLabel: '', lotMode: 'manual', lotNo: 'HAND-1',
    });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('cycleLabel');
  });
});

describe('toEditPayload — Auto regenerate is no longer gated on the PO', () => {
  it('auto + PO -> sends lotNo:null + poNumber, never source/running', () => {
    const p = toEditPayload({ lotMode: 'auto', poNumber: 'PO25001', pCode: '', cycleLabel: '2605' } as CycleEditFormValues);
    expect(p.lotNo).toBeNull();
    expect(p.poNumber).toBe('PO25001');
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });

  it('auto + blank PO -> STILL emits lotNo:null (V2 regenerates without a PO); poNumber sent as null (clear)', () => {
    const p = toEditPayload({ lotMode: 'auto', poNumber: '', pCode: '', cycleLabel: '2605' } as CycleEditFormValues);
    expect(Object.prototype.hasOwnProperty.call(p, 'lotNo')).toBe(true);
    expect(p.lotNo).toBeNull();
    // round 8-13B: poNumber is now ALWAYS sent (blank = clear), never omitted.
    expect(p).toHaveProperty('poNumber');
    expect(p.poNumber).toBeNull();
  });

  it('keep -> omits lotNo entirely (the existing lot is preserved)', () => {
    const p = toEditPayload({ lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605' } as CycleEditFormValues);
    expect(Object.prototype.hasOwnProperty.call(p, 'lotNo')).toBe(false);
  });
});

describe('supplierLotNo payloads', () => {
  it('create trims the value and never sends an internal series key', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      lotMode: 'auto', supplierLotNo: '  SUP-OWN-1  ',
    } as CycleFormValues);
    expect(p.supplierLotNo).toBe('SUP-OWN-1');
    expect(p).not.toHaveProperty('autoLotSeriesKey');
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });

  it('create with a blank supplier lot sends null', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      lotMode: 'auto', supplierLotNo: '   ',
    } as CycleFormValues);
    expect(p.supplierLotNo).toBeNull();
  });

  it('supplier lot never changes the Manual/Auto decision', () => {
    const auto = toPayload({
      poNumber: 'P', pCode: 'C', cycleLabel: 'L', lotMode: 'auto', supplierLotNo: 'S-1',
    } as CycleFormValues);
    const manual = toPayload({
      poNumber: 'P', pCode: 'C', cycleLabel: 'L', lotMode: 'manual', lotNo: 'M-1', supplierLotNo: 'S-1',
    } as CycleFormValues);
    expect(auto.lotNo).toBeNull();
    expect(manual.lotNo).toBe('M-1');
  });

  it('edit sends the supplier lot on every lot mode, including keep', () => {
    const p = toEditPayload({
      lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605', supplierLotNo: 'S-9',
    } as CycleEditFormValues);
    expect(p.supplierLotNo).toBe('S-9');
    expect(p).not.toHaveProperty('lotNo');
  });
});

function legacyCycle(over: Partial<PlotCycle> = {}): PlotCycle {
  return {
    id: 'c1', plotId: 'p1', cycleNo: 2, status: 'active',
    crop: 'พริก', variety: null, cycleLabel: 'jun2026',
    lotNo: 'LEGACY-LOT', poNumber: null, pCode: null, lotNoSource: null, lotRunningNo: null, supplierLotNo: null,
    oracleSupplierCode: null, oracleInvoice: null, refAccount: null,
    plantingDate: null, plantCount: null, expectedYieldFull: null, expectedYieldUnit: null,
    startedAt: '2026-06-01T00:00:00Z', closedAt: null, closedById: null, closeReason: null,
    finalYieldPct: null, finalEstimatedYield: null, finalInspectionRecordId: null,
    harvestYield: null, finalYieldAfterClean: null, finalYieldUnit: null,
    harvestDate: null, finalNote: null,
    createdAt: '2026-06-01T00:00:00Z', updatedAt: '2026-06-01T00:00:00Z',
    ...over,
  };
}

function renderEdit(cycle: PlotCycle) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <EditCycleModal plotId="p1" supplierCode="SUP010" cycle={cycle} onClose={onClose} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { onClose, onSaved };
}

// Round 8-12B — V1 blocked "regenerate Auto Lot" without a PO. V2's formula
// has no PO at all, so that rule is GONE; what Auto needs now is a cycleLabel
// and a P.Code. These tests replace the round-8-5B.1 pair that asserted the
// opposite.
describe('EditCycleModal — Auto regenerate requires cycleLabel + P.Code, never a PO', () => {
  it('choosing "สร้าง Auto Lot ใหม่" with no P.Code blocks submit and names P.Code, not the PO', async () => {
    updatePlotCycleMock.mockReset();
    // legacy cycle: has a lot + a cycleLabel, but no pCode and no PO.
    const { onSaved } = renderEdit(legacyCycle());

    expect(screen.getByText('LEGACY-LOT')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง Auto Lot ใหม่' }));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    expect(await screen.findByText('กรุณากรอก P.Code ก่อนสร้าง Lot No ระบบอัตโนมัติ')).toBeTruthy();
    // the retired PO rule must not fire
    expect(screen.queryByText('กรุณากรอก PO Number ก่อนสร้าง Auto Lot')).toBeNull();
    expect(updatePlotCycleMock).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByText('แก้รอบปลูก — รอบที่ 2')).toBeTruthy();
  });

  it('filling P.Code alone lets Auto through — a PO is never required (sends lotNo:null, poNumber:null)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(
      legacyCycle({ lotNo: 'jun2026-SUP010-WM-141-001', lotNoSource: 'auto' }),
    );
    const { onSaved } = renderEdit(legacyCycle());

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง Auto Lot ใหม่' }));
    await pickVariety();
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    const [, , payload] = updatePlotCycleMock.mock.calls[0];
    expect(payload.lotNo).toBeNull();          // = "regenerate"
    expect(payload.pCode).toBe('WM-141');
    // round 8-13B: poNumber is ALWAYS sent now (blank input -> null), never
    // omitted — the legacy cycle already had no PO, so this is a no-op clear.
    expect(payload).toHaveProperty('poNumber');
    expect(payload.poNumber).toBeNull();
    expect(payload).not.toHaveProperty('lotNoSource');
    expect(payload).not.toHaveProperty('lotRunningNo');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('a blank cycleLabel blocks Auto and names the cycle label', async () => {
    updatePlotCycleMock.mockReset();
    renderEdit(legacyCycle({ cycleLabel: null, pCode: 'WM-141' }));

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง Auto Lot ใหม่' }));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    // Round 8-17A.1 — the Auto-only message is retired; blank cycleLabel
    // now fails the unconditional requireCycleLabel check with this message.
    expect(
      await screen.findByText('กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ'),
    ).toBeTruthy();
    expect(updatePlotCycleMock).not.toHaveBeenCalled();
  });

  it('an untouched edit of a complete cycle can regenerate straight away (values are prefilled)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ pCode: 'WM-141', cycleLabel: '2605' }));

    fireEvent.click(screen.getByRole('button', { name: 'สร้าง Auto Lot ใหม่' }));
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    expect(updatePlotCycleMock.mock.calls[0][2].lotNo).toBeNull();
  });
});

describe('EditCycleModal — Supplier Lot No', () => {
  it('prefills the stored value and sends it back trimmed', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ supplierLotNo: 'SUP-OWN-7' }));

    const input = screen.getByPlaceholderText('เช่น SUP-LOT-A123') as HTMLInputElement;
    expect(input.value).toBe('SUP-OWN-7');

    fireEvent.change(input, { target: { value: '  SUP-OWN-9  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    expect(updatePlotCycleMock.mock.calls[0][2].supplierLotNo).toBe('SUP-OWN-9');
  });

  it('emptying the box clears it (sends null), and never touches the system lot', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ supplierLotNo: 'SUP-OWN-7' }));

    fireEvent.change(screen.getByPlaceholderText('เช่น SUP-LOT-A123'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    const payload = updatePlotCycleMock.mock.calls[0][2];
    expect(payload.supplierLotNo).toBeNull();
    // lot mode defaulted to "เก็บ Lot เดิม" → the system lot is untouched
    expect(payload).not.toHaveProperty('lotNo');
  });
});

// --- round 8-13B: PO Number label/optionality across every create-mode -----
// modal (Start/Rollover/Reactivate all render CyclePlanFields mode="create").

describe('CyclePlanFields (create) — PO Number label', () => {
  it('never shows a required-marker (*) on PO Number', () => {
    renderPlan();
    expect(screen.getByText('PO Number (ไม่บังคับ)')).toBeTruthy();
    expect(screen.queryByText('PO Number *')).toBeNull();
  });

  it('still shows the required marker (*) on P.Code', () => {
    renderPlan();
    expect(screen.getByText('P.Code *')).toBeTruthy();
  });
});

describe('StartCycleModal — PO Number optional (round 8-13B)', () => {
  function renderStart() {
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const onClose = vi.fn();
    const onSaved = vi.fn();
    render(
      <QueryClientProvider client={qc}>
        <StartCycleModal plotId="p1" supplierCode="SUP010" onClose={onClose} onSaved={onSaved} />
      </QueryClientProvider>,
    );
    return { onClose, onSaved };
  }

  it('submits successfully with PO left blank — payload.poNumber is null', async () => {
    createPlotCycleMock.mockReset();
    createPlotCycleMock.mockResolvedValue(legacyCycle());
    const { onSaved } = renderStart();

    fireEvent.click(screen.getByRole('button', { name: 'กรอก Lot เอง' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น LOT-01'), { target: { value: 'HAND-1' } });
    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required regardless of Auto/Manual lot.
    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } },
    );
    // PO Number left untouched (blank default).
    fireEvent.click(screen.getByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledTimes(1));
    const [, payload] = createPlotCycleMock.mock.calls[0];
    expect(payload.poNumber).toBeNull();
    expect(payload.pCode).toBe('WM-141');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('Auto Lot + blank PO + cycleLabel/pCode filled -> submits, preview never shows a PO', async () => {
    createPlotCycleMock.mockReset();
    createPlotCycleMock.mockResolvedValue(legacyCycle());
    renderStart();

    fireEvent.change(screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } });
    await pickVariety();
    expect(screen.getByText('2605-SUP010-WM-141-###')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledTimes(1));
    const [, payload] = createPlotCycleMock.mock.calls[0];
    expect(payload.poNumber).toBeNull();
    expect(payload.lotNo).toBeNull(); // Auto — backend generates it
  });
});

describe('RolloverCycleModal — PO Number optional (round 8-13B)', () => {
  it('the new cycle submits with PO left blank', async () => {
    rolloverPlotCycleMock.mockReset();
    rolloverPlotCycleMock.mockResolvedValue({
      plotId: 'p1', activeCycleNo: 2, closedCycle: legacyCycle(), newCycle: legacyCycle({ cycleNo: 2 }),
    });
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const onSaved = vi.fn();
    render(
      <QueryClientProvider client={qc}>
        <RolloverCycleModal plotId="p1" supplierCode="SUP010" cycle={legacyCycle()} onClose={vi.fn()} onSaved={onSaved} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'กรอก Lot เอง' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น LOT-01'), { target: { value: 'HAND-2' } });
    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required regardless of Auto/Manual lot.
    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'jul2026' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันจบรอบ + เริ่มรอบใหม่' }));

    await waitFor(() => expect(rolloverPlotCycleMock).toHaveBeenCalledTimes(1));
    const [, , payload] = rolloverPlotCycleMock.mock.calls[0];
    expect(payload.newCycle.poNumber).toBeNull();
    expect(payload.newCycle.pCode).toBe('WM-141');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});

describe('ReactivatePlotWithCycleModal — PO Number optional (round 8-13B)', () => {
  it('submits with PO left blank', async () => {
    reactivatePlotWithCycleMock.mockReset();
    reactivatePlotWithCycleMock.mockResolvedValue({ plot: {}, cycle: legacyCycle() });
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const onSaved = vi.fn();
    render(
      <QueryClientProvider client={qc}>
        <ReactivatePlotWithCycleModal plotId="p1" supplierCode="SUP010" onClose={vi.fn()} onSaved={onSaved} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'กรอก Lot เอง' }));
    fireEvent.change(screen.getByPlaceholderText('เช่น LOT-01'), { target: { value: 'HAND-3' } });
    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required regardless of Auto/Manual lot.
    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'aug2026' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูก' }));

    await waitFor(() => expect(reactivatePlotWithCycleMock).toHaveBeenCalledTimes(1));
    const [, payload] = reactivatePlotWithCycleMock.mock.calls[0];
    expect(payload.poNumber).toBeNull();
    expect(payload.pCode).toBe('WM-141');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});

describe('EditCycleModal — PO Number prefill / preserve / clear (round 8-13B)', () => {
  it('prefills the stored PO value', () => {
    renderEdit(legacyCycle({ poNumber: 'PO25001' }));
    const input = screen.getByPlaceholderText('เช่น PO25001') as HTMLInputElement;
    expect(input.value).toBe('PO25001');
  });

  it('shows the clear-oriented helper text, not the old generic "preserve" text', () => {
    renderEdit(legacyCycle({ poNumber: 'PO25001' }));
    expect(screen.getByText('ไม่บังคับ — เว้นว่างเพื่อลบ PO Number ของรอบนี้')).toBeTruthy();
  });

  it('leaving the prefilled PO untouched and saving preserves it (same value re-sent)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ poNumber: 'PO25001' }));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    expect(updatePlotCycleMock.mock.calls[0][2].poNumber).toBe('PO25001');
  });

  it('deleting the prefilled PO down to blank sends poNumber:null (clears it)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ poNumber: 'PO25001' }));

    fireEvent.change(screen.getByPlaceholderText('เช่น PO25001'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    const payload = updatePlotCycleMock.mock.calls[0][2];
    expect(payload).toHaveProperty('poNumber');
    expect(payload.poNumber).toBeNull();
  });
});

// --- Round 8-21B: Oracle Supplier Code / Oracle Invoice / Ref Account ------

describe('CyclePlanFields — Oracle reference group (round 8-21B)', () => {
  it('renders all three inputs under their own heading, none required (no *)', () => {
    renderPlan();
    expect(screen.getByText('ข้อมูลอ้างอิง Oracle')).toBeTruthy();
    expect(screen.getByText('Oracle Supplier Code')).toBeTruthy();
    expect(screen.getByText('Oracle Invoice')).toBeTruthy();
    expect(screen.getByText('Ref Account')).toBeTruthy();
    // None of the three labels ever carries a required marker.
    expect(screen.queryByText('Oracle Supplier Code *')).toBeNull();
    expect(screen.queryByText('Oracle Invoice *')).toBeNull();
    expect(screen.queryByText('Ref Account *')).toBeNull();
  });

  it('caps every input at 255 characters (maxLength attribute)', () => {
    renderPlan();
    for (const placeholder of ['เช่น ORC-SUP-001', 'เช่น INV-2026-0001', 'เช่น ACC-0001']) {
      const input = screen.getByPlaceholderText(placeholder) as HTMLInputElement;
      expect(input.maxLength).toBe(255);
    }
  });
});

describe('toPayload/toEditPayload — Oracle reference fields', () => {
  it('create: trims and sends all three values', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605', lotMode: 'auto',
      oracleSupplierCode: '  ORC-SUP-1  ', oracleInvoice: '  INV-1  ', refAccount: '  ACC-1  ',
    } as CycleFormValues);
    expect(p.oracleSupplierCode).toBe('ORC-SUP-1');
    expect(p.oracleInvoice).toBe('INV-1');
    expect(p.refAccount).toBe('ACC-1');
  });

  it('create: blank/omitted fields all send null', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605', lotMode: 'auto',
      oracleSupplierCode: '   ',
    } as CycleFormValues);
    expect(p.oracleSupplierCode).toBeNull();
    expect(p.oracleInvoice).toBeNull();
    expect(p.refAccount).toBeNull();
  });

  it('edit: always sends the three fields (trim-or-null), same convention as supplierLotNo', () => {
    const p = toEditPayload({
      lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605',
      oracleSupplierCode: '  X  ', oracleInvoice: '', refAccount: undefined,
    } as CycleEditFormValues);
    expect(p.oracleSupplierCode).toBe('X');
    expect(p.oracleInvoice).toBeNull();
    expect(p.refAccount).toBeNull();
  });
});

describe('Oracle reference fields — validation (>255 chars rejected)', () => {
  it.each(['oracleSupplierCode', 'oracleInvoice', 'refAccount'])(
    'create schema rejects %s over 255 characters',
    (field) => {
      const r = cycleFormSchema.safeParse({
        poNumber: '', pCode: 'C', cycleLabel: 'L', lotMode: 'auto', variety: 'พริกขี้หนู',
        [field]: 'X'.repeat(256),
      });
      expect(r.success).toBe(false);
    },
  );

  it.each(['oracleSupplierCode', 'oracleInvoice', 'refAccount'])(
    'create schema accepts exactly 255 characters for %s',
    (field) => {
      const r = cycleFormSchema.safeParse({
        poNumber: '', pCode: 'C', cycleLabel: 'L', lotMode: 'auto', variety: 'พริกขี้หนู',
        [field]: 'X'.repeat(255),
      });
      expect(r.success).toBe(true);
    },
  );

  it('edit schema also rejects over 255 characters', () => {
    const r = cycleEditFormSchema.safeParse({
      lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: 'L',
      oracleInvoice: 'X'.repeat(256),
    });
    expect(r.success).toBe(false);
  });
});

describe('StartCycleModal / RolloverCycleModal / ReactivatePlotWithCycleModal — Oracle fields (round 8-21B)', () => {
  it('Start: trims a filled value and sends null for the other two', async () => {
    createPlotCycleMock.mockReset();
    createPlotCycleMock.mockResolvedValue(legacyCycle());
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <StartCycleModal plotId="p1" supplierCode="SUP010" onClose={vi.fn()} onSaved={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: '2605' } },
    );
    await pickVariety();
    fireEvent.change(
      screen.getByPlaceholderText('เช่น ORC-SUP-001'), { target: { value: '  ORC-1  ' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'เริ่มรอบปลูก' }));

    await waitFor(() => expect(createPlotCycleMock).toHaveBeenCalledTimes(1));
    const [, payload] = createPlotCycleMock.mock.calls[0];
    expect(payload.oracleSupplierCode).toBe('ORC-1');
    expect(payload.oracleInvoice).toBeNull();
    expect(payload.refAccount).toBeNull();
  });

  it('Rollover: the new cycle carries the trimmed Oracle values', async () => {
    rolloverPlotCycleMock.mockReset();
    rolloverPlotCycleMock.mockResolvedValue({
      plotId: 'p1', activeCycleNo: 2, closedCycle: legacyCycle(), newCycle: legacyCycle({ cycleNo: 2 }),
    });
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <RolloverCycleModal plotId="p1" supplierCode="SUP010" cycle={legacyCycle()} onClose={vi.fn()} onSaved={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'jul2026' } },
    );
    await pickVariety();
    fireEvent.change(
      screen.getByPlaceholderText('เช่น INV-2026-0001'), { target: { value: '  INV-9  ' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'ยืนยันจบรอบ + เริ่มรอบใหม่' }));

    await waitFor(() => expect(rolloverPlotCycleMock).toHaveBeenCalledTimes(1));
    const [, , payload] = rolloverPlotCycleMock.mock.calls[0];
    expect(payload.newCycle.oracleInvoice).toBe('INV-9');
    expect(payload.newCycle.oracleSupplierCode).toBeNull();
  });

  it('Reactivate: submits the trimmed Ref Account value', async () => {
    reactivatePlotWithCycleMock.mockReset();
    reactivatePlotWithCycleMock.mockResolvedValue({ plot: {}, cycle: legacyCycle() });
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ReactivatePlotWithCycleModal plotId="p1" supplierCode="SUP010" onClose={vi.fn()} onSaved={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.change(
      screen.getByPlaceholderText('เช่น jun2026 หรือ may2026'), { target: { value: 'aug2026' } },
    );
    await pickVariety();
    fireEvent.change(
      screen.getByPlaceholderText('เช่น ACC-0001'), { target: { value: '  ACC-9  ' } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'เปิดใช้งานและเริ่มรอบปลูก' }));

    await waitFor(() => expect(reactivatePlotWithCycleMock).toHaveBeenCalledTimes(1));
    const [, payload] = reactivatePlotWithCycleMock.mock.calls[0];
    expect(payload.refAccount).toBe('ACC-9');
  });
});

describe('EditCycleModal — Oracle reference fields (round 8-21B)', () => {
  it('prefills all three stored values', () => {
    renderEdit(legacyCycle({
      oracleSupplierCode: 'ORC-SUP-7', oracleInvoice: 'INV-7', refAccount: 'ACC-7',
    }));
    expect((screen.getByPlaceholderText('เช่น ORC-SUP-001') as HTMLInputElement).value).toBe('ORC-SUP-7');
    expect((screen.getByPlaceholderText('เช่น INV-2026-0001') as HTMLInputElement).value).toBe('INV-7');
    expect((screen.getByPlaceholderText('เช่น ACC-0001') as HTMLInputElement).value).toBe('ACC-7');
  });

  it('shows the clear-oriented helper text in edit mode', () => {
    renderEdit(legacyCycle());
    expect(screen.getByText('ไม่บังคับ — เว้นว่างเพื่อลบค่าของรอบนี้')).toBeTruthy();
  });

  it('leaving the prefilled values untouched re-sends the same values (preserve via resend)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ oracleSupplierCode: 'ORC-SUP-7' }));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    expect(updatePlotCycleMock.mock.calls[0][2].oracleSupplierCode).toBe('ORC-SUP-7');
  });

  it('emptying a prefilled box clears it (sends null)', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    renderEdit(legacyCycle({ oracleSupplierCode: 'ORC-SUP-7', oracleInvoice: 'INV-7', refAccount: 'ACC-7' }));

    fireEvent.change(screen.getByPlaceholderText('เช่น ORC-SUP-001'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    const payload = updatePlotCycleMock.mock.calls[0][2];
    expect(payload.oracleSupplierCode).toBeNull();
    // Untouched siblings still round-trip their own current value.
    expect(payload.oracleInvoice).toBe('INV-7');
    expect(payload.refAccount).toBe('ACC-7');
  });
});

describe('CyclePlanFields — round 8-26C: P.Code derives from the พันธุ์', () => {
  it('renders P.Code read-only — it can never be typed into', async () => {
    renderPlan();

    const input = await screen.findByLabelText('P.Code');
    expect((input as HTMLInputElement).readOnly).toBe(true);
  });

  it('fills P.Code from the chosen พันธุ์', async () => {
    renderPlan();

    fireEvent.change(screen.getByTestId('master-select-variety'), { target: { value: 'พริกขี้หนู' } });

    await waitFor(() =>
      expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe('WM-141'),
    );
  });

  it('clears P.Code when the พันธุ์ is cleared', async () => {
    renderPlan();
    await pickVariety();

    fireEvent.change(screen.getByTestId('master-select-variety'), { target: { value: '' } });

    await waitFor(() =>
      expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe(''),
    );
  });

  it('clears P.Code when the ชนิดพืช changes, because that clears the พันธุ์', async () => {
    renderPlan();
    await pickVariety();

    fireEvent.change(screen.getByTestId('master-select-crop'), { target: { value: 'พริก' } });

    await waitFor(() =>
      expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe(''),
    );
  });

  it('warns, and leaves P.Code blank, for a พันธุ์ that has no P.Code yet', async () => {
    renderPlan();

    fireEvent.change(screen.getByTestId('master-select-variety'), { target: { value: 'พริกไม่มีรหัส' } });

    expect(await screen.findByText(/พันธุ์นี้ยังไม่ได้กำหนด P.Code/)).toBeTruthy();
    expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe('');
  });

  it('marks พันธุ์ as required on create', async () => {
    renderPlan();

    expect(await screen.findByText('พันธุ์/สายพันธุ์ *')).toBeTruthy();
  });

  it('does NOT mark พันธุ์ required on edit — a legacy cycle without one stays editable', async () => {
    renderPlan({ mode: 'edit' });

    expect(await screen.findByText('พันธุ์/สายพันธุ์')).toBeTruthy();
    expect(screen.queryByText('พันธุ์/สายพันธุ์ *')).toBeNull();
  });

  it('leaves a stored P.Code untouched on edit until the user actually picks a พันธุ์', async () => {
    // The legacy case: the form opens with a free-text P.Code that is not in
    // Master Data. Deriving on mount would silently rewrite it — and change
    // the Lot No a regenerate produces — for data the user never touched.
    renderPlan({ mode: 'edit' });

    await waitFor(() =>
      expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe('WM-141'),
    );
    expect(listMasterDataMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'p_code' }),
    );
  });

  it('derives on edit once the user does pick a พันธุ์', async () => {
    renderPlan({ mode: 'edit' });

    fireEvent.change(screen.getByTestId('master-select-variety'), { target: { value: 'พริกไม่มีรหัส' } });

    await waitFor(() =>
      expect((screen.getByLabelText('P.Code') as HTMLInputElement).value).toBe(''),
    );
  });
});

describe('cycleFormSchema — round 8-26C: พันธุ์ required on create', () => {
  it('rejects a create with no variety', () => {
    const r = cycleFormSchema.safeParse({
      poNumber: '', pCode: 'WM-141', cycleLabel: '2605', lotMode: 'auto',
    });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('variety');
  });

  it('rejects a whitespace-only variety', () => {
    const r = cycleFormSchema.safeParse({
      poNumber: '', pCode: 'WM-141', cycleLabel: '2605', lotMode: 'auto', variety: '   ',
    });
    expect(r.success).toBe(false);
  });

  it('the EDIT schema still accepts a blank variety', () => {
    const r = cycleEditFormSchema.safeParse({
      lotMode: 'keep', poNumber: '', pCode: '', cycleLabel: '2605', variety: '',
    });
    expect(r.success).toBe(true);
  });
});
