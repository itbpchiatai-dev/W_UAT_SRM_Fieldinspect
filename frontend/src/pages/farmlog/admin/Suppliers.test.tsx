/**
 * Suppliers — round 8-3G retirement checks: the inspection-code gate
 * (column, form field, payload field) is gone from this admin page, while
 * Supplier status/deactivate stays fully intact (explicitly NOT retired —
 * see the round brief's "ไม่ได้สั่งลบปุ่ม 'ปิดใช้งาน Supplier'").
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Suppliers } from './Suppliers';
import { SupplierImportReportError, type SupplierDetail, type SupplierSummary } from '../../../api/suppliers';

const listSuppliersMock = vi.fn();
const searchSuppliersMock = vi.fn();
const getSupplierMock = vi.fn();
const createSupplierMock = vi.fn();
const updateSupplierMock = vi.fn();
const deactivateSupplierMock = vi.fn();
const downloadTemplateMock = vi.fn();

vi.mock('../../../api/suppliers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/suppliers')>();
  return {
    ...actual,
    listSuppliers: (...args: unknown[]) => listSuppliersMock(...args),
    searchSuppliers: (...args: unknown[]) => searchSuppliersMock(...args),
    getSupplier: (...args: unknown[]) => getSupplierMock(...args),
    createSupplier: (...args: unknown[]) => createSupplierMock(...args),
    updateSupplier: (...args: unknown[]) => updateSupplierMock(...args),
    deactivateSupplier: (...args: unknown[]) => deactivateSupplierMock(...args),
    downloadSupplierImportTemplate: (...args: unknown[]) => downloadTemplateMock(...args),
  };
});

const downloadBlobMock = vi.fn();
vi.mock('../../../lib/downloadBlob', () => ({
  downloadBlob: (...a: unknown[]) => downloadBlobMock(...a),
}));

// Round 8-20B — the modal itself has its own dedicated test file
// (SupplierImportModal.test.tsx); here it is stubbed down to the two
// callbacks this PAGE is responsible for wiring.
vi.mock('../../../components/farmlog/SupplierImportModal', () => ({
  SupplierImportModal: ({ onClose, onImported }: { onClose: () => void; onImported: () => void }) => (
    <div data-testid="supplier-import-modal">
      <button type="button" onClick={onClose}>close-import</button>
      <button type="button" onClick={onImported}>fire-imported</button>
    </div>
  ),
}));

// null = every permission allowed (default for most tests below).
let allowedPerms: Set<string> | null = null;
vi.mock('../../../hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (allowedPerms === null ? true : allowedPerms.has(key)),
}));

function supplierSummary(overrides: Partial<SupplierSummary> = {}): SupplierSummary {
  return {
    id: 'sup-1', code: 'SUP001', name: 'Supplier One', isActive: true,
    contactName: 'คุณสมชาย', contactEmail: 'somchai@example.com',
    ...overrides,
  };
}

function supplierDetail(overrides: Partial<SupplierDetail> = {}): SupplierDetail {
  return {
    id: 'sup-1', code: 'SUP001', name: 'Supplier One',
    taxId: null, contactName: 'คุณสมชาย', contactEmail: 'somchai@example.com',
    contactPhone: null, address: null, isActive: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function renderPage(qc: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Suppliers />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  allowedPerms = null;
  listSuppliersMock.mockReset();
  searchSuppliersMock.mockReset();
  getSupplierMock.mockReset();
  createSupplierMock.mockReset();
  updateSupplierMock.mockReset();
  deactivateSupplierMock.mockReset();
  downloadTemplateMock.mockReset();
  downloadBlobMock.mockReset();
  listSuppliersMock.mockResolvedValue([supplierSummary()]);
  // Round 8-20D — the page reads through POST /suppliers/search now; the GET
  // list endpoint stays exported for other callers and is still mocked above.
  searchSuppliersMock.mockResolvedValue([supplierSummary()]);
});

/** Round 8-25F — a row's แก้ไข/ปิดการใช้งาน actions live behind one
 * ActionMenu trigger now (matching the Plots admin table) instead of two
 * standalone icon buttons; open it by its per-row title before looking for
 * an action by name. */
