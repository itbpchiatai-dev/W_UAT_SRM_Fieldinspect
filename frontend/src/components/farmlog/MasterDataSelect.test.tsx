/**
 * MasterDataSelect — round 8-15D: an inactive-but-currently-selected value
 * must stay visible (never go blank / look like data loss) but must be
 * unselectable as a NEW choice once the user picks something else.
 */
import { it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MasterDataSelect } from './MasterDataSelect';
import type { MasterDataItem } from '../../api/masterdata';

const listMasterDataMock = vi.fn();

vi.mock('../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/masterdata')>();
  return { ...actual, listMasterData: (...args: unknown[]) => listMasterDataMock(...args) };
});

function item(value: string, active = true): MasterDataItem {
  return {
    id: value, type: 'crop', value, parent: null, orderIndex: 0, active,
    createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
  };
}

function renderSelect(value: string | null, onChange = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MasterDataSelect type="crop" value={value} onChange={onChange} />
    </QueryClientProvider>,
  );
  return onChange;
}

beforeEach(() => {
  listMasterDataMock.mockReset();
});

it('queries with activeOnly=true', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect(null);
  await waitFor(() => expect(listMasterDataMock).toHaveBeenCalled());
  expect(listMasterDataMock.mock.calls[0][0]).toMatchObject({ type: 'crop', activeOnly: true });
});

it('renders only active options returned by the query', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect(null);
  const opt = await screen.findByRole('option', { name: 'พริก' });
  expect(opt).toBeTruthy();
  expect(screen.getByRole('option', { name: 'เมล่อน' })).toBeTruthy();
  // No stray "inactive" marker when the current value has nothing to mark.
  expect(screen.queryByText(/ปิดใช้งาน\/ค่าเดิม/)).toBeNull();
});

it('current value not in the active list stays visible, marked, and disabled', async () => {
  // The list never contains "ทุเรียน" (e.g. deactivated after this cycle was created).
  listMasterDataMock.mockResolvedValue([item('พริก')]);
  renderSelect('ทุเรียน');

  const legacyOption = await screen.findByRole(
    'option', { name: 'ทุเรียน (ปิดใช้งาน/ค่าเดิม)' },
  ) as HTMLOptionElement;
  expect(legacyOption.disabled).toBe(true);
  // The <select> itself still reflects it as the current value (never blank).
  const select = screen.getByRole('combobox') as HTMLSelectElement;
  expect(select.value).toBe('ทุเรียน');
});

it('an active current value renders with no legacy marker', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect('พริก');
  await screen.findByRole('option', { name: 'พริก' });
  expect(screen.queryByText(/ปิดใช้งาน\/ค่าเดิม/)).toBeNull();
});
