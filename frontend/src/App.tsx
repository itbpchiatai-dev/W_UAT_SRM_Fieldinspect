import { lazy } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthBootstrap } from './components/AuthBootstrap';
import { RequireAuth } from './components/RequireAuth';
import { RequirePermission } from './components/RequirePermission';
import { AppLayout } from './components/Layout/AppLayout';
import { LazyRoute } from './components/LazyRoute';
import { MODULE_ROUTES } from './routes';

/**
 * Route-level code splitting (round 8-17A).
 *
 * The routing shell — AuthBootstrap, RequireAuth, RequirePermission,
 * AppLayout, LazyRoute — stays EAGER above: it is needed to decide what to
 * render at all, so deferring it would just add a waterfall before the
 * first paint.
 *
 * Every page below is a separate dynamic import, which is what keeps the
 * unauthenticated /public/inspect flow from shipping the Settings and
 * FarmLog admin screens to a field user on mobile data. Pages use NAMED
 * exports, so each loader maps the named export onto the `default` key
 * React.lazy expects — done here rather than by adding default exports to
 * ~20 page files.
 */
const Login = lazy(() => import('./pages/Login').then((m) => ({ default: m.Login })));
const AuthCallback = lazy(() =>
  import('./pages/AuthCallback').then((m) => ({ default: m.AuthCallback })),
);
const Approval = lazy(() => import('./pages/Approval').then((m) => ({ default: m.Approval })));
const PublicInspect = lazy(() =>
  import('./pages/farmlog/PublicInspect').then((m) => ({ default: m.PublicInspect })),
);
const Dashboard = lazy(() => import('./pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const SettingsIndex = lazy(() =>
  import('./pages/SettingsIndex').then((m) => ({ default: m.SettingsIndex })),
);
const SettingsUsers = lazy(() =>
  import('./pages/settings/Users').then((m) => ({ default: m.Users })),
);
const SettingsRoles = lazy(() =>
  import('./pages/settings/Roles').then((m) => ({ default: m.Roles })),
);
const SettingsPermissions = lazy(() =>
  import('./pages/settings/Permissions').then((m) => ({ default: m.Permissions })),
);
const SettingsMenus = lazy(() =>
  import('./pages/settings/Menus').then((m) => ({ default: m.Menus })),
);
const SettingsAuth = lazy(() =>
  import('./pages/settings/AuthSettings').then((m) => ({ default: m.AuthSettings })),
);
const SettingsSystemLogs = lazy(() =>
  import('./pages/settings/SystemLogs').then((m) => ({ default: m.SystemLogs })),
);
const SettingsActivityLogs = lazy(() =>
  import('./pages/settings/ActivityLogs').then((m) => ({ default: m.ActivityLogs })),
);
const SettingsDbConnections = lazy(() =>
  import('./pages/settings/DatabaseConnections').then((m) => ({ default: m.DatabaseConnections })),
);
const SettingsQuerySandbox = lazy(() =>
  import('./pages/settings/QuerySandbox').then((m) => ({ default: m.QuerySandbox })),
);

export default function App() {
  return (
    <AuthBootstrap>
      <div className="min-h-screen bg-background text-foreground">
        <Routes>
          {/* Public */}
          <Route path="/login" element={<LazyRoute><Login /></LazyRoute>} />
          {/* OAuth landing — Azure AD redirects here with code + state */}
          <Route path="/auth/callback" element={<LazyRoute><AuthCallback /></LazyRoute>} />
          {/* Approval token landing — public, the URL token is the capability */}
          <Route path="/approve/:token" element={<LazyRoute><Approval /></LazyRoute>} />
          {/* Unauthenticated field-assistant flow — scan QR, verify plot
              inspection code, submit one record. No login involved. */}
          <Route path="/public/inspect" element={<LazyRoute><PublicInspect /></LazyRoute>} />

          {/* Protected */}
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<LazyRoute><Dashboard /></LazyRoute>} />
            <Route path="/settings" element={<LazyRoute><SettingsIndex /></LazyRoute>} />
            <Route
              path="/settings/users"
              element={
                <RequirePermission perm="users.read">
                  <LazyRoute>
                    <SettingsUsers />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/roles"
              element={
                <RequirePermission perm="roles.read">
                  <LazyRoute>
                    <SettingsRoles />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/permissions"
              element={
                <RequirePermission perm="roles.read">
                  <LazyRoute>
                    <SettingsPermissions />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/menus"
              element={
                <RequirePermission perm="menus.read">
                  <LazyRoute>
                    <SettingsMenus />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/auth"
              element={
                <RequirePermission perm="admin_settings.read">
                  <LazyRoute>
                    <SettingsAuth />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/system-logs"
              element={
                <RequirePermission perm="system_logs.read">
                  <LazyRoute>
                    <SettingsSystemLogs />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/activity-logs"
              element={
                <RequirePermission perm="activity_logs.read">
                  <LazyRoute>
                    <SettingsActivityLogs />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/db-connections"
              element={
                <RequirePermission perm="db_connections.read">
                  <LazyRoute>
                    <SettingsDbConnections />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            <Route
              path="/settings/query-sandbox"
              element={
                <RequirePermission perm="db_connections.query">
                  <LazyRoute>
                    <SettingsQuerySandbox />
                  </LazyRoute>
                </RequirePermission>
              }
            />
            {MODULE_ROUTES}
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </div>
    </AuthBootstrap>
  );
}

function NotFound() {
  return (
    <main className="flex min-h-[50vh] items-center justify-center px-4">
      <p className="text-muted-foreground">404 — Page not found</p>
    </main>
  );
}