async function openRowMenu(code = 'SUP001') {
  fireEvent.click(await screen.findByTitle(`ตัวเลือกเพิ่มเติมสำหรับ Supplier ${code}`));
}

describe('Suppliers — inspection-code retirement (round 8-3G)', () => {
  it('does not render an inspection-code table column', async () => {
    renderPage();
    await screen.findByText('SUP001');

    expect(screen.queryByText('รหัสตรวจแปลง')).toBeNull();
  });

  it('does not render an inspection-code field in the create form', async () => {
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /เพิ่ม Supplier/ }));
    await screen.findByText('เพิ่ม Supplier ใหม่');

    expect(screen.queryByText('รหัสเข้าตรวจแปลง')).toBeNull();
    expect(screen.queryByText(/รหัสเข้าตรวจแปลง/)).toBeNull();
    expect(screen.queryByPlaceholderText('1111')).toBeNull();
  });

  it('does not render an inspection-code field in the edit form', async () => {
    getSupplierMock.mockResolvedValue(supplierDetail());
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'แก้ไข' }));
    await screen.findByText('แก้ไข Supplier');
    await waitFor(() => expect(getSupplierMock).toHaveBeenCalledWith('sup-1'));

    expect(screen.queryByText(/รหัสเข้าตรวจแปลง/)).toBeNull();
  });

  it('create payload never carries inspectionCode', async () => {
    createSupplierMock.mockResolvedValue(supplierDetail());
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /เพิ่ม Supplier/ }));
    await screen.findByText('เพิ่ม Supplier ใหม่');

    fireEvent.change(screen.getByPlaceholderText('SUP001'), { target: { value: 'SUP002' } });
    fireEvent.change(screen.getByPlaceholderText('บริษัท ...'), { target: { value: 'Supplier Two' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createSupplierMock).toHaveBeenCalledOnce());
    const [payload] = createSupplierMock.mock.calls[0];
    expect(payload).not.toHaveProperty('inspectionCode');
  });

  it('edit payload never carries inspectionCode', async () => {
    getSupplierMock.mockResolvedValue(supplierDetail());
    updateSupplierMock.mockResolvedValue(supplierDetail());
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'แก้ไข' }));
    await screen.findByText('แก้ไข Supplier');
    await waitFor(() => expect(getSupplierMock).toHaveBeenCalledWith('sup-1'));

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(updateSupplierMock).toHaveBeenCalledOnce());
    const [, payload] = updateSupplierMock.mock.calls[0];
    expect(payload).not.toHaveProperty('inspectionCode');
  });
});

describe('Suppliers — status/deactivate unaffected (round 8-3G do-not-touch)', () => {
  it('shows the ปิดการใช้งาน action for an active supplier', async () => {
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    expect(screen.getByRole('menuitem', { name: 'ปิดการใช้งาน' })).toBeTruthy();
  });

  it('hides the ปิดการใช้งาน action for an already-inactive supplier', async () => {
    searchSuppliersMock.mockResolvedValue([supplierSummary({ isActive: false })]);
    renderPage();
    await screen.findByText('SUP001');

    // The trigger still renders (แก้ไข remains available) — only the
    // deactivate ITEM is gone, so open the menu before asserting absence.
    await openRowMenu();
    expect(screen.queryByRole('menuitem', { name: 'ปิดการใช้งาน' })).toBeNull();
    expect(screen.getByRole('menuitem', { name: 'แก้ไข' })).toBeTruthy();
    expect(screen.getByText('ปิดแล้ว')).toBeTruthy();
  });

  it('calls deactivateSupplier when confirmed', async () => {
    deactivateSupplierMock.mockResolvedValue(supplierDetail({ isActive: false }));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'ปิดการใช้งาน' }));

    await waitFor(() => expect(deactivateSupplierMock).toHaveBeenCalledWith('sup-1'));
  });

  it('does not call deactivateSupplier when the confirm dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'ปิดการใช้งาน' }));

    expect(deactivateSupplierMock).not.toHaveBeenCalled();
  });

  it('hides create/edit/deactivate actions without permission', async () => {
    allowedPerms = new Set();
    renderPage();
    await screen.findByText('SUP001');

    expect(screen.queryByRole('button', { name: /เพิ่ม Supplier/ })).toBeNull();
    // With neither permission the items list is empty, so ActionMenu renders
    // nothing at all — no trigger to open, not merely an empty menu.
    expect(screen.queryByTitle('ตัวเลือกเพิ่มเติมสำหรับ Supplier SUP001')).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'แก้ไข' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'ปิดการใช้งาน' })).toBeNull();
  });
});

