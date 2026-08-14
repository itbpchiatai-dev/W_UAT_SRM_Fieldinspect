/**
 * PlotInspectionPasswordModal (round 8-9B) — input contract, client policy
 * mirror, error mapping, and above all the secret-handling guarantees: the PIN
 * never leaves component memory and never survives a close/reopen.
 *
 * The PINs here are test-only fixtures, never real credentials.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders } from 'axios';
import {
  PlotInspectionPasswordModal,
  describeInspectionCredentialError,
  validatePlotInspectionPin,
} from './PlotInspectionPasswordModal';

const setCredentialMock = vi.fn();

vi.mock('../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/plots')>();
  return {
    ...actual,
    setPlotInspectionAccessCredential: (...a: unknown[]) => setCredentialMock(...a),
  };
});

let allowedPerms: Set<string> | null = null; // null = every permission allowed
vi.mock('../../hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => (allowedPerms === null ? true : allowedPerms.has(key)),
}));

const PIN = '135790';        // test-only
const OTHER_PIN = '482913';  // test-only

function axiosStatus(status: number): AxiosError {
  return new AxiosError('failed', 'ERR', undefined, null, {
    status, statusText: '', data: { detail: 'internal detail that must not leak' },
    headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() },
  });
}

function renderModal(props: Partial<{ configured: boolean; onClose: () => void; onSaved: () => void }> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  const onSaved = props.onSaved ?? vi.fn();
  const view = render(
    <QueryClientProvider client={qc}>
      <PlotInspectionPasswordModal
        plotId="plot-1"
        supplierCode="SUP001"
        supplierName="ซัพพลายเออร์ หนึ่ง"
        plotCode="SUP001-P001"
        plotName="แปลงทดสอบ"
        configured={props.configured ?? false}
        onClose={onClose}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  return { onClose, onSaved, qc, view };
}

function pinInputs(): HTMLInputElement[] {
  return [
    screen.getByLabelText('รหัสยืนยันแปลง') as HTMLInputElement,
    screen.getByLabelText('ยืนยันรหัสอีกครั้ง') as HTMLInputElement,
  ];
}

function fillBoth(first: string, second = first) {
  const [pin, confirm] = pinInputs();
  fireEvent.change(pin, { target: { value: first } });
  fireEvent.change(confirm, { target: { value: second } });
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: /ยืนยัน(ตั้ง|เปลี่ยน)รหัส/ }));
}

beforeEach(() => {
  setCredentialMock.mockReset();
  setCredentialMock.mockResolvedValue({ configured: true, credentialVersion: 1, updatedAt: null });
  allowedPerms = null;
});

/** Dump a Storage's contents. jsdom does not always expose localStorage/
 * sessionStorage here, so tolerate undefined — same helper shape
 * PublicInspect.test.tsx already uses for its token check. */
function dumpStorage(store: Storage | undefined): string {
  if (!store) return '';
  let out = '';
  for (let i = 0; i < store.length; i += 1) {
    const key = store.key(i);
    if (key) out += `${key}=${store.getItem(key)}|`;
  }
  return out;
}

// --- pure policy mirror -----------------------------------------------------

describe('validatePlotInspectionPin', () => {
  it('accepts any ASCII-digit code from 4 to 20 digits', () => {
    for (const ok of [PIN, '1357', '102030', '1'.repeat(20), '1234567890'.repeat(2)]) {
      expect(validatePlotInspectionPin(ok)).toBeNull();
    }
  });

  it('accepts repeated codes — the guessability rule is gone (round 8-9B.0)', () => {
    for (const easy of ['0000', '1111', '9999', '000000', '111111']) {
      expect(validatePlotInspectionPin(easy)).toBeNull();
    }
  });

  it('accepts sequential codes in both directions', () => {
    for (const easy of ['1234', '4321', '0123', '123456', '987654', '012345']) {
      expect(validatePlotInspectionPin(easy)).toBeNull();
    }
  });

  it('rejects the wrong length or non-ASCII digits with one static message', () => {
    for (const bad of ['1', '12', '123', '1'.repeat(21), '13579a', '๑๓๕๗', '１２３４', '12 34']) {
      expect(validatePlotInspectionPin(bad))
        .toBe('รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก');
    }
  });

  it('asks for a value when empty', () => {
    expect(validatePlotInspectionPin('')).toBe('กรุณากรอกรหัสยืนยันแปลง');
  });

  it('never mentions the old guessability rules', () => {
    const message = validatePlotInspectionPin('123') ?? '';
    for (const banned of ['เดาง่าย', 'เลขซ้ำ', 'เลขเรียง', '6 หลัก']) {
      expect(message).not.toContain(banned);
    }
  });
});

