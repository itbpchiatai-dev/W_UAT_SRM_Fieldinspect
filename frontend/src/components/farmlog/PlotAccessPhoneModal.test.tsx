import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlotAccessPhoneModal } from './PlotAccessPhoneModal';
import type { PlotAccessPhoneConfigResponse } from '../../api/plots';

const getPlotAccessPhonesMock = vi.fn();
const replacePlotAccessPhonesMock = vi.fn();

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return {
    ...actual,
    getPlotAccessPhones: (...a: unknown[]) => getPlotAccessPhonesMock(...a),
    replacePlotAccessPhones: (...a: unknown[]) => replacePlotAccessPhonesMock(...a),
  };
});

let allowedPerms: Set<string> | null = null; // null = every permission allowed
vi.mock('../../hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (allowedPerms === null ? true : allowedPerms.has(key)),
}));

function config(overrides: Partial<PlotAccessPhoneConfigResponse> = {}): PlotAccessPhoneConfigResponse {
  return {
    primaryPhone: '0845552162',
    additionalPhones: ['0812345678'],
    items: [
      { id: 'row-1', phone: '0845552162', accessType: 'primary', isActive: true, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
      { id: 'row-2', phone: '0812345678', accessType: 'additional', isActive: true, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
    ],
    ...overrides,
  };
}

function renderModal(props: Partial<{ onClose: () => void; onSaved: () => void }> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  const onSaved = props.onSaved ?? vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <PlotAccessPhoneModal plotId="plot-1" plotLabel="SUP001-P001" onClose={onClose} onSaved={onSaved} />
    </QueryClientProvider>,
  );
  return { onClose, onSaved, qc };
}

beforeEach(() => {
  getPlotAccessPhonesMock.mockReset();
  replacePlotAccessPhonesMock.mockReset();
  allowedPerms = null;
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('PlotAccessPhoneModal — fetch on open', () => {
  it('fetches GET access-phones when opened', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    renderModal();
    await waitFor(() => expect(getPlotAccessPhonesMock).toHaveBeenCalledWith('plot-1'));
  });

  it('shows a loading state before the fetch resolves', () => {
    getPlotAccessPhonesMock.mockReturnValue(new Promise(() => {})); // never resolves
    renderModal();
    expect(document.querySelector('.animate-spin')).toBeTruthy();
  });

  it('populates primary/additional from the fetched config', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    renderModal();
    const primary = await screen.findByLabelText('เบอร์หลัก') as HTMLInputElement;
    expect(primary.value).toBe('0845552162');
    expect((screen.getByLabelText('เบอร์เสริมที่ 1') as HTMLInputElement).value).toBe('0812345678');
  });

  it('shows an error state with retry on fetch failure', async () => {
    getPlotAccessPhonesMock.mockRejectedValueOnce(new Error('boom'));
    getPlotAccessPhonesMock.mockResolvedValueOnce(config());
    renderModal();
    await screen.findByText('โหลดข้อมูลเบอร์โทรไม่สำเร็จ');
    fireEvent.click(screen.getByRole('button', { name: 'ลองใหม่' }));
    await screen.findByLabelText('เบอร์หลัก');
    expect(getPlotAccessPhonesMock).toHaveBeenCalledTimes(2);
  });
});

describe('PlotAccessPhoneModal — save', () => {
  it('PUTs the built config exactly once and invalidates the 3 keys', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    replacePlotAccessPhonesMock.mockResolvedValue(config());
    const { qc, onSaved } = renderModal();
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    await screen.findByLabelText('เบอร์หลัก');

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));

    await waitFor(() => expect(replacePlotAccessPhonesMock).toHaveBeenCalledTimes(1));
    expect(replacePlotAccessPhonesMock).toHaveBeenCalledWith('plot-1', {
      primaryPhone: '0845552162', additionalPhones: ['0812345678'],
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    const invalidatedKeys = invalidateSpy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(invalidatedKeys).toContain(JSON.stringify(['plot', 'plot-1']));
    expect(invalidatedKeys).toContain(JSON.stringify(['plots']));
    expect(invalidatedKeys).toContain(JSON.stringify(['plot-access-phones', 'plot-1']));
  });

  it('does not optimistically update before the PUT resolves', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config({ primaryPhone: '0845552162', additionalPhones: [] }));
    let resolvePut: (v: unknown) => void = () => {};
    replacePlotAccessPhonesMock.mockReturnValue(new Promise((resolve) => { resolvePut = resolve; }));
    renderModal();
    const primary = await screen.findByLabelText('เบอร์หลัก') as HTMLInputElement;
    fireEvent.change(primary, { target: { value: '0891112222' } });

    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
    // still mid-flight: the GET-derived data is untouched (no re-fetch fired yet)
    expect(getPlotAccessPhonesMock).toHaveBeenCalledTimes(1);
    resolvePut(config());
    await waitFor(() => expect(getPlotAccessPhonesMock).toHaveBeenCalledTimes(1));
  });
});

