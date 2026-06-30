/**
 * Additional protected project routes.
 *
 * App.tsx mounts these inside the <RequireAuth/> + <AppLayout/> tree, so
 * any <Route> pushed here is auth-gated and receives the standard
 * top-bar + sidebar shell. Public routes (login, signup, etc.) should
 * be added directly in App.tsx, NOT here.
 */
import type { ReactElement } from 'react';
import { Route } from 'react-router-dom';
import { RequirePermission } from './components/RequirePermission';
import { Suppliers } from './pages/farmlog/admin/Suppliers';
import { Plots } from './pages/farmlog/admin/Plots';
import { Fields } from './pages/farmlog/admin/Fields';
import { MasterData } from './pages/farmlog/admin/MasterData';
import { RecordList } from './pages/farmlog/RecordList';
import { RecordForm } from './pages/farmlog/RecordForm';
import { RecordPreview } from './pages/farmlog/RecordPreview';

export const MODULE_ROUTES: ReactElement[] = [
  <Route key="farmlog-suppliers" path="/farmlog/admin/suppliers" element={
    <RequirePermission perm="suppliers.read"><Suppliers /></RequirePermission>
  } />,
  <Route key="farmlog-plots" path="/farmlog/admin/plots" element={
    <RequirePermission perm="plots.read"><Plots /></RequirePermission>
  } />,
  <Route key="farmlog-fields" path="/farmlog/admin/fields" element={
    <RequirePermission perm="fielddefs.read"><Fields /></RequirePermission>
  } />,
  <Route key="farmlog-masterdata" path="/farmlog/admin/masterdata" element={
    <RequirePermission perm="masterdata.read"><MasterData /></RequirePermission>
  } />,
  <Route key="farmlog-records" path="/farmlog/records" element={
    <RequirePermission perm="records.read"><RecordList /></RequirePermission>
  } />,
  <Route key="farmlog-record-new" path="/farmlog/records/new" element={
    <RequirePermission perm="records.create"><RecordForm /></RequirePermission>
  } />,
  <Route key="farmlog-record-preview" path="/farmlog/records/:id/preview" element={
    <RequirePermission perm="records.read"><RecordPreview /></RequirePermission>
  } />,
  <Route key="farmlog-record-detail" path="/farmlog/records/:id" element={
    <RequirePermission perm="records.read"><RecordForm /></RequirePermission>
  } />,
];
