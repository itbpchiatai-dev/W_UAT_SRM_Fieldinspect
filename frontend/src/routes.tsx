/**
 * Additional protected project routes.
 *
 * App.tsx mounts these inside the <RequireAuth/> + <AppLayout/> tree, so
 * any <Route> pushed here is auth-gated and receives the standard
 * top-bar + sidebar shell. Public routes (login, signup, etc.) should
 * be added directly in App.tsx, NOT here.
 */
import { lazy, type ReactElement } from 'react';
import { Navigate, Route, useParams } from 'react-router-dom';
import { RequirePermission } from './components/RequirePermission';
import { LazyRoute } from './components/LazyRoute';

/**
 * Route-level code splitting (round 8-17A) — see App.tsx for the rationale.
 * These FarmLog admin/record/report screens are the bulk of the app and are
 * reachable only after login, so none of them belong in the bundle a
 * /public/inspect visitor downloads.
 */
const Suppliers = lazy(() =>
  import('./pages/farmlog/admin/Suppliers').then((m) => ({ default: m.Suppliers })),
);
const Plots = lazy(() =>
  import('./pages/farmlog/admin/Plots').then((m) => ({ default: m.Plots })),
);
const PlotDetail = lazy(() =>
  import('./pages/farmlog/admin/PlotDetail').then((m) => ({ default: m.PlotDetail })),
);
const Fields = lazy(() =>
  import('./pages/farmlog/admin/Fields').then((m) => ({ default: m.Fields })),
);
const MasterData = lazy(() =>
  import('./pages/farmlog/admin/MasterData').then((m) => ({ default: m.MasterData })),
);
const InspectionProtocols = lazy(() =>
  import('./pages/farmlog/admin/InspectionProtocols').then((m) => ({
    default: m.InspectionProtocols,
  })),
);
const RecordList = lazy(() =>
  import('./pages/farmlog/RecordList').then((m) => ({ default: m.RecordList })),
);
const RecordForm = lazy(() =>
  import('./pages/farmlog/RecordForm').then((m) => ({ default: m.RecordForm })),
);
const RecordPreview = lazy(() =>
  import('./pages/farmlog/RecordPreview').then((m) => ({ default: m.RecordPreview })),
);
const ReportsPage = lazy(() =>
  import('./pages/farmlog/reports/ReportsPage').then((m) => ({ default: m.ReportsPage })),
);

/**
 * Round 8.0.5 append-only lock — an existing record has no edit view.
 * /farmlog/records/:id (a bare record id, no /preview suffix) redirects
 * straight to the read-only One Page Preview instead of ever mounting
 * RecordForm, which is create-only now (route below, /new path only).
 */
function RecordDetailRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/farmlog/records/${id}/preview`} replace />;
}

export const MODULE_ROUTES: ReactElement[] = [
  <Route key="farmlog-suppliers" path="/farmlog/admin/suppliers" element={
    <RequirePermission perm="suppliers.read"><LazyRoute><Suppliers /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-plots" path="/farmlog/admin/plots" element={
    <RequirePermission perm="plots.read"><LazyRoute><Plots /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-plot-detail" path="/farmlog/admin/plots/:plotId" element={
    <RequirePermission perm="plots.read"><LazyRoute><PlotDetail /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-fields" path="/farmlog/admin/fields" element={
    <RequirePermission perm="fielddefs.read"><LazyRoute><Fields /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-masterdata" path="/farmlog/admin/masterdata" element={
    <RequirePermission perm="masterdata.read"><LazyRoute><MasterData /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-inspection-protocols" path="/farmlog/admin/inspection-protocols" element={
    <RequirePermission perm="masterdata.read"><LazyRoute><InspectionProtocols /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-records" path="/farmlog/records" element={
    <RequirePermission perm="records.read"><LazyRoute><RecordList /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-record-new" path="/farmlog/records/new" element={
    <RequirePermission perm="records.create"><LazyRoute><RecordForm /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-record-preview" path="/farmlog/records/:id/preview" element={
    <RequirePermission perm="records.read"><LazyRoute><RecordPreview /></LazyRoute></RequirePermission>
  } />,
  <Route key="farmlog-record-detail" path="/farmlog/records/:id" element={
    <RequirePermission perm="records.read"><RecordDetailRedirect /></RequirePermission>
  } />,
  <Route key="farmlog-report-plot-status" path="/farmlog/reports/plot-status" element={
    <RequirePermission perm="plots.read"><LazyRoute><ReportsPage /></LazyRoute></RequirePermission>
  } />,
];