describe('PlotAccessPhoneModal — error mapping', () => {
  async function triggerSaveError(error: unknown) {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    replacePlotAccessPhonesMock.mockRejectedValue(error);
    const { onClose, onSaved } = renderModal();
    await screen.findByLabelText('เบอร์หลัก');
    fireEvent.click(screen.getByRole('button', { name: 'บันทึก' }));
    await waitFor(() => expect(replacePlotAccessPhonesMock).toHaveBeenCalled());
    return { onClose, onSaved };
  }

  function axiosError(status: number, data?: unknown) {
    return { isAxiosError: true, response: { status, data } };
  }

  it('404 shows the not-found/no-permission message', async () => {
    await triggerSaveError(axiosError(404));
    await screen.findByText('ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์เข้าถึง');
  });

  it('409 shows the stale-data message', async () => {
    await triggerSaveError(axiosError(409));
    await screen.findByText('ข้อมูลมีการเปลี่ยนแปลงจากที่อื่น กรุณาโหลดใหม่แล้วลองอีกครั้ง');
  });

  it('422 shows the backend validation message', async () => {
    await triggerSaveError(axiosError(422, { detail: [{ msg: 'เบอร์โทรศัพท์ไม่ถูกต้อง' }] }));
    await screen.findByText('เบอร์โทรศัพท์ไม่ถูกต้อง');
  });

  it('network error (no response) shows a generic Thai message', async () => {
    await triggerSaveError({ isAxiosError: true, response: undefined });
    await screen.findByText('เชื่อมต่อเครือข่ายไม่สำเร็จ กรุณาลองใหม่อีกครั้ง');
  });

  it('a failed save does NOT close the modal or call onSaved', async () => {
    const { onClose, onSaved } = await triggerSaveError(axiosError(409));
    await screen.findByText('ข้อมูลมีการเปลี่ยนแปลงจากที่อื่น กรุณาโหลดใหม่แล้วลองอีกครั้ง');
    expect(onClose).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });
});

describe('PlotAccessPhoneModal — permission behavior', () => {
  it('with plots.update: Save button is present', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    renderModal();
    await screen.findByLabelText('เบอร์หลัก');
    expect(screen.getByRole('button', { name: 'บันทึก' })).toBeTruthy();
  });

  it('without plots.update: read-only, no Save button, inputs disabled', async () => {
    allowedPerms = new Set(); // no permissions at all
    getPlotAccessPhonesMock.mockResolvedValue(config());
    renderModal();
    const primary = await screen.findByLabelText('เบอร์หลัก') as HTMLInputElement;
    expect(primary.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'บันทึก' })).toBeNull();
    expect(screen.getByText(/ไม่มีสิทธิ์แก้ไข/)).toBeTruthy();
  });
});

describe('PlotAccessPhoneModal — dirty close confirm', () => {
  it('confirms before closing when there are unsaved changes', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { onClose } = renderModal();
    const primary = await screen.findByLabelText('เบอร์หลัก') as HTMLInputElement;
    fireEvent.change(primary, { target: { value: '0891112222' } });

    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('does not confirm when nothing changed', async () => {
    getPlotAccessPhonesMock.mockResolvedValue(config());
    const confirmSpy = vi.spyOn(window, 'confirm');
    const { onClose } = renderModal();
    await screen.findByLabelText('เบอร์หลัก');

    fireEvent.click(screen.getByRole('button', { name: 'ยกเลิก' }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
