/**
 * PlotCycleModals — round 8-5B PO/P.Code, and the round-A locked lot UX. Unit
 * tests for the exported payload/preview/badge helpers (the deterministic core
 * of the form logic) plus render tests that the system Lot No is display-only:
 * a preview before the cycle exists, the stored value after. The
 * create/start/rollover integration flows are covered by Plots.test.tsx /
 * PlotDetail.test.tsx.
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
  cyclePlanFields,
  cycleEditPlanFields,
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
  return { poNumber: 'po25001', pCode: 'Melon-A', cycleLabel: '2605' } as CycleFormValues;
}

describe('toPayload (CREATE) — round 8-5B', () => {
  it('always sends trimmed PO (as entered) + pCode, and no lot at all', () => {
    const p = toPayload({ ...base(), poNumber: '  po25001 ', pCode: '  Melon-A ' });
    expect(p.poNumber).toBe('po25001');
    expect(p.pCode).toBe('Melon-A');
    // Round A — the server generates the lot; the client never names it, not
    // even as an explicit null.
    expect(p).not.toHaveProperty('lotNo');
    // never leaks the server-derived fields either
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });
});

describe('toEditPayload (EDIT) — preserve / regenerate semantics', () => {
  function editBase(): CycleEditFormValues {
    return { poNumber: '', pCode: '', cycleLabel: '2605' } as CycleEditFormValues;
  }

  it('never sends a lot in any form — the stored one is immutable', () => {
    // Round A — not omitted-because-unchanged, but structurally absent: there
    // is no mode, no input and no key. Changing the very fields the lot was
    // built from makes no difference.
    const p = toEditPayload({ ...editBase(), cycleLabel: '26-may', pCode: 'WM-999' });
    expect(Object.prototype.hasOwnProperty.call(p, 'lotNo')).toBe(false);
    expect(p.cycleLabel).toBe('26-may');
    expect(p.pCode).toBe('WM-999');
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
    const p = toEditPayload({ ...editBase() });
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
    expect(cycleFormSchema.safeParse({ poNumber: '', pCode: '', cycleLabel: '2605', variety: 'พริกขี้หนู' }).success).toBe(false);
    expect(cycleEditFormSchema.safeParse({ poNumber: '', pCode: '', cycleLabel: '2605' }).success).toBe(true);
  });
  it('round 8-13B: CREATE accepts a blank PO alone, as long as pCode is present', () => {
    const r = cycleFormSchema.safeParse({
      poNumber: '', pCode: 'X', cycleLabel: '2605', variety: 'พริกขี้หนู',
    });
    expect(r.success).toBe(true);
  });
  it('round A: neither schema has a lot field to fill in', () => {
    expect('lotMode' in cyclePlanFields).toBe(false);
    expect('lotNo' in cyclePlanFields).toBe(false);
    expect('lotMode' in cycleEditPlanFields).toBe(false);
    expect('lotNo' in cycleEditPlanFields).toBe(false);
    // the SUPPLIER's own lot number is untouched and still editable
    expect('supplierLotNo' in cyclePlanFields).toBe(true);
  });
});

// --- render: the segmented control drives the lot input --------------------

function Harness({ supplierCode = 'SUP010', mode = 'create' as const }: {
  supplierCode?: string; mode?: 'create' | 'edit';
}) {
  const { register, watch, setValue, formState: { errors } } = useForm<CycleFormValues>({
    resolver: zodResolver(cycleFormSchema),
    defaultValues: {
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
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

describe('CyclePlanFields — the system lot is generated, never typed (create)', () => {
  it('shows the V2 preview of what the server will mint, with no input at all', () => {
    renderPlan();
    expect(screen.getByText('2605-SUP010-WM-141-###')).toBeTruthy();
    expect(screen.queryByPlaceholderText('เช่น LOT-01')).toBeNull();
  });

  it('round A: the Auto/Manual control is gone — there is no way to type a lot', () => {
    renderPlan();
    expect(screen.queryByRole('button', { name: 'กรอก Lot เอง' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'สร้างอัตโนมัติ' })).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น LOT-01')).toBeNull();
  });

  it('the Supplier Lot No input is separate from it, and stays editable', () => {
    renderPlan();
    const input = screen.getByPlaceholderText('เช่น SUP-LOT-A123') as HTMLInputElement;
    expect(input.readOnly).toBe(false);
    expect(input.disabled).toBe(false);
  });

  it('labels the system lot so it is not confused with the supplier lot', () => {
    renderPlan();
    expect(screen.getByText('Lot No ระบบ')).toBeTruthy();
    expect(screen.getByText('Supplier Lot No')).toBeTruthy();
  });
});

// --- round 8-12B: the Auto Lot components. Round A — required on CREATE (a
// lot is minted there) but NOT on EDIT, where a blank P.Code is back to
// meaning "preserve the stored one" because nothing is minted. -------------

describe('refineEditCyclePlan — an edit requires cycleLabel, never the lot components', () => {
  it('edit + blank cycleLabel -> rejected, naming ONLY the label', () => {
    const r = cycleEditFormSchema.safeParse({ poNumber: '', pCode: '' });
    expect(r.success).toBe(false);
    if (!r.success) {
      const paths = r.error.issues.map((i) => i.path[0]);
      expect(paths).toContain('cycleLabel');
      // Round A — a blank P.Code is a PRESERVE, not an error: an edit mints no
      // lot, and a legacy cycle may never have had one to begin with.
      expect(paths).not.toContain('pCode');
      // the retired PO rule must be gone too
      expect(paths).not.toContain('poNumber');
    }
  });

  it('edit + a PO but no cycleLabel -> STILL rejected (a PO is not a label)', () => {
    const r = cycleEditFormSchema.safeParse({ poNumber: 'PO25001', pCode: '' });
    expect(r.success).toBe(false);
  });

  it('edit + cycleLabel + pCode and NO PO -> ok (V2 never needs the PO)', () => {
    const r = cycleEditFormSchema.safeParse({
      poNumber: '', pCode: 'WM-141', cycleLabel: '2605',
    });
    expect(r.success).toBe(true);
  });

  it('edit + blank cycleLabel -> rejected (round 8-17A.1: required in every edit)', () => {
    expect(cycleEditFormSchema.safeParse({ poNumber: '', pCode: '' }).success).toBe(false);
  });

  it('edit + blank everything ELSE but cycleLabel present -> ok (preserve still works for pCode/PO)', () => {
    expect(
      cycleEditFormSchema.safeParse({ poNumber: '', pCode: '', cycleLabel: '2605' }).success,
    ).toBe(true);
  });
});

describe('cycleFormSchema (create) — Auto requires cycleLabel + P.Code', () => {
  // variety is required on CREATE since round 8-26C (P.Code derives from it).
  const base = { poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605', variety: 'พริกขี้หนู' };

  it('all components -> ok', () => {
    expect(cycleFormSchema.safeParse({ ...base }).success).toBe(true);
  });

  it('missing cycleLabel -> blocked', () => {
    const r = cycleFormSchema.safeParse({ ...base, cycleLabel: '' });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('cycleLabel');
  });

  it('missing pCode -> blocked', () => {
    const r = cycleFormSchema.safeParse({ ...base, pCode: '' });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('pCode');
  });

  it('round A: the lot components are required unconditionally — there is no mode that skips them', () => {
    // Before this round a hand-typed lot exempted the row from needing them.
    const r = cycleFormSchema.safeParse({ ...base, cycleLabel: '', pCode: '' });
    expect(r.success).toBe(false);
    if (!r.success) {
      const paths = r.error.issues.map((i) => i.path[0]);
      expect(paths).toContain('cycleLabel');
      expect(paths).toContain('pCode');
    }
  });
});

describe('toEditPayload — PO handling, with the lot untouchable', () => {
  it('PO -> sent, and still no lot / source / running', () => {
    const p = toEditPayload({ poNumber: 'PO25001', pCode: '', cycleLabel: '2605' } as CycleEditFormValues);
    expect(p.poNumber).toBe('PO25001');
    expect(p).not.toHaveProperty('lotNo');
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });

  it('blank PO -> sent as null (CLEAR, never omitted); the lot stays absent', () => {
    const p = toEditPayload({ poNumber: '', pCode: '', cycleLabel: '2605' } as CycleEditFormValues);
    // round 8-13B: poNumber is ALWAYS sent (blank = clear), never omitted.
    expect(p).toHaveProperty('poNumber');
    expect(p.poNumber).toBeNull();
    expect(Object.prototype.hasOwnProperty.call(p, 'lotNo')).toBe(false);
  });
});

describe('supplierLotNo payloads', () => {
  it('create trims the value and never sends an internal series key', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      supplierLotNo: '  SUP-OWN-1  ',
    } as CycleFormValues);
    expect(p.supplierLotNo).toBe('SUP-OWN-1');
    expect(p).not.toHaveProperty('autoLotSeriesKey');
    expect(p).not.toHaveProperty('lotNoSource');
    expect(p).not.toHaveProperty('lotRunningNo');
  });

  it('create with a blank supplier lot sends null', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      supplierLotNo: '   ',
    } as CycleFormValues);
    expect(p.supplierLotNo).toBeNull();
  });

  it('the supplier lot never drags the system lot into the payload', () => {
    const p = toPayload({
      poNumber: 'P', pCode: 'C', cycleLabel: 'L', supplierLotNo: 'S-1',
    } as CycleFormValues);
    expect(p.supplierLotNo).toBe('S-1');
    expect(p).not.toHaveProperty('lotNo');
  });

  it('edit sends the supplier lot and still never the system lot', () => {
    const p = toEditPayload({
      poNumber: '', pCode: '', cycleLabel: '2605', supplierLotNo: 'S-9',
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

// Round 8-12B — V1 blocked "regenerate Auto Lot" without a PO; V2 needed a
// cycleLabel + P.Code instead. Round A retired regeneration itself: the lot is
// minted once, at cycle creation, so the edit form only DISPLAYS it. These
// tests assert that display, and that the components stay required for the
// reasons that outlived the lot mode (cycleLabel/P.Code still identify the
// cycle and still build the lot at CREATE time).
describe('EditCycleModal — the system lot is display-only', () => {
  it('shows the stored lot read-only, with no way to change or regenerate it', () => {
    updatePlotCycleMock.mockReset();
    renderEdit(legacyCycle());

    expect(screen.getByText('LEGACY-LOT')).toBeTruthy();
    expect(screen.getByText('ระบบสร้างให้ตอนเริ่มรอบปลูก — แก้ไขไม่ได้')).toBeTruthy();
    // none of the retired controls exist any more
    expect(screen.queryByRole('button', { name: 'สร้าง Auto Lot ใหม่' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'กรอก Lot เอง' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'เก็บ Lot เดิม' })).toBeNull();
    expect(screen.queryByPlaceholderText('เช่น LOT-01')).toBeNull();
  });

  it('tags a legacy lot with its source badge so an old hand-typed one is recognisable', () => {
    updatePlotCycleMock.mockReset();
    renderEdit(legacyCycle({ lotNo: 'HAND-01', lotNoSource: 'manual' }));
    expect(screen.getByText('HAND-01')).toBeTruthy();
    expect(screen.getByText('กรอกเอง')).toBeTruthy();
  });

  it('saving an edit never sends a lot, even when P.Code and label change', async () => {
    updatePlotCycleMock.mockReset();
    updatePlotCycleMock.mockResolvedValue(legacyCycle());
    const { onSaved } = renderEdit(legacyCycle({ pCode: 'WM-141', cycleLabel: '2605' }));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updatePlotCycleMock).toHaveBeenCalledTimes(1));
    const [, , payload] = updatePlotCycleMock.mock.calls[0];
    expect(payload).not.toHaveProperty('lotNo');
    expect(payload).not.toHaveProperty('lotNoSource');
    expect(payload).not.toHaveProperty('lotRunningNo');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('a blank cycleLabel still blocks the save (it identifies the cycle)', async () => {
    updatePlotCycleMock.mockReset();
    renderEdit(legacyCycle({ cycleLabel: null, pCode: 'WM-141' }));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    expect(
      await screen.findByText('กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ'),
    ).toBeTruthy();
    expect(updatePlotCycleMock).not.toHaveBeenCalled();
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
    // round A — the system lot is not part of an edit payload at all
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

    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required on every new-cycle form.
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

  it('blank PO + cycleLabel/pCode filled -> submits, preview never shows a PO', async () => {
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
    // Round A — the lot is absent from the payload entirely; the backend mints
    // it from the cycleLabel/pCode above.
    expect(payload).not.toHaveProperty('lotNo');
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

    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required on every new-cycle form.
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

    await pickVariety();
    // Round 8-17A.1 — cycleLabel is required on every new-cycle form.
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
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      oracleSupplierCode: '  ORC-SUP-1  ', oracleInvoice: '  INV-1  ', refAccount: '  ACC-1  ',
    } as CycleFormValues);
    expect(p.oracleSupplierCode).toBe('ORC-SUP-1');
    expect(p.oracleInvoice).toBe('INV-1');
    expect(p.refAccount).toBe('ACC-1');
  });

  it('create: blank/omitted fields all send null', () => {
    const p = toPayload({
      poNumber: 'PO25001', pCode: 'WM-141', cycleLabel: '2605',
      oracleSupplierCode: '   ',
    } as CycleFormValues);
    expect(p.oracleSupplierCode).toBeNull();
    expect(p.oracleInvoice).toBeNull();
    expect(p.refAccount).toBeNull();
  });

  it('edit: always sends the three fields (trim-or-null), same convention as supplierLotNo', () => {
    const p = toEditPayload({
      poNumber: '', pCode: '', cycleLabel: '2605',
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
        poNumber: '', pCode: 'C', cycleLabel: 'L', variety: 'พริกขี้หนู',
        [field]: 'X'.repeat(256),
      });
      expect(r.success).toBe(false);
    },
  );

  it.each(['oracleSupplierCode', 'oracleInvoice', 'refAccount'])(
    'create schema accepts exactly 255 characters for %s',
    (field) => {
      const r = cycleFormSchema.safeParse({
        poNumber: '', pCode: 'C', cycleLabel: 'L', variety: 'พริกขี้หนู',
        [field]: 'X'.repeat(255),
      });
      expect(r.success).toBe(true);
    },
  );

  it('edit schema also rejects over 255 characters', () => {
    const r = cycleEditFormSchema.safeParse({
      poNumber: '', pCode: '', cycleLabel: 'L',
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
      poNumber: '', pCode: 'WM-141', cycleLabel: '2605',
    });
    expect(r.success).toBe(false);
    if (!r.success) expect(r.error.issues.map((i) => i.path[0])).toContain('variety');
  });

  it('rejects a whitespace-only variety', () => {
    const r = cycleFormSchema.safeParse({
      poNumber: '', pCode: 'WM-141', cycleLabel: '2605', variety: '   ',
    });
    expect(r.success).toBe(false);
  });

  it('the EDIT schema still accepts a blank variety', () => {
    const r = cycleEditFormSchema.safeParse({
      poNumber: '', pCode: '', cycleLabel: '2605', variety: '',
    });
    expect(r.success).toBe(true);
  });
});
