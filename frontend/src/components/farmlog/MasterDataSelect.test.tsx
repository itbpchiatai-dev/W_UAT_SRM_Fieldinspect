/**
 * MasterDataSelect — round 8-15D: an inactive-but-currently-selected value
 * must stay visible (never go blank / look like data loss) but must be
 * unselectable as a NEW choice once the user picks something else.
 *
 * Round Z — the control is a searchable dropdown (SearchableSelect), not a
 * native <select>, so its options exist only while the list is open. The
 * rules under test are unchanged.
 */
import { it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MasterDataSelect } from './MasterDataSelect';
import { masterDataQueryKey, type MasterDataItem } from '../../api/masterdata';

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

function renderSelect(
  value: string | null,
  onChange = vi.fn(),
  qc: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
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

/** Open the dropdown once its options have loaded. */
async function openList() {
  const trigger = await screen.findByRole('button', { name: 'เลือกcrop' });
  await waitFor(() => expect((trigger as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(trigger);
}

it('queries with activeOnly=true', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect(null);
  await waitFor(() => expect(listMasterDataMock).toHaveBeenCalled());
  expect(listMasterDataMock.mock.calls[0][0]).toMatchObject({ type: 'crop', activeOnly: true });
});

it('renders only active options returned by the query', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect(null);
  await openList();
  expect(screen.getByRole('option', { name: 'พริก' })).toBeTruthy();
  expect(screen.getByRole('option', { name: 'เมล่อน' })).toBeTruthy();
  // No stray "inactive" marker when the current value has nothing to mark.
  expect(screen.queryByText(/ปิดใช้งาน\/ค่าเดิม/)).toBeNull();
});

it('current value not in the active list stays visible and marked', async () => {
  // The list never contains "ทุเรียน" (e.g. deactivated after this cycle was created).
  listMasterDataMock.mockResolvedValue([item('พริก')]);
  renderSelect('ทุเรียน');

  // Round Z — the field still SHOWS it (never blank, never data loss), and
  // it is not among the options a new choice can be made from.
  const trigger = await screen.findByRole('button', { name: 'เลือกcrop' });
  await waitFor(() =>
    expect(trigger.textContent).toContain('ทุเรียน (ปิดใช้งาน/ค่าเดิม)'),
  );
  fireEvent.click(trigger);
  expect(screen.queryByRole('option', { name: /ทุเรียน/ })).toBeNull();
});

it('an active current value renders with no legacy marker', async () => {
  listMasterDataMock.mockResolvedValue([item('พริก'), item('เมล่อน')]);
  renderSelect('พริก');
  await openList();
  expect(screen.getByRole('option', { name: 'พริก' })).toBeTruthy();
  expect(screen.queryByText(/ปิดใช้งาน\/ค่าเดิม/)).toBeNull();
});

it('round 8-22A: never reuses the Admin (all-status) cache entry, even when it is already populated', async () => {
  // Simulates the Admin Master Data page having already loaded 'crop'
  // (all statuses, including an inactive one) under ITS key, seconds
  // before this selector mounts elsewhere in the app.
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5 * 60 * 1000 } },
  });
  qc.setQueryData(masterDataQueryKey('crop', null, false), [item('พริก'), item('ทุเรียน', false)]);
  // The active-only query itself always excludes it server-side.
  listMasterDataMock.mockResolvedValue([item('พริก')]);

  renderSelect(null, vi.fn(), qc);

  await waitFor(() => expect(listMasterDataMock).toHaveBeenCalled());
  await openList();
  // A distinct key means this query starts with no cached data of its own
  // — it never even transiently shows the Admin page's inactive-included
  // list before its own fetch resolves.
  expect(screen.queryByRole('option', { name: 'ทุเรียน' })).toBeNull();
});