// --- error mapping ----------------------------------------------------------

describe('describeInspectionCredentialError', () => {
  it('maps each status to its specified Thai message', () => {
    expect(describeInspectionCredentialError(axiosStatus(422)))
      .toBe('รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก');
    expect(describeInspectionCredentialError(axiosStatus(503)))
      .toBe('ระบบยังไม่พร้อมตั้งรหัสยืนยันแปลง กรุณาติดต่อผู้ดูแลระบบ');
    expect(describeInspectionCredentialError(axiosStatus(409)))
      .toBe('มีผู้ใช้อื่นเปลี่ยนข้อมูลแปลงนี้ กรุณาลองใหม่อีกครั้ง');
    expect(describeInspectionCredentialError(axiosStatus(404)))
      .toBe('ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์จัดการแปลง');
  });

  it('falls back to one generic message for anything else', () => {
    expect(describeInspectionCredentialError(axiosStatus(500)))
      .toBe('ตั้งรหัสยืนยันแปลงไม่สำเร็จ กรุณาลองใหม่อีกครั้ง');
    expect(describeInspectionCredentialError(new Error('boom')))
      .toBe('ตั้งรหัสยืนยันแปลงไม่สำเร็จ กรุณาลองใหม่อีกครั้ง');
  });

  it('never surfaces the raw backend detail', () => {
    for (const status of [422, 503, 409, 404, 500]) {
      expect(describeInspectionCredentialError(axiosStatus(status)))
        .not.toContain('internal detail');
    }
  });
});

// --- rendering / input contract ---------------------------------------------