describe('Suppliers — create/edit still work (round 8-3G regression)', () => {
  it('creates a supplier with the remaining contact/address fields', async () => {
    createSupplierMock.mockResolvedValue(supplierDetail());
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /เพิ่ม Supplier/ }));
    await screen.findByText('เพิ่ม Supplier ใหม่');

    fireEvent.change(screen.getByPlaceholderText('SUP001'), { target: { value: 'SUP002' } });
    fireEvent.change(screen.getByPlaceholderText('บริษัท ...'), { target: { value: 'Supplier Two' } });
    fireEvent.click(screen.getByRole('button', { name: 'สร้าง' }));

    await waitFor(() => expect(createSupplierMock).toHaveBeenCalledOnce());
    const [payload] = createSupplierMock.mock.calls[0];
    expect(payload).toMatchObject({ code: 'SUP002', name: 'Supplier Two' });
  });

  it('loads the existing supplier into the edit form', async () => {
    getSupplierMock.mockResolvedValue(supplierDetail({ name: 'Supplier One Co.' }));
    renderPage();
    await screen.findByText('SUP001');

    await openRowMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'แก้ไข' }));
    await screen.findByText('แก้ไข Supplier');

    expect(await screen.findByDisplayValue('Supplier One Co.')).toBeTruthy();
  });
});

// --- Round 8-20B: Excel import entry points --------------------------------

describe('Suppliers — Excel import entry points (round 8-20B)', () => {
  it('shows both import buttons for a caller with suppliers.read', async () => {
    renderPage();
    await screen.findByText('SUP001');

    expect(screen.getByRole('button', { name: /ดาวน์โหลด Template/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /นำเข้า Excel/ })).toBeTruthy();
  });

  it('hides both import buttons without suppliers.read', async () => {
    allowedPerms = new Set(['suppliers.create', 'suppliers.update']);
    renderPage();
    await screen.findByText('SUP001');

    expect(screen.queryByRole('button', { name: /ดาวน์โหลด Template/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /นำเข้า Excel/ })).toBeNull();
  });

  it('shows the import buttons for a read-only caller — the backend decides per row', async () => {
    // suppliers.read alone is enough to LOOK; Preview will mark any row the
    // caller may not execute as an ERROR row, and Commit will refuse. The
    // page must never pre-judge that.
    allowedPerms = new Set(['suppliers.read']);
    renderPage();
    await screen.findByText('SUP001');

    expect(screen.getByRole('button', { name: /ดาวน์โหลด Template/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /นำเข้า Excel/ })).toBeTruthy();
    // ...and the ordinary create button is still permission-gated as before.
    expect(screen.queryByRole('button', { name: /เพิ่ม Supplier/ })).toBeNull();
  });

  it('downloads the template through the API helper', async () => {
    downloadTemplateMock.mockResolvedValue({
      blob: new Blob(['x']), filename: 'supplier-import-template.xlsx',
    });
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลด Template/ }));

    await waitFor(() => expect(downloadTemplateMock).toHaveBeenCalled());
    expect(downloadBlobMock).toHaveBeenCalledWith(expect.any(Blob), 'supplier-import-template.xlsx');
  });

  it('shows a Thai message when the template download fails', async () => {
    downloadTemplateMock.mockRejectedValue(
      new SupplierImportReportError('ไม่มีสิทธิ์ดาวน์โหลด', 403),
    );
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /ดาวน์โหลด Template/ }));

    expect(await screen.findByText('ไม่มีสิทธิ์ดาวน์โหลด')).toBeTruthy();
  });

  it('opens the import modal and closes it again', async () => {
    renderPage();
    await screen.findByText('SUP001');
    expect(screen.queryByTestId('supplier-import-modal')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /นำเข้า Excel/ }));
    expect(await screen.findByTestId('supplier-import-modal')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'close-import' }));
    await waitFor(() => expect(screen.queryByTestId('supplier-import-modal')).toBeNull());
  });

  it('invalidates the supplier caches after a successful import', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    renderPage(qc);
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /นำเข้า Excel/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'fire-imported' }));

    await waitFor(() => {
      const keys = invalidate.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
      expect(keys).toContain(JSON.stringify(['suppliers']));
      expect(keys).toContain(JSON.stringify(['supplier']));
    });
  });

  it('does not nest the import modal inside the edit modal', async () => {
    renderPage();
    await screen.findByText('SUP001');

    fireEvent.click(screen.getByRole('button', { name: /นำเข้า Excel/ }));
    const modal = await screen.findByTestId('supplier-import-modal');

    // A sibling of the page content, never a descendant of the edit form.
    expect(modal.closest('form')).toBeNull();
  });
});

