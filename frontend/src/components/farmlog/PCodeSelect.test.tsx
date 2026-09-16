/**
 * PCodeSelect (round Y; searchable since round Z) — the control that replaced
 * the พันธุ์ dropdown.
 *
 * Master Data nests P.Codes under varieties and varieties under crops, so
 * "the P.Codes of crop WM" is a two-level question. This component asks it
 * with the two list endpoints that already exist and joins the answers, so
 * the user picks a crop and then a P.Code, and the variety comes along for
 * the ride.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PCodeSelect } from './PCodeSelect';

const listMasterDataMock = vi.fn();
vi.mock('../../api/masterdata', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/masterdata')>()),
  listMasterData: (...a: unknown[]) => listMasterDataMock(...a),
}));

function md(type: string, value: string, parent: string | null) {
  return {
    id: `${type}-${value}`, type, value, parent, orderIndex: 0, active: true,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  };
}

const VARIETIES = [md('variety', 'แตงโม RWA 412 (D-12)', 'WM'), md('variety', 'แตงโม กินรี', 'WM')];
const P_CODES = [
  md('p_code', 'CTT-507', 'แตงโม RWA 412 (D-12)'),
  md('p_code', 'CTT-620', 'แตงโม กินรี'),
  md('p_code', 'WM-111', 'พริกขี้หนู'), // another crop's — must not be offered
];

beforeEach(() => {
  listMasterDataMock.mockReset();
  listMasterDataMock.mockImplementation(({ type, parent }: { type: string; parent?: string }) => {
    if (type === 'variety') {
      return Promise.resolve(parent === 'WM' ? VARIETIES : []);
    }
    if (type === 'p_code') return Promise.resolve(P_CODES);
    return Promise.resolve([]);
  });
});

function renderSelect(props: Partial<Parameters<typeof PCodeSelect>[0]> = {}) {
  const onChange = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PCodeSelect crop="WM" value={null} onChange={onChange} {...props} />
    </QueryClientProvider>,
  );
  return { onChange };
}

/** Open the list once the crop's P.Codes have loaded (the trigger stays
 *  disabled until then). */
async function openList() {
  const trigger = screen.getByRole('button', { name: 'เลือก P.Code' });
  await waitFor(() => expect((trigger as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(trigger);
  return screen.getByRole('listbox');
}

describe('PCodeSelect', () => {
  it('offers only the P.Codes whose variety sits under the chosen crop', async () => {
    renderSelect();
    const listbox = await openList();

    expect(within(listbox).getByRole('option', { name: /CTT-507/ })).toBeTruthy();
    expect(within(listbox).getByRole('option', { name: /CTT-620/ })).toBeTruthy();
    // WM-111 belongs to พริกขี้หนู, a variety of another crop.
    expect(within(listbox).queryByRole('option', { name: /WM-111/ })).toBeNull();
  });

  it('shows the variety beside each P.Code, so the choice is readable', async () => {
    renderSelect();
    const listbox = await openList();

    expect(within(listbox).getByRole('option', { name: 'CTT-507 — แตงโม RWA 412 (D-12)' })).toBeTruthy();
  });

  it('can be searched by P.Code or by variety name (round Z)', async () => {
    renderSelect();
    const listbox = await openList();

    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'กินรี' } });
    expect(within(listbox).getByRole('option', { name: /CTT-620/ })).toBeTruthy();
    expect(within(listbox).queryByRole('option', { name: /CTT-507/ })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'ctt-507' } });
    expect(within(listbox).getByRole('option', { name: /CTT-507/ })).toBeTruthy();
  });

  it('reports the chosen P.Code together with its variety', async () => {
    const { onChange } = renderSelect();
    const listbox = await openList();

    fireEvent.click(within(listbox).getByRole('option', { name: /CTT-507/ }));

    // The caller needs both: the P.Code to send, the variety to display.
    expect(onChange).toHaveBeenCalledWith('CTT-507', 'แตงโม RWA 412 (D-12)');
  });

  it('asks the user for a crop first rather than listing every P.Code', async () => {
    renderSelect({ crop: null });

    const trigger = screen.getByRole('button', { name: 'เลือก P.Code' });
    expect(trigger.textContent).toContain('— เลือกชนิดพืชก่อน —');
    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(listMasterDataMock).not.toHaveBeenCalled());
  });

  it('says so when a crop has no P.Code yet, instead of an empty box', async () => {
    listMasterDataMock.mockImplementation(({ type }: { type: string }) =>
      Promise.resolve(type === 'variety' ? VARIETIES : []),
    );
    renderSelect();

    await waitFor(() => expect(screen.getByText(/ยังไม่มี P.Code/)).toBeTruthy());
  });

  it('keeps a stored value visible even when it is no longer offered', async () => {
    // A cycle created before the P.Code was deactivated must not look like
    // data loss — the same rule MasterDataSelect follows.
    renderSelect({ value: 'OLD-001' });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'เลือก P.Code' }).textContent)
        .toContain('OLD-001 (ปิดใช้งาน/ค่าเดิม)'),
    );
  });
});