describe('PlotInspectionPasswordModal — input contract', () => {
  it('renders both fields as password inputs with numeric hints, capped at 20', () => {
    renderModal();
    for (const input of pinInputs()) {
      expect(input.type).toBe('password');
      expect(input.inputMode).toBe('numeric');
      expect(input.getAttribute('autocomplete')).toBe('new-password');
      expect(input.maxLength).toBe(20);
      // type="number" would eat a leading zero and add spinners
      expect(input.type).not.toBe('number');
    }
  });

  it('shows the length helper under the field', () => {
    renderModal();
    expect(screen.getByText('กรอกตัวเลขอย่างน้อย 4 หลัก')).toBeTruthy();
  });

  it('keeps a leading zero', () => {
    renderModal();
    const [pin] = pinInputs();
    fireEvent.change(pin, { target: { value: '013579' } });
    expect(pin.value).toBe('013579');
    fireEvent.change(pin, { target: { value: '0000' } });
    expect(pin.value).toBe('0000');
  });

  it('strips non-digits (including Unicode digit forms) and truncates past 20', () => {
    renderModal();
    const [pin] = pinInputs();
    fireEvent.change(pin, { target: { value: '1a3-5 7' } });
    expect(pin.value).toBe('1357');
    fireEvent.change(pin, { target: { value: '๑๒๓๔5678' } });
    expect(pin.value).toBe('5678');
    fireEvent.change(pin, { target: { value: '1'.repeat(25) } });
    expect(pin.value).toBe('1'.repeat(20));
  });

  it('never shows any copy about 6 digits or easy-to-guess codes', () => {
    renderModal({ configured: true });
    const text = document.body.textContent ?? '';
    for (const banned of ['6 หลัก', 'เดาง่าย', 'เลขซ้ำ', 'เลขเรียง', 'ใช้รหัสอื่น']) {
      expect(text).not.toContain(banned);
    }
  });

  it('toggles reveal for both fields with an accessible tooltip', () => {
    renderModal();
    expect(pinInputs()[0].type).toBe('password');
    fireEvent.click(screen.getAllByRole('button', { name: 'แสดงรหัส' })[0]);
    for (const input of pinInputs()) expect(input.type).toBe('text');
    fireEvent.click(screen.getAllByRole('button', { name: 'ซ่อนรหัส' })[0]);
    for (const input of pinInputs()) expect(input.type).toBe('password');
  });

  it('shows the plot identity so the wrong plot cannot be edited by accident', () => {
    renderModal();
    expect(screen.getByText('ซัพพลายเออร์ หนึ่ง')).toBeTruthy();
    expect(screen.getByText('SUP001')).toBeTruthy();
    expect(screen.getByText('SUP001-P001')).toBeTruthy();
    expect(screen.getByText('แปลงทดสอบ')).toBeTruthy();
  });

  it('titles itself for a first set and shows no change-warning', () => {
    renderModal({ configured: false });
    expect(screen.getByText('ตั้งรหัสยืนยันแปลง')).toBeTruthy();
    expect(screen.queryByText(/ผู้ตรวจที่ใช้รหัสเดิม/)).toBeNull();
  });

  it('titles itself for a replace and warns that old inspectors lose access', () => {
    renderModal({ configured: true });
    expect(screen.getByText('เปลี่ยนรหัสยืนยันแปลง')).toBeTruthy();
    expect(screen.getByText(/ผู้ตรวจที่ใช้รหัสเดิมจะไม่สามารถเข้าตรวจแปลงนี้ได้/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /ยืนยันเปลี่ยนรหัส/ })).toBeTruthy();
  });

  it('explains what the password is used for', () => {
    renderModal();
    expect(screen.getByText('รหัสนี้ใช้ร่วมกับหมายเลขสำหรับเข้าตรวจ เพื่อค้นหาแปลงที่ได้รับอนุญาต')).toBeTruthy();
  });

  it('never renders an existing password, hash, digest or version', () => {
    renderModal({ configured: true });
    const text = document.body.textContent ?? '';
    for (const leaked of ['$2b$', 'digest', 'hash', 'pepper', 'credentialVersion', 'เวอร์ชัน']) {
      expect(text.toLowerCase()).not.toContain(leaked.toLowerCase());
    }
  });

  it('falls back to a read-only view without plots.update', () => {
    allowedPerms = new Set(['plots.read']);
    renderModal();
    expect(screen.getByText(/คุณไม่มีสิทธิ์ตั้งหรือเปลี่ยนรหัสยืนยันแปลงนี้/)).toBeTruthy();
    expect(screen.queryByLabelText('รหัสยืนยันแปลง')).toBeNull();
    expect(screen.queryByRole('button', { name: /ยืนยัน/ })).toBeNull();
  });
});

// --- client validation blocks the request -----------------------------------

describe('PlotInspectionPasswordModal — client validation', () => {
  it('blocks a code shorter than 4 digits', async () => {
    renderModal();
    fillBoth('123');
    submit();
    expect(await screen.findByText('รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก')).toBeTruthy();
    expect(setCredentialMock).not.toHaveBeenCalled();
  });

  it('blocks when the confirmation does not match', async () => {
    renderModal();
    fillBoth(PIN, OTHER_PIN);
    submit();
    expect(await screen.findByText('รหัสยืนยันแปลงทั้งสองช่องไม่ตรงกัน')).toBeTruthy();
    expect(setCredentialMock).not.toHaveBeenCalled();
  });

  it.each(['0000', '1111', '1234', '987654', '111111'])(
    'submits the easy-to-guess code %s — no guessability rule any more',
    async (easy) => {
      renderModal();
      fillBoth(easy);
      submit();
      await waitFor(() => expect(setCredentialMock).toHaveBeenCalledTimes(1));
      expect(setCredentialMock).toHaveBeenCalledWith('plot-1', easy);
    },
  );

  it.each(['1357', '1'.repeat(20)])('submits the boundary length %s', async (edge) => {
    renderModal();
    fillBoth(edge);
    submit();
    await waitFor(() => expect(setCredentialMock).toHaveBeenCalledWith('plot-1', edge));
  });

  it('cannot even type 21 digits — the input caps at the policy maximum', async () => {
    renderModal();
    fillBoth('1'.repeat(21));
    submit();
    await waitFor(() => expect(setCredentialMock).toHaveBeenCalledTimes(1));
    expect(setCredentialMock).toHaveBeenCalledWith('plot-1', '1'.repeat(20));
  });
});

// --- submit -----------------------------------------------------------------

