/**
 * MasterData — rounds 8-14E / 8-14E.1: hides 4 unused categories (level,
 * severity, irrigation, fertilizer) from the admin tab list. Frontend-only
 * — no DB mutation, no change to the masterdata API. See MasterData.tsx's
 * MD_TYPES comment for the consumer audit that justified this removal.
 *
 * Round 8-15B adds the crop/variety Excel import entry points. The import
 * modal itself is stubbed here (its own behavior — Preview/Commit/error
 * handling — is fully covered by MasterDataCropVarietyImportModal.test.tsx)
 * so these tests focus on the INTEGRATION boundary this page owns:
 * permission-gated buttons, opening/closing the modal, the Template
 * download, and invalidating ['masterdata'] on a successful import.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MasterData } from './MasterData';
import { masterDataQueryKey, type MasterDataItem } from '../../../api/masterdata';

const listMasterDataMock = vi.fn();
const createMasterDataMock = vi.fn();
const updateMasterDataMock = vi.fn();
const deleteMasterDataMock = vi.fn();
const downloadCropVarietyImportTemplateMock = vi.fn();

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return {
    ...actual,
    listMasterData: (...args: unknown[]) => listMasterDataMock(...args),
    createMasterData: (...args: unknown[]) => createMasterDataMock(...args),
    updateMasterData: (...args: unknown[]) => updateMasterDataMock(...args),
    deleteMasterData: (...args: unknown[]) => deleteMasterDataMock(...args),
    downloadCropVarietyImportTemplate: (...args: unknown[]) => downloadCropVarietyImportTemplateMock(...args),
  };
});

// Stub — MasterDataCropVarietyImportModal's own Preview/Commit/error-
// handling behavior is covered exhaustively in its own test file. This
// stub exposes just enough surface (onImported/onClose) to test how
// MasterData.tsx WIRES the modal, without re-driving its internal flow.
vi.mock('../../../components/farmlog/MasterDataCropVarietyImportModal', () => ({
  MasterDataCropVarietyImportModal: ({ onImported, onClose }: { onImported: () => void; onClose: () => void }) => (
    <div data-testid="import-modal-stub">
      <button type="button" onClick={onImported}>fake-import-success</button>
      <button type="button" onClick={onClose}>fake-close</button>
    </div>
  ),
}));

// null = every permission allowed (default for most tests below).
let allowedPerms: Set<string> | null = null;
vi.mock('../../../hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (allowedPerms === null ? true : allowedPerms.has(key)),
}));

function masterDataItem(overrides: Partial<MasterDataItem> = {}): MasterDataItem {
  return {
    id: 'md-1', type: 'crop', value: 'ข้าวโพด', parent: null, orderIndex: 0, active: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function renderPage(qc: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MasterData />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  allowedPerms = null;
  listMasterDataMock.mockReset();
  createMasterDataMock.mockReset();
  updateMasterDataMock.mockReset();
  deleteMasterDataMock.mockReset();
  downloadCropVarietyImportTemplateMock.mockReset();
  listMasterDataMock.mockResolvedValue([masterDataItem()]);
});

describe('MasterData — rounds 8-14E/8-14E.1: hides 4 categories (level/severity/irrigation/fertilizer)', () => {
  it('does not render the ระดับ (เตรียม/ดูแล) tab', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByText('ระดับ (เตรียม/ดูแล)')).toBeNull();
  });

  it('does not render the ระดับความรุนแรง tab', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByText('ระดับความรุนแรง')).toBeNull();
  });

  it('does not render the การให้น้ำ tab', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByText('การให้น้ำ')).toBeNull();
  });

  it('does not render the ปุ๋ย tab (round 8-14E.1)', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByText('ปุ๋ย')).toBeNull();
  });

  it('never requests type level, severity, irrigation, or fertilizer on initial load', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    const requestedTypes = listMasterDataMock.mock.calls.map((args) => (args[0] as { type?: string }).type);
    expect(requestedTypes).not.toContain('level');
    expect(requestedTypes).not.toContain('severity');
    expect(requestedTypes).not.toContain('irrigation');
    expect(requestedTypes).not.toContain('fertilizer');
  });
});

describe('MasterData — rounds 8-14E/8-14E.1: remaining tabs still visible', () => {
  const remainingLabels = [
    'ชนิดพืช',
    'พันธุ์/สายพันธุ์',
    'P.Code',
    'ระยะการเจริญเติบโต',
    'สภาพอากาศ',
    'จังหวัด',
  ];

  it.each(remainingLabels)('renders the %s tab', async (label) => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.getByText(label)).toBeTruthy();
  });
});

describe('MasterData — rounds 8-14E/8-14E.1: tab selection + CRUD regression', () => {
  it('switching to a remaining tab refetches with that type', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');
    listMasterDataMock.mockClear();
    listMasterDataMock.mockResolvedValue([masterDataItem({ id: 'md-2', type: 'weather', value: 'แดดจัด' })]);

    fireEvent.click(screen.getByText('สภาพอากาศ'));

    await screen.findByText('แดดจัด');
    expect(listMasterDataMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'weather' }));
  });

  it('creates an item under the active (remaining) type', async () => {
    createMasterDataMock.mockResolvedValue(masterDataItem({ id: 'md-3', value: 'มะม่วง' }));
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });

    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'มะม่วง' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createMasterDataMock).toHaveBeenCalledOnce());
    const [payload] = createMasterDataMock.mock.calls[0];
    expect(payload).toMatchObject({ type: 'crop', value: 'มะม่วง' });
  });

  it('deletes an item on the active (remaining) type after confirmation', async () => {
    deleteMasterDataMock.mockResolvedValue(undefined);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    await screen.findByText('ข้าวโพด');

    // Round 8-25F — the row's actions live behind one ActionMenu trigger now.
    fireEvent.click(screen.getByTitle('ตัวเลือกเพิ่มเติมสำหรับ ข้าวโพด'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'ลบ' }));

    await waitFor(() => expect(deleteMasterDataMock).toHaveBeenCalledWith('md-1'));
  });

  it('hides create/edit/delete actions without permission, on a remaining tab', async () => {
    allowedPerms = new Set();
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByRole('button', { name: 'เพิ่มตัวเลือก' })).toBeNull();
    // With neither permission the items list is empty, so ActionMenu renders
    // nothing at all — no trigger to open, not merely an empty menu.
    expect(screen.queryByTitle('ตัวเลือกเพิ่มเติมสำหรับ ข้าวโพด')).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'แก้ไข' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'ลบ' })).toBeNull();
  });
});

describe('MasterData — round 8-15B: crop/variety import entry points', () => {
  it('shows the Template download button for a masterdata.read-only user (no import access)', async () => {
    allowedPerms = new Set(['masterdata.read']);
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.getByRole('button', { name: /ดาวน์โหลด Template/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /นำเข้า Excel/ })).toBeNull();
  });

  it('hides the Template download button without masterdata.read', async () => {
    allowedPerms = new Set();
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByRole('button', { name: /ดาวน์โหลด Template/ })).toBeNull();
  });

  it('hides the Import Excel button when the user has ONLY masterdata.create (missing update)', async () => {
    allowedPerms = new Set(['masterdata.read', 'masterdata.create']);
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByRole('button', { name: /นำเข้า Excel/ })).toBeNull();
  });

  it('hides the Import Excel button when the user has ONLY masterdata.update (missing create)', async () => {
    allowedPerms = new Set(['masterdata.read', 'masterdata.update']);
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.queryByRole('button', { name: /นำเข้า Excel/ })).toBeNull();
  });

  it('shows the Import Excel button only when BOTH masterdata.create AND masterdata.update are granted', async () => {
    allowedPerms = new Set(['masterdata.read', 'masterdata.create', 'masterdata.update']);
    renderPage();
    await screen.findByText('ข้าวโพด');

    expect(screen.getByRole('button', { name: /นำเข้า Excel/ })).toBeTruthy();
  });

  it('the Template button downloads the template independently of the import modal', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL');
    const blob = new Blob(['xlsx']);
    downloadCropVarietyImportTemplateMock.mockResolvedValue({ blob, filename: 'crop-variety-import-template.xlsx' });
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลด Template/ }));

    await waitFor(() => expect(downloadCropVarietyImportTemplateMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(blob));
    // Never opened the modal for a plain template download.
    expect(screen.queryByTestId('import-modal-stub')).toBeNull();
  });

  it('opens the import modal on click and closes it via onClose', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: /นำเข้า Excel/ }));
    expect(await screen.findByTestId('import-modal-stub')).toBeTruthy();

    fireEvent.click(screen.getByText('fake-close'));
    expect(screen.queryByTestId('import-modal-stub')).toBeNull();
  });

  it('a successful import (onImported) invalidates the masterdata query so tabs see the latest data', async () => {
    renderPage();
    await screen.findByText('ข้าวโพด');
    listMasterDataMock.mockClear();
    listMasterDataMock.mockResolvedValue([masterDataItem({ id: 'md-new', value: 'มังคุด' })]);

    fireEvent.click(screen.getByRole('button', { name: /นำเข้า Excel/ }));
    await screen.findByTestId('import-modal-stub');
    fireEvent.click(screen.getByText('fake-import-success'));

    // The active (crop) tab refetches and shows the post-import data —
    // proves ['masterdata'] was actually invalidated, not just that a
    // callback fired.
    await screen.findByText('มังคุด');
    expect(listMasterDataMock).toHaveBeenCalled();
  });

  it('existing CRUD (create/toggle/delete) is unaffected by the import entry points', async () => {
    createMasterDataMock.mockResolvedValue(masterDataItem({ id: 'md-3', value: 'ทุเรียน' }));
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });
    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'ทุเรียน' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createMasterDataMock).toHaveBeenCalledOnce());
  });
});

describe('MasterData — round 8-22A: query-key contract + duplicate-value error handling', () => {
  it('shows inactive items even when the active-only selector cache is already populated under a different key', async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } },
    });
    // Simulates MasterDataSelect/MasterDataButtons/Plots' crop filter having
    // already cached the active-only crop list (no inactive rows) under
    // THEIR key before this admin page (all statuses) mounts.
    qc.setQueryData(masterDataQueryKey('crop', null, true), [masterDataItem({ value: 'ข้าวโพด' })]);
    listMasterDataMock.mockResolvedValue([
      masterDataItem({ id: 'md-1', value: 'ข้าวโพด', active: true }),
      masterDataItem({ id: 'md-2', value: 'ทุเรียน', active: false }),
    ]);

    renderPage(qc);

    // A distinct query key means the admin page's own (all-status) fetch
    // runs — proving it never reused the active-only cache entry above,
    // which would have hidden ทุเรียน.
    await screen.findByText('ทุเรียน');
    expect(screen.getByText('ข้าวโพด')).toBeTruthy();
  });

  it('shows the backend\'s specific duplicate-value message on a 409, not a generic axios message', async () => {
    createMasterDataMock.mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed with status code 409',
      response: { status: 409, data: { detail: 'มี "ข้าวโพด" อยู่แล้วในระบบ' } },
    });
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });
    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'ข้าวโพด' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await screen.findByText('มี "ข้าวโพด" อยู่แล้วในระบบ');
    expect(screen.queryByText('Request failed with status code 409')).toBeNull();
  });

  it('shows a distinct message for a duplicate-but-inactive value, suggesting reactivation', async () => {
    createMasterDataMock.mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: { detail: 'มี "ข้าวโพด" อยู่แล้วแต่ถูกปิดใช้งานอยู่ — กรุณาเปิดใช้งานรายการเดิมแทนการสร้างรายการใหม่' },
      },
    });
    renderPage();
    await screen.findByText('ข้าวโพด');

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });
    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'ข้าวโพด' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await screen.findByText(/ปิดใช้งานอยู่.*เปิดใช้งานรายการเดิม/);
  });
});

describe('MasterData — round 8-26A: the P.Code tab', () => {
  async function openPCodeTab() {
    renderPage();
    await screen.findByText('ข้าวโพด');
    listMasterDataMock.mockClear();
    listMasterDataMock.mockResolvedValue([
      masterDataItem({ id: 'md-pc', type: 'p_code', value: 'WM-111', parent: 'พริกขี้หนู' }),
    ]);
    fireEvent.click(screen.getByText('P.Code'));
    await screen.findByText('WM-111');
  }

  it('fetches type=p_code when the tab is selected', async () => {
    await openPCodeTab();

    expect(listMasterDataMock).toHaveBeenCalledWith(expect.objectContaining({ type: 'p_code' }));
  });

  it('shows the owning variety in the อยู่ภายใต้ column — not the crop', async () => {
    await openPCodeTab();

    expect(screen.getByText('อยู่ภายใต้')).toBeTruthy();
    expect(screen.getByText('พริกขี้หนู')).toBeTruthy();
  });

  it('the add form offers varieties as the parent, so a P.Code is filed under a variety', async () => {
    await openPCodeTab();

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });

    // The parent picker is a MasterDataSelect bound to parentType — its label
    // names the type it lists, which must be variety (crop → variety → p_code).
    expect(screen.getByText('อยู่ภายใต้ (variety)')).toBeTruthy();
  });

  it('creates with type p_code and the chosen variety as parent', async () => {
    createMasterDataMock.mockResolvedValue(
      masterDataItem({ id: 'md-pc2', type: 'p_code', value: 'WM-999', parent: 'พริกจินดา' }),
    );
    await openPCodeTab();

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });
    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'WM-999' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createMasterDataMock).toHaveBeenCalledOnce());
    const [payload] = createMasterDataMock.mock.calls[0];
    expect(payload).toMatchObject({ type: 'p_code', value: 'WM-999' });
  });

  it("surfaces the backend's 422 when a variety already owns an active P.Code", async () => {
    createMasterDataMock.mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed with status code 422',
      response: {
        status: 422,
        data: { detail: 'พันธุ์ "พริกขี้หนู" มี P.Code "WM-111" อยู่แล้ว — 1 พันธุ์มีได้เพียง 1 P.Code กรุณาปิดใช้งานรายการเดิมก่อน' },
      },
    });
    await openPCodeTab();

    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มตัวเลือก' }));
    await screen.findByRole('heading', { name: 'เพิ่มตัวเลือก' });
    const [valueInput] = screen.getAllByRole('textbox');
    fireEvent.change(valueInput, { target: { value: 'WM-999' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await screen.findByText(/1 พันธุ์มีได้เพียง 1 P.Code/);
    expect(screen.queryByText('Request failed with status code 422')).toBeNull();
  });
});
