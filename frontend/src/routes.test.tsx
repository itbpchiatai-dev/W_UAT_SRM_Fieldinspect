/**
 * routes.tsx — round 8.0.5 append-only lock: /farmlog/records/:id (a bare
 * record id, no /preview suffix) must redirect straight to the read-only
 * One Page Preview, never mount the (now create-only) RecordForm.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes } from 'react-router-dom';
import { MODULE_ROUTES } from './routes';
import { useAuthStore } from './stores/auth';

vi.mock('./pages/farmlog/RecordPreview', () => ({
  RecordPreview: () => <div>__preview_page_for_id__</div>,
}));
vi.mock('./pages/farmlog/RecordForm', () => ({
  RecordForm: () => <div>__record_form_should_never_mount_here__</div>,
}));
vi.mock('./pages/farmlog/ExecutiveDashboard', () => ({
  ExecutiveDashboard: () => <div>__executive_overview_page__</div>,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>{MODULE_ROUTES}</Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ permissionKeys: new Set(['records.read', 'records.create']) });
});

describe('routes — /farmlog/records/:id append-only redirect (round 8.0.5)', () => {
  it('redirects a bare record id straight to the One Page Preview', async () => {
    renderAt('/farmlog/records/rec-1');

    expect(await screen.findByText('__preview_page_for_id__')).toBeTruthy();
    expect(screen.queryByText('__record_form_should_never_mount_here__')).toBeNull();
  });

  it('does not redirect /farmlog/records/new — that still mounts the create-only RecordForm', async () => {
    renderAt('/farmlog/records/new');

    expect(await screen.findByText('__record_form_should_never_mount_here__')).toBeTruthy();
    expect(screen.queryByText('__preview_page_for_id__')).toBeNull();
  });

  it('does not redirect /farmlog/records/:id/preview itself (no redirect loop)', async () => {
    renderAt('/farmlog/records/rec-1/preview');

    expect(await screen.findByText('__preview_page_for_id__')).toBeTruthy();
  });
});

describe('routes — /farmlog/records/new permission gate (round 8-4F)', () => {
  it('mounts RecordForm when the user has records.create (e.g. supplier owner)', async () => {
    useAuthStore.setState({ permissionKeys: new Set(['records.read', 'records.create']) });
    renderAt('/farmlog/records/new');

    expect(await screen.findByText('__record_form_should_never_mount_here__')).toBeTruthy();
  });

  it('denies without records.create even with other supplier perms — the gate is the permission, not a role name', async () => {
    // A supplier-shaped set (reads + plot writes) but NO records.create — i.e.
    // supplier:staff, or supplier:owner before this round's grant. The
    // RequirePermission gate keys off the permission alone, so RecordForm must
    // NOT mount and the inline 403 panel renders instead.
    useAuthStore.setState({
      permissionKeys: new Set([
        'records.read', 'plots.read', 'plots.create', 'plots.update', 'suppliers.read',
      ]),
    });
    renderAt('/farmlog/records/new');

    // The 403 panel renders (a heading), and the create form never mounts.
    expect(await screen.findByRole('heading')).toBeTruthy();
    expect(screen.queryByText('__record_form_should_never_mount_here__')).toBeNull();
  });
});

/**
 * Round 43 — /farmlog/executive is gated on its OWN key.
 *
 * plots.read would have been the easy reuse and is exactly the mistake
 * migration 0058 had to undo for the four reports: supplier:owner holds
 * plots.read, and this page is whole-company totals.
 */
describe('routes — /farmlog/executive is gated on executive_dashboard.read', () => {
  it('mounts the overview for a holder of the key', async () => {
    useAuthStore.setState({ permissionKeys: new Set(['executive_dashboard.read']) });
    renderAt('/farmlog/executive');

    expect(await screen.findByText('__executive_overview_page__')).toBeTruthy();
  });

  it('refuses a supplier-shaped permission set that only has plots.read', async () => {
    useAuthStore.setState({
      permissionKeys: new Set(['plots.read', 'suppliers.read', 'records.read']),
    });
    renderAt('/farmlog/executive');

    expect(await screen.findByRole('heading')).toBeTruthy();
    expect(screen.queryByText('__executive_overview_page__')).toBeNull();
  });
});
