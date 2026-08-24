/**
 * Roles page — round 8-25H: row actions (แก้ไข/ลบ) moved behind one
 * ActionMenu trigger, matching Users and the farmlog admin tables. No prior
 * test file existed for this page; this one covers only the ActionMenu
 * behaviour, not the create/edit form (untouched by this round).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Roles } from './Roles';
import type { RoleSummary } from '../../types/auth';

const listRolesMock = vi.fn();
const deleteRoleMock = vi.fn();
const getRoleMock = vi.fn();

vi.mock('../../api/roles', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/roles')>();
  return {
    ...actual,
    listRoles: (...a: unknown[]) => listRolesMock(...a),
    deleteRole: (...a: unknown[]) => deleteRoleMock(...a),
    getRole: (...a: unknown[]) => getRoleMock(...a),
  };
});
vi.mock('../../api/permissions', () => ({
  listPermissions: () => Promise.resolve([]),
  groupByCategory: () => ({}),
}));

// i18n returns the key path, matching Users.test.tsx's convention —
// assertions target stable keys, not translated copy.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && 'name' in opts ? `${key}:${opts.name}` : key,
  }),
}));

function roleSummary(overrides: Partial<RoleSummary> = {}): RoleSummary {
  return {
    id: 'role-1', name: 'internal:field_officer', displayName: 'Field Officer',
    providerScope: 'internal', isSystem: false, usersCount: 0,
    ...overrides,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Roles />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listRolesMock.mockReset();
  deleteRoleMock.mockReset();
  getRoleMock.mockReset();
  listRolesMock.mockResolvedValue([roleSummary()]);
  getRoleMock.mockResolvedValue({ ...roleSummary(), description: null, permissions: [] });
});

describe('row actions — ActionMenu (round 8-25H)', () => {
  it('hides both actions behind one trigger until opened', async () => {
    renderPage();
    await screen.findByText('internal:field_officer');

    expect(screen.queryByRole('menuitem', { name: 'common.edit' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'common.delete' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'common.edit' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'common.delete' })).toBeTruthy();
  });

  it('opens the edit form from the menu', async () => {
    renderPage();
    await screen.findByText('internal:field_officer');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'common.edit' }));

    expect(await screen.findByRole('dialog')).toBeTruthy();
  });

  it('disables delete for a system role', async () => {
    listRolesMock.mockResolvedValue([roleSummary({ isSystem: true })]);
    renderPage();
    await screen.findByText('internal:field_officer');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'common.delete' }).hasAttribute('disabled')).toBe(true);
  });

  it('disables delete for a role that still has assigned users', async () => {
    listRolesMock.mockResolvedValue([roleSummary({ usersCount: 3 })]);
    renderPage();
    await screen.findByText('internal:field_officer');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'common.delete' }).hasAttribute('disabled')).toBe(true);
  });

  it('calls deleteRole when confirmed for a deletable role', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteRoleMock.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('internal:field_officer');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'common.delete' }));

    await waitFor(() => expect(deleteRoleMock).toHaveBeenCalledWith('role-1'));
  });
});
