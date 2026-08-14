/**
 * Mobile nav drawer — regression guard.
 *
 * Asserts that on a sub-640px viewport the sidebar is an openable
 * off-canvas drawer (not removed from the DOM, not icon-only) and that the
 * TopBar hamburger toggles it. See the git history of Sidebar/TopBar for
 * the original "menu disappears on mobile" bug this locks down.
 */
import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// jsdom has no matchMedia — emulate a phone (below the 640px breakpoint).
beforeAll(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false, // (min-width: 640px) === false -> mobile
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
});

import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { useAuthStore } from '../../stores/auth';

// Sidebar reads menus from the auth store (populated in real usage by
// hydrateFromServer/refreshPermissions) rather than fetching them itself —
// seed the store directly instead of mocking api/me's getMyMenus.
beforeEach(() => {
  useAuthStore.setState({
    menus: [
      {
        id: '1', key: 'dashboard', labelTh: 'แดชบอร์ด', labelEn: 'Dashboard',
        path: '/', icon: 'LayoutDashboard', parentId: null, order: 1,
        permissionKey: null, children: [],
      },
      {
        id: '2', key: 'users', labelTh: 'ผู้ใช้', labelEn: 'Users',
        path: '/users', icon: 'Users', parentId: null, order: 2,
        permissionKey: null, children: [],
      },
    ],
  });
});

function renderShell() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TopBar />
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('mobile nav drawer', () => {
  it('has a hamburger toggle that opens/closes the off-canvas drawer', () => {
    const { container } = renderShell();

    const hamburger = screen.getByRole('button', { name: /เปิดเมนู|open menu/i });
    expect(hamburger.className).toContain('sm:hidden');

    const aside = container.querySelector('aside')!;
    expect(aside).toBeTruthy();
    expect(aside.className).toContain('-translate-x-full');
    // The original bug removed the sidebar entirely via `hidden sm:block`.
    expect(aside.className).not.toContain('hidden');
    expect(container.querySelector('.bg-black\\/40')).toBeNull();

    fireEvent.click(hamburger);
    expect(aside.className).toContain('translate-x-0');
    expect(aside.className).not.toContain('-translate-x-full');
    const backdrop = container.querySelector('.bg-black\\/40');
    expect(backdrop).toBeTruthy();

    fireEvent.click(backdrop as Element);
    expect(aside.className).toContain('-translate-x-full');
  });

  it('shows full menu labels in the drawer (not the icon-only rail) on mobile', async () => {
    const { container } = renderShell();
    const aside = container.querySelector('aside')!;
    expect(await within(aside).findByText('แดชบอร์ด')).toBeTruthy();
    expect(await within(aside).findByText('ผู้ใช้')).toBeTruthy();
  });
});