// --- Round 8-20D: contact/status filter row --------------------------------

describe('Suppliers — contact and status filters (round 8-20D)', () => {
  const NAME_CODE = 'ชื่อหรือรหัส Supplier';
  const CONTACT_NAME = 'ชื่อผู้ติดต่อ';
  const CONTACT_PHONE = 'หมายเลขติดต่อ';

  function lastSearchBody(): Record<string, unknown> {
    const calls = searchSuppliersMock.mock.calls;
    return calls[calls.length - 1][0] as Record<string, unknown>;
  }

  async function ready() {
    renderPage();
    await screen.findByText('SUP001');
  }

  /** For tests that swap in their own fixture before rendering — waits for a
   * code that fixture actually contains. */
  async function readyWith(code: string) {
    renderPage();
    await screen.findByText(code);
  }

  function apply() {
    fireEvent.click(screen.getByRole('button', { name: 'ค้นหา' }));
  }

  // --- the pre-existing name/code search --------------------------------

  it('keeps the existing ชื่อหรือรหัส search box', async () => {
    await ready();
    expect(screen.getByPlaceholderText('ค้นหาชื่อหรือรหัส...')).toBeTruthy();
    expect(screen.getByLabelText(NAME_CODE)).toBeTruthy();
  });

  it('still searches supplier code/name partially through q', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP0' } });
    apply();

    await waitFor(() => expect(lastSearchBody().q).toBe('SUP0'));
  });

  it('trims the name/code value before applying', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: '  SUP0  ' } });
    apply();

    await waitFor(() => expect(lastSearchBody().q).toBe('SUP0'));
  });

  // --- the two new text filters ------------------------------------------

  it('renders the contact-name and contact-number boxes', async () => {
    await ready();
    expect(screen.getByLabelText(CONTACT_NAME)).toBeTruthy();
    expect(screen.getByLabelText(CONTACT_PHONE)).toBeTruthy();
  });

  it('searches a partial contact name', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_NAME), { target: { value: 'สมชาย' } });
    apply();

    await waitFor(() => expect(lastSearchBody().contactName).toBe('สมชาย'));
  });

  it('searches a partial contact number', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    apply();

    await waitFor(() => expect(lastSearchBody().contactPhoneDigits).toBe('5552'));
  });

  it('strips non-digits from the contact-number box as they are typed', async () => {
    await ready();
    const input = screen.getByLabelText(CONTACT_PHONE) as HTMLInputElement;

    fireEvent.change(input, { target: { value: '084-555-2162' } });
    expect(input.value).toBe('0845552162');

    fireEvent.change(input, { target: { value: '55%2_' } });
    expect(input.value).toBe('552');
  });

  it('caps the contact-number box at 10 digits', async () => {
    await ready();
    const input = screen.getByLabelText(CONTACT_PHONE) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '08123456789012' } });
    expect(input.value).toBe('0812345678');
  });

  it('is numeric-friendly and never autofilled', async () => {
    await ready();
    const input = screen.getByLabelText(CONTACT_PHONE) as HTMLInputElement;
    expect(input.type).toBe('text');
    expect(input.inputMode).toBe('numeric');
    expect(input.autocomplete).toBe('off');
  });

  // --- validation ---------------------------------------------------------

  it('rejects a 3-digit fragment with a generic Thai message and sends nothing', async () => {
    await ready();
    searchSuppliersMock.mockClear();

    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '555' } });
    apply();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/4-10 หลัก/);
    // Never echoes the digits the user typed.
    expect(alert.textContent).not.toContain('555');
    expect(searchSuppliersMock).not.toHaveBeenCalled();
  });

  it('blocks the whole apply when the number is invalid, even with other filters set', async () => {
    await ready();
    searchSuppliersMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP0' } });
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '55' } });
    apply();

    await screen.findByRole('alert');
    expect(searchSuppliersMock).not.toHaveBeenCalled();
  });

  it('clears the validation message once the user edits again', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '555' } });
    apply();
    await screen.findByRole('alert');

    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('accepts a full 10-digit number (partial search is additive)', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '0845552162' } });
    apply();

    await waitFor(() => expect(lastSearchBody().contactPhoneDigits).toBe('0845552162'));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  // --- status -------------------------------------------------------------

  it('defaults the status filter to ใช้งาน and requests status=active', async () => {
    await ready();
    expect((screen.getByLabelText('สถานะ') as HTMLSelectElement).value).toBe('active');
    await waitFor(() => expect(lastSearchBody().status).toBe('active'));
  });

  it('can request the inactive suppliers', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'inactive' } });
    apply();

    await waitFor(() => expect(lastSearchBody().status).toBe('inactive'));
  });

  it('can request every supplier regardless of status', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'all' } });
    apply();

    await waitFor(() => expect(lastSearchBody().status).toBe('all'));
  });

  it('actually shows an inactive supplier when asked for one', async () => {
    await ready();
    searchSuppliersMock.mockResolvedValue([
      supplierSummary({ code: 'SUP900', name: 'Closed Supplier', isActive: false }),
    ]);
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'inactive' } });
    apply();

    expect(await screen.findByText('SUP900')).toBeTruthy();
  });

  // --- combined (AND) ------------------------------------------------------

  it('sends every filter together in one request body', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP' } });
    fireEvent.change(screen.getByLabelText(CONTACT_NAME), { target: { value: 'สมชาย' } });
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'all' } });
    apply();

    await waitFor(() => {
      const body = lastSearchBody();
      expect(body.q).toBe('SUP');
      expect(body.contactName).toBe('สมชาย');
      expect(body.contactPhoneDigits).toBe('5552');
      expect(body.status).toBe('all');
    });
  });

  it('omits filters that were left blank', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP' } });
    apply();

    await waitFor(() => {
      const body = lastSearchBody();
      expect(body.q).toBe('SUP');
      expect(body.contactName).toBeUndefined();
      expect(body.contactPhoneDigits).toBeUndefined();
    });
  });

  // --- apply semantics -----------------------------------------------------

  it('does not query until the filters are applied', async () => {
    await ready();
    searchSuppliersMock.mockClear();

    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'typing' } });
    fireEvent.change(screen.getByLabelText(CONTACT_NAME), { target: { value: 'typing' } });

    expect(searchSuppliersMock).not.toHaveBeenCalled();
  });

  it('applies on Enter from any filter box', async () => {
    await ready();
    const input = screen.getByLabelText(CONTACT_NAME);
    fireEvent.change(input, { target: { value: 'สมชาย' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(lastSearchBody().contactName).toBe('สมชาย'));
  });

  it('re-applying the SAME number fires a fresh request (nonce, not a cached page)', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    apply();
    await waitFor(() => expect(lastSearchBody().contactPhoneDigits).toBe('5552'));
    const first = searchSuppliersMock.mock.calls.length;

    apply();
    await waitFor(() => expect(searchSuppliersMock.mock.calls.length).toBeGreaterThan(first));
  });

  // --- PII containment ------------------------------------------------------

  it('never puts the contact number in the page URL', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    apply();
    await waitFor(() => expect(lastSearchBody().contactPhoneDigits).toBe('5552'));

    expect(window.location.search).not.toContain('5552');
    expect(window.location.href).not.toContain('5552');
  });

  it('never puts the contact number in a React Query key', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderPage(qc);
    await screen.findByText('SUP001');

    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    apply();
    await waitFor(() => expect(lastSearchBody().contactPhoneDigits).toBe('5552'));

    const keys = qc.getQueryCache().getAll().map((entry) => JSON.stringify(entry.queryKey));
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(key).not.toContain('5552');
    }
  });

  it('does not echo the searched number back on screen', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    apply();

    const note = await screen.findByText(/กำลังกรองตามหมายเลขติดต่อ/);
    expect(note.textContent).not.toContain('5552');
  });

  // --- clear ----------------------------------------------------------------

  it('ล้างค่า resets every filter and restores status=active', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP' } });
    fireEvent.change(screen.getByLabelText(CONTACT_NAME), { target: { value: 'สมชาย' } });
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'all' } });
    apply();
    await waitFor(() => expect(lastSearchBody().q).toBe('SUP'));

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));

    expect((screen.getByLabelText(NAME_CODE) as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(CONTACT_NAME) as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText(CONTACT_PHONE) as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('สถานะ') as HTMLSelectElement).value).toBe('active');

    await waitFor(() => {
      const body = lastSearchBody();
      expect(body.q).toBeUndefined();
      expect(body.contactName).toBeUndefined();
      expect(body.contactPhoneDigits).toBeUndefined();
      expect(body.status).toBe('active');
      expect(body.offset).toBe(0);
    });
  });

  it('ล้างค่า also clears a pending validation message', async () => {
    await ready();
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '555' } });
    apply();
    await screen.findByRole('alert');

    fireEvent.click(screen.getByRole('button', { name: 'ล้างค่า' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  // --- pagination ------------------------------------------------------------

  // Round 8-25D — the page-size selector's default is 100 (was a fixed 20),
  // so "a full page" now means 100 rows, not 20.
  it('returns to the first page whenever filters are applied', async () => {
    searchSuppliersMock.mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => supplierSummary({ id: `s${i}`, code: `SUP${i}` })),
    );
    await readyWith('SUP0');

    // Rendering the full 100-row page is heavy; allow extra time so this
    // doesn't flake under parallel suite load (it's fast in isolation) —
    // same reasoning as Plots.test.tsx's own chunked-render test.
    fireEvent.click(screen.getByRole('button', { name: /ถัดไป/ }));
    await waitFor(() => expect(lastSearchBody().offset).toBe(100), { timeout: 10000 });

    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP' } });
    apply();

    await waitFor(() => expect(lastSearchBody().offset).toBe(0));
  }, 15000);

  it('keeps the applied filters when paging', async () => {
    searchSuppliersMock.mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => supplierSummary({ id: `s${i}`, code: `SUP${i}` })),
    );
    await readyWith('SUP0');

    fireEvent.change(screen.getByLabelText(NAME_CODE), { target: { value: 'SUP' } });
    fireEvent.change(screen.getByLabelText(CONTACT_PHONE), { target: { value: '5552' } });
    fireEvent.change(screen.getByLabelText('สถานะ'), { target: { value: 'all' } });
    apply();
    await waitFor(() => expect(lastSearchBody().q).toBe('SUP'));
    // "ถัดไป" is disabled while a page has fewer than the selected page
    // size, which includes the moment the post-apply fetch is still in
    // flight — wait for the filtered page to actually render before paging.
    await screen.findByText('SUP0');
    // Rendering the full 100-row page is heavy; allow extra time so this
    // doesn't flake under parallel suite load (it's fast in isolation) —
    // same reasoning as Plots.test.tsx's own chunked-render test.
    await waitFor(() =>
      expect((screen.getByRole('button', { name: /ถัดไป/ }) as HTMLButtonElement).disabled).toBe(false),
      { timeout: 10000 },
    );

    fireEvent.click(screen.getByRole('button', { name: /ถัดไป/ }));

    await waitFor(() => {
      const body = lastSearchBody();
      expect(body.offset).toBe(100);
      expect(body.q).toBe('SUP');
      expect(body.contactPhoneDigits).toBe('5552');
      expect(body.status).toBe('all');
    });
  }, 15000);

  // --- layout ----------------------------------------------------------------

  it('lays the filter row out responsively without overflowing', async () => {
    await ready();
    const row = screen.getByLabelText(NAME_CODE).closest('.flex-col');
    expect(row).toBeTruthy();
    // Stacked on mobile, one row from lg upwards.
    const filterRow = screen.getByLabelText('สถานะ').closest('div.flex-col');
    expect(filterRow?.className).toContain('lg:flex-row');
  });
});

