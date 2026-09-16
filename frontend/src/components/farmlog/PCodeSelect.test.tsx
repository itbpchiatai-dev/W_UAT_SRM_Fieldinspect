/**
 * PCodeSelect (round Y) — the control that replaced the พันธุ์ dropdown.
 *
 * Master Data nests P.Codes under varieties and varieties under crops, so
 * "the P.Codes of crop WM" is a two-level question. This component asks it
 * with the two list endpoints that already exist and joins the answers, so
 * the user picks a crop and then a P.Code, and the variety comes along for
 * the ride.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

describe('PCodeSelect', () => {
  it('offers only the P.Codes whose variety sits under the chosen crop', async () => {
    renderSelect();
    await waitFor(() => expect(screen.getByRole('option', { name: /CTT-507/ })).toBeTruthy());
    expect(screen.getByRole('option', { name: /CTT-620/ })).toBeTruthy();
    // WM-111 belongs to พริกขี้หนู, a variety of another crop.
    expect(screen.queryByRole('option', { name: /WM-111/ })).toBeNull();
  });

  it('shows the variety beside each P.Code, so the choice is readable', async () => {
    renderSelect();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'CTT-507 — แตงโม RWA 412 (D-12)' })).toBeTruthy(),
    );
  });

  it('reports the chosen P.Code together with its variety', async () => {
    const { onChange } = renderSelect();
    await waitFor(() => expect(screen.getByRole('option', { name: /CTT-507/ })).toBeTruthy());
    fireEvent.change(screen.getByLabelText('เลือก P.Code'), { target: { value: 'CTT-507' } });
    // The caller needs both: the P.Code to send, the variety to display.
    expect(onChange).toHaveBeenCalledWith('CTT-507', 'แตงโม RWA 412 (D-12)');
  });

  it('clearing the box reports no P.Code and no variety', async () => {
    const { onChange } = renderSelect({ value: 'CTT-507' });
    await waitFor(() => expect(screen.getByRole('option', { name: /CTT-507/ })).toBeTruthy());
    fireEvent.change(screen.getByLabelText('เลือก P.Code'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith(null, null);
  });

  it('asks the user for a crop first rather than listing every P.Code', async () => {
    renderSelect({ crop: null });
    expect(screen.getByText('— เลือกชนิดพืชก่อน —')).toBeTruthy();
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
      expect(screen.getByRole('option', { name: /OLD-001/ })).toBeTruthy(),
    );
  });
});