describe('PlotInspectionPasswordModal — submit', () => {
  it('calls the API exactly once with (plotId, password) for a valid PIN', async () => {
    renderModal();
    fillBoth(PIN);
    submit();
    await waitFor(() => expect(setCredentialMock).toHaveBeenCalledTimes(1));
    expect(setCredentialMock).toHaveBeenCalledWith('plot-1', PIN);
  });

  it('blocks a double submit while the request is in flight', async () => {
    let resolve!: (v: unknown) => void;
    setCredentialMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    renderModal();
    fillBoth(PIN);
    submit();
    await waitFor(() => expect(setCredentialMock).toHaveBeenCalledTimes(1));

    const button = screen.getByRole('button', { name: /ยืนยันตั้งรหัส/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    submit();
    submit();
    expect(setCredentialMock).toHaveBeenCalledTimes(1);

    resolve({ configured: true, credentialVersion: 1, updatedAt: null });
  });

  it('closes via onSaved and invalidates the status query on success', async () => {
    const { onSaved, qc } = renderModal();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    fillBoth(PIN);
    submit();
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith({ queryKey: ['plot-inspection-credential', 'plot-1'] });
  });

  it('clears both fields on success so no plaintext lingers', async () => {
    const { onSaved } = renderModal();
    fillBoth(PIN);
    submit();
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    for (const input of pinInputs()) expect(input.value).toBe('');
  });
});

// --- server errors ----------------------------------------------------------

describe('PlotInspectionPasswordModal — server errors', () => {
  it.each([
    [422, 'รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก'],
    [503, 'ระบบยังไม่พร้อมตั้งรหัสยืนยันแปลง กรุณาติดต่อผู้ดูแลระบบ'],
    [409, 'มีผู้ใช้อื่นเปลี่ยนข้อมูลแปลงนี้ กรุณาลองใหม่อีกครั้ง'],
    [404, 'ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์จัดการแปลง'],
    [500, 'ตั้งรหัสยืนยันแปลงไม่สำเร็จ กรุณาลองใหม่อีกครั้ง'],
  ])('shows the mapped message for HTTP %i', async (status, message) => {
    setCredentialMock.mockRejectedValue(axiosStatus(status as number));
    renderModal();
    fillBoth(PIN);
    submit();
    expect(await screen.findByText(message as string)).toBeTruthy();
  });

  it('keeps the modal open after a failed save', async () => {
    setCredentialMock.mockRejectedValue(axiosStatus(409));
    const { onClose, onSaved } = renderModal();
    fillBoth(PIN);
    submit();
    await screen.findByText('มีผู้ใช้อื่นเปลี่ยนข้อมูลแปลงนี้ กรุณาลองใหม่อีกครั้ง');
    expect(onClose).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByLabelText('รหัสยืนยันแปลง')).toBeTruthy();
  });

  it('never puts the submitted PIN into the error message', async () => {
    setCredentialMock.mockRejectedValue(axiosStatus(422));
    renderModal();
    fillBoth(PIN);
    submit();
    await screen.findByText(/รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก/);
    const errorText = screen.getByText(/รหัสยืนยันแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก/).textContent ?? '';
    expect(errorText).not.toContain(PIN);
  });
});

// --- secret handling --------------------------------------------------------

describe('PlotInspectionPasswordModal — secret handling', () => {
  it('never writes the PIN to localStorage or sessionStorage', async () => {
    renderModal();
    fillBoth(PIN);
    submit();
    await waitFor(() => expect(setCredentialMock).toHaveBeenCalled());

    const all = dumpStorage(globalThis.localStorage) + dumpStorage(globalThis.sessionStorage);
    expect(all).not.toContain(PIN);
  });

  it('does not survive a close and reopen', () => {
    const { view } = renderModal();
    fillBoth(PIN);
    expect(pinInputs()[0].value).toBe(PIN);

    view.unmount();          // exactly what onClose does in PlotDetail
    renderModal();
    for (const input of pinInputs()) expect(input.value).toBe('');
  });

  it('never renders the PIN as page text while masked', () => {
    renderModal();
    fillBoth(PIN);
    // The value lives in the input's value property, not in the DOM text.
    expect(document.body.textContent).not.toContain(PIN);
  });
});