// Round 8-25D — this page used to be fixed at 20 rows/page with no way to
// see more. Same [100, 200, 500, 'ทั้งหมด'] contract as the Plots admin page.
describe('Suppliers — rows-per-page selector (100 / 200 / 500 / ทั้งหมด)', () => {
  function hasSearchCallContaining(expected: Record<string, unknown>) {
    return searchSuppliersMock.mock.calls.some(([body]) => (
      Object.entries(expected).every(([key, value]) => (body as Record<string, unknown>)[key] === value)
    ));
  }

  it('defaults to fetching 100 rows', async () => {
    searchSuppliersMock.mockResolvedValue([supplierSummary()]);
    renderPage();

    await waitFor(() => expect(hasSearchCallContaining({ limit: 100, offset: 0 })).toBe(true));
    const selector = screen.getByLabelText('แสดง') as HTMLSelectElement;
    expect(selector.value).toBe('100');
  });

  it('switches to 500 rows per page when selected', async () => {
    searchSuppliersMock.mockResolvedValue([supplierSummary()]);
    renderPage();

    await screen.findByText('SUP001');
    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: '500' } });

    await waitFor(() => expect(hasSearchCallContaining({ limit: 500, offset: 0 })).toBe(true));
  });

  it('pages through everything (chunked) when "ทั้งหมด" is selected', async () => {
    const firstChunk = Array.from({ length: 200 }, (_, i) => supplierSummary({ id: `s${i}`, code: `SUP${i}` }));
    searchSuppliersMock
      .mockResolvedValueOnce([supplierSummary()]) // default 100-row load on mount
      .mockResolvedValueOnce(firstChunk)           // "all": chunk 1 (full → keep going)
      .mockResolvedValueOnce([supplierSummary()]); // "all": chunk 2 (short → stop)

    renderPage();
    await screen.findByText('SUP001');

    fireEvent.change(screen.getByLabelText('แสดง'), { target: { value: 'all' } });

    await waitFor(() => expect(hasSearchCallContaining({ limit: 200, offset: 200 })).toBe(true), {
      timeout: 15000,
    });
    expect(screen.queryByText('ถัดไป →')).toBeNull();
  }, 20000);
});
