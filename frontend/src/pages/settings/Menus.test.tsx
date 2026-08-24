/**
 * Menus page — round 8-25H: all 4 row actions (↑ เลื่อนขึ้น, ↓ เลื่อนลง,
 * แก้ไข, ลบ) moved behind one ActionMenu trigger, matching Users/Roles.
 * Reorder joining the menu (rather than staying inline) was an explicit
 * choice made in that round's brief, not a default — see Menus.tsx's
 * comment at the call site.
 *
 * No prior test file existed for this page; this one covers only the
 * ActionMenu behaviour, not the create/edit form (untouched by this round).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Menus } from './Menus';
import type { MenuItemTree } from '../../types/auth';

const listMenusMock = vi.fn();
const deleteMenuMock = vi.fn();
const swapMenuOrderMock = vi.fn();

vi.mock('../../api/menus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/menus')>();
  return {
    ...actual,
    listMenus: (...a: unknown[]) => listMenusMock(...a),
    deleteMenu: (...a: unknown[]) => deleteMenuMock(...a),
    swapMenuOrder: (...a: unknown[]) => swapMenuOrderMock(...a),
  };
});
vi.mock('../../api/permissions', () => ({
  listPermissions: () => Promise.resolve([]),
}));

// i18n returns the key path, same convention as Users.test.tsx/Roles.test.tsx.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && 'key' in opts ? `${key}:${opts.key}` : key,
    i18n: { language: 'th' },
  }),
}));

function node(overrides: Partial<MenuItemTree> = {}): MenuItemTree {
  return {
    id: 'menu-1', key: 'farmlog.admin.plots', labelTh: 'แปลง', labelEn: 'Plots',
    icon: null, path: '/farmlog/admin/plots', parentId: null, orderIndex: 0,
    requiredPermissionKey: 'plots.read', isSystem: false, children: [],
    ...overrides,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <Menus />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listMenusMock.mockReset();
  deleteMenuMock.mockReset();
  swapMenuOrderMock.mockReset();
  listMenusMock.mockResolvedValue([node()]);
});

describe('row actions — ActionMenu (round 8-25H)', () => {
  it('hides all 4 actions behind one trigger until opened', async () => {
    renderPage();
    await screen.findByText('แปลง');

    expect(screen.queryByRole('menuitem', { name: 'common.edit' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'settings.menus.moveUp' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'settings.menus.moveDown' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'common.edit' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'common.delete' })).toBeTruthy();
  });

  it('opens the edit form from the menu', async () => {
    renderPage();
    await screen.findByText('แปลง');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'common.edit' }));

    expect(await screen.findByText('settings.menus.edit')).toBeTruthy();
  });

  it('swaps order with the next sibling on เลื่อนลง', async () => {
    listMenusMock.mockResolvedValue([
      node({ id: 'menu-1', key: 'a', orderIndex: 0 }),
      node({ id: 'menu-2', key: 'b', labelTh: 'บี', orderIndex: 1 }),
    ]);
    swapMenuOrderMock.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('แปลง');

    fireEvent.click(screen.getAllByRole('button', { name: /common\.actions/ })[0]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'settings.menus.moveDown' }));

    await waitFor(() => expect(swapMenuOrderMock).toHaveBeenCalledWith(
      { id: 'menu-1', orderIndex: 0 },
      { id: 'menu-2', orderIndex: 1 },
    ));
  });

  it('calls deleteMenu when confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteMenuMock.mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('แปลง');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'common.delete' }));

    await waitFor(() => expect(deleteMenuMock).toHaveBeenCalledWith('menu-1'));
  });

  it('does not call deleteMenu when the confirm dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPage();
    await screen.findByText('แปลง');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'common.delete' }));

    expect(deleteMenuMock).not.toHaveBeenCalled();
  });
});
