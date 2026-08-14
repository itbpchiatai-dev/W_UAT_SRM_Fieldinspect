/**
 * Sidebar — permission-filtered nav tree from /me/menus.
 *
 * The backend already filters the tree to only the items the current
 * user can see; the frontend just renders it. Parent-node collapse
 * state is local (per-mount) — not persisted, by design, because
 * sidebars are short and the tree rarely deep enough to justify it.
 *
 * Whole-sidebar collapse (rail mode) reads from the UI store so the
 * choice persists across reloads and stays in sync with the TopBar
 * toggle button.
 *
 * Icon strategy: each MenuItem.icon is a Lucide icon name (string). We
 * look it up dynamically; unknown names render an empty span so a
 * typo in the seed doesn't blow up the whole sidebar.
 */
import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChevronDown, ChevronRight, type LucideIcon } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '../../stores/auth';
import { useUI } from '../../stores/ui';
import type { MenuItem } from '../../types/auth';

function resolveIcon(name: string | null | undefined): LucideIcon | null {
  if (!name) return null;
  const lib = LucideIcons as unknown as Record<string, LucideIcon>;
  // Accept both the documented PascalCase ("LayoutDashboard") and the
  // kebab-case form shown on lucide.dev ("layout-dashboard") — normalise
  // to PascalCase so either spelling in the seed / admin form resolves.
  const pascal = name
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');
  return lib[name] ?? lib[pascal] ?? null;
}

// Tracks the Tailwind `sm` breakpoint (640px). Rail-collapse is a
// desktop-only affordance; the mobile drawer always shows full labels,
// so node rendering needs to know which side of the breakpoint we're on.
function useIsDesktop(): boolean {
  const query = '(min-width: 640px)';
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window !== 'undefined' ? window.matchMedia(query).matches : true,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setIsDesktop(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return isDesktop;
}

export function Sidebar() {
  const { i18n } = useTranslation();
  const location = useLocation();
  const collapsed = useUI((s) => s.sidebarCollapsed);
  const mobileNavOpen = useUI((s) => s.mobileNavOpen);
  const closeMobileNav = useUI((s) => s.closeMobileNav);
  const isDesktop = useIsDesktop();
  // Only collapse to a rail on desktop — never icon-only inside the
  // mobile drawer, where the user expects readable labels.
  const railCollapsed = isDesktop && collapsed;

  // Menus already live in the auth store — hydrateFromServer() (on mount/
  // login) and refreshPermissions() (on window focus) both populate them.
  // Reading from there instead of a separate useQuery avoids firing a
  // second, redundant GET /api/v1/me/menus on every Sidebar mount.
  const menus = useAuthStore((s) => s.menus);

  // Navigating to a new route dismisses the drawer so a tap on a link
  // doesn't leave it covering the page. No-op on desktop (always closed).
  useEffect(() => {
    closeMobileNav();
  }, [location.pathname, closeMobileNav]);

  return (
    <>
      {/* Mobile backdrop — tap to dismiss. Sits below the sticky TopBar
          (top-14) so the hamburger stays tappable to toggle the drawer. */}
      {mobileNavOpen ? (
        <div
          className="fixed inset-x-0 bottom-0 top-14 z-30 bg-black/40 sm:hidden"
          aria-hidden="true"
          onClick={closeMobileNav}
        />
      ) : null}
      <aside
        className={`fixed bottom-0 left-0 top-14 z-40 flex w-64 shrink-0 transform flex-col border-r border-border bg-card transition-transform duration-200 sm:static sm:z-auto sm:h-full sm:translate-x-0 sm:transition-[width] ${
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        } ${railCollapsed ? 'sm:w-14' : 'sm:w-56'}`}
      >
        <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-2">
          {menus.map((item) => (
            <SidebarNode
              key={item.id}
              item={item}
              lang={i18n.language}
              currentPath={location.pathname}
              collapsed={railCollapsed}
            />
          ))}
        </nav>
      </aside>
    </>
  );
}

function SidebarNode({
  item,
  lang,
  currentPath,
  collapsed,
}: {
  item: MenuItem;
  lang: string;
  currentPath: string;
  collapsed: boolean;
}) {
  const label = lang === 'en' ? item.labelEn : item.labelTh;
  const Icon = useMemo(() => resolveIcon(item.icon), [item.icon]);
  const hasChildren = !!item.children && item.children.length > 0;
  const [open, setOpen] = useState(true);

  if (hasChildren) {
    // In rail mode we collapse the whole subtree to an icon-only entry;
    // hovering surfaces the label via the title attribute. Avoids the
    // visual mess of a half-expanded tree fighting a 56px-wide rail.
    if (collapsed) {
      return (
        <div className="flex items-center justify-center rounded-md px-2 py-2 text-foreground/80" title={label}>
          {Icon ? <Icon className="h-4 w-4" /> : null}
        </div>
      );
    }
    return (
      <div className="flex flex-col">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center justify-between rounded-md px-2 py-2 text-sm font-medium text-foreground/80 hover:bg-secondary"
        >
          <span className="flex items-center gap-2">
            {Icon ? <Icon className="h-4 w-4" /> : null}
            {label}
          </span>
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        {open ? (
          <div className="ml-3 mt-1 flex flex-col gap-1 border-l border-border pl-2">
            {item.children!.map((child) => (
              <SidebarNode
                key={child.id}
                item={child}
                lang={lang}
                currentPath={currentPath}
                collapsed={collapsed}
              />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  const path = item.path ?? '#';
  const isActive = currentPath === path;
  return (
    <Link
      to={path}
      title={collapsed ? label : undefined}
      className={`flex items-center gap-2 rounded-md border-l-4 px-2 py-2 text-sm transition-colors ${
        collapsed ? 'justify-center' : ''
      } ${
        isActive
          ? 'border-accent bg-primary/10 text-primary'
          : 'border-transparent text-foreground/80 hover:bg-secondary hover:text-foreground'
      }`}
    >
      {Icon ? <Icon className="h-4 w-4" /> : null}
      {collapsed ? null : label}
    </Link>
  );
}

export default Sidebar;
