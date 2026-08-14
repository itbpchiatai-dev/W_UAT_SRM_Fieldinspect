/**
 * AppLayout — standard protected-page shell.
 *
 *   [ TopBar                                              ]
 *   [ Sidebar ] [ <Outlet/> (page content)                ]
 *
 * Page content is responsible for its own padding / max-width — keep
 * the shell layout-only so pages stay portable across deployments.
 */
import { Outlet } from 'react-router-dom';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';

export function AppLayout() {
  // Fixed-height app shell: the viewport itself never scrolls. TopBar is a
  // fixed-height row; below it a flex row holds Sidebar + main, each scrolling
  // independently. `min-h-0` on that row is load-bearing — without it flex
  // children refuse to shrink and overflow gets clipped instead of scrolling.
  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background text-foreground">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex-1 overflow-y-auto overflow-x-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export default AppLayout;
