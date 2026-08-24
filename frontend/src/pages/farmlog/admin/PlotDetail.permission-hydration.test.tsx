/**
 * Round 8-9F diagnostic — the permission chain that decides whether the
 * "ตั้งรหัส Supplier ตรวจแปลง" button exists at all:
 *
 *   GET /me/permissions → authStore.hydrateFromServer() → permissionKeys Set
 *   → useHasPermission('plots.update') → PlotDetail canUpdatePlot
 *   → InspectionCredentialSection's button
 *
 * PlotDetail.test.tsx mocks `useHasPermission` wholesale, so every existing
 * gating test asserts against a hand-built Set and NOTHING in the suite proves
 * that a real /me/permissions payload actually reaches the button. That gap is
 * exactly where a "backend says I have plots.update but the button is missing"
 * report would live, so this file deliberately uses the REAL hook and the REAL
 * Zustand store, and only mocks the HTTP layer.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlotDetail } from './PlotDetail';
import { useAuthStore } from '../../../stores/auth';
import type { PlotDetail as PlotDetailType } from '../../../api/plots';

const getPlotMock = vi.fn();
const listPlotCyclesMock = vi.fn();
const getPlotAccessPhonesMock = vi.fn();
const getPlotInspectionCredentialMock = vi.fn();
const getMePermissionsMock = vi.fn();
const getMeMock = vi.fn();
const getMyMenusMock = vi.fn();

vi.mock('../../../api/plots', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/plots')>();
  return {
    ...actual,
    getPlot: (...args: unknown[]) => getPlotMock(...args),
    listPlotCycles: (...args: unknown[]) => listPlotCyclesMock(...args),
    getPlotAccessPhones: (...args: unknown[]) => getPlotAccessPhonesMock(...args),
    getPlotInspectionAccessCredential: (...args: unknown[]) => getPlotInspectionCredentialMock(...args),
  };
});

vi.mock('../../../api/records', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/records')>();
  return { ...actual, listRecords: () => Promise.resolve([]), getRecord: () => Promise.resolve(null) };
});

vi.mock('../../../api/masterdata', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/masterdata')>();
  return { ...actual, listMasterData: () => Promise.resolve([]) };
});

// The ONLY thing stubbed on the auth path: the HTTP calls. The store action,
// the Set it builds, and the hook that reads it are all the real ones.
vi.mock('../../../api/me', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/me')>();
  return {
    ...actual,
    getMe: (...args: unknown[]) => getMeMock(...args),
    getMyPermissions: (...args: unknown[]) => getMePermissionsMock(...args),
    getMyMenus: (...args: unknown[]) => getMyMenusMock(...args),
  };
});

/** The real shape of a super-admin answer: /me/permissions returns
 * { permissionKeys: [...] } and api/me.ts unwraps it to a string[]. */
const SUPER_ADMIN_PERMISSION_KEYS = [
  'plots.read', 'plots.create', 'plots.update', 'plots.delete', 'plots.assign',
  'records.read', 'records.create', 'suppliers.read', 'users.read',
];

function basePlot(overrides: Partial<PlotDetailType> = {}): PlotDetailType {
  return {
    id: 'plot-1',
    supplierId: 'sup-1',
    supplierCode: 'SUP010',
    code: 'P001',
    name: 'แปลงลุงสิบ',
    plotCode: 'SUP010-P001',
    plotName: 'แปลงลุงสิบ',
    supplierName: 'Supplier Ten',
    isActive: true,
    createdAt: '2026-07-01T10:00:00Z',
    qrKey: null,
    primaryPhone: null,
    additionalPhones: [],
    currentCrop: null, currentVariety: null, currentLotNo: null,
    currentPlantingDate: null, currentStage: null, currentYieldPct: null,
    lastInspectedAt: null, lastInspectionRecordId: null,
    fieldPrepScore: null, weatherScore: null, careScore: null,
    varietyResistanceScore: null,
    ...overrides,
  } as PlotDetailType;
}

function renderPlotDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/farmlog/admin/plots/plot-1']}>
        <Routes>
          <Route path="/farmlog/admin/plots/:plotId" element={<PlotDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getPlotMock.mockReset();
  listPlotCyclesMock.mockReset();
  getPlotAccessPhonesMock.mockReset();
  getPlotInspectionCredentialMock.mockReset();
  getMePermissionsMock.mockReset();
  getMeMock.mockReset();
  getMyMenusMock.mockReset();

  getPlotMock.mockResolvedValue(basePlot());
  listPlotCyclesMock.mockResolvedValue([]);
  getPlotAccessPhonesMock.mockResolvedValue([]);
  // The state of the 10 SUP010 plots this round is blocked on: eligible, no
  // password yet.
  getPlotInspectionCredentialMock.mockResolvedValue({
    configured: false, credentialVersion: null, updatedAt: null,
  });
  getMeMock.mockResolvedValue({ id: 'u1', email: 'admin@example.com', isActive: true });
  getMyMenusMock.mockResolvedValue([]);
  getMePermissionsMock.mockResolvedValue(SUPER_ADMIN_PERMISSION_KEYS);

  // Start every test from a genuinely empty session — no persisted state
  // exists to leak between tests (see the storage-policy test below).
  useAuthStore.setState({
    user: null, permissionKeys: new Set<string>(), menus: [],
    isAuthenticated: false, isLoading: false,
  });
});

// --- the hydration chain ----------------------------------------------------

describe('permission hydration — /me/permissions reaches useHasPermission', () => {
  it('hydrateFromServer puts plots.update into the store as a plain string key', async () => {
    await useAuthStore.getState().hydrateFromServer();

    const keys = useAuthStore.getState().permissionKeys;
    expect(keys.has('plots.update')).toBe(true);
    // Set<string>, not Set<{key}> — a shape mismatch here is the classic way a
    // "backend says true, UI says false" bug happens, and `has()` would then
    // silently return false for every key.
    expect([...keys].every((k) => typeof k === 'string')).toBe(true);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('a failed /me/permissions leaves NO stale permissions behind', async () => {
    await useAuthStore.getState().hydrateFromServer();
    expect(useAuthStore.getState().permissionKeys.has('plots.update')).toBe(true);

    getMePermissionsMock.mockRejectedValue(new Error('boom'));
    await useAuthStore.getState().hydrateFromServer();

    // Fails closed: anonymous, not "keep the last good answer".
    expect(useAuthStore.getState().permissionKeys.size).toBe(0);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it('refreshPermissions replaces the set wholesale (a revoked key disappears)', async () => {
    await useAuthStore.getState().hydrateFromServer();

    getMePermissionsMock.mockResolvedValue(['plots.read']);
    await useAuthStore.getState().refreshPermissions();

    expect(useAuthStore.getState().permissionKeys.has('plots.update')).toBe(false);
    expect(useAuthStore.getState().permissionKeys.has('plots.read')).toBe(true);
  });

  it('no permission cache is persisted anywhere a stale answer could survive', () => {
    // The store is in-memory by design (stores/auth.ts docstring): only the
    // access token is mirrored, and only into sessionStorage. If permissions
    // ever start being persisted, a stale "no plots.update" could outlive a
    // role change and hide the button — this test is the tripwire.
    const dump = (s: Storage | undefined): string => {
      if (!s) return '';
      let out = '';
      for (let i = 0; i < s.length; i++) {
        const k = s.key(i);
        if (k) out += `${k}=${s.getItem(k)}|`;
      }
      return out;
    };
    const all = dump(globalThis.localStorage) + dump(globalThis.sessionStorage);
    expect(all).not.toContain('plots.update');
    expect(all).not.toContain('permissionKeys');
  });
});

// --- the button itself ------------------------------------------------------

describe('PlotDetail — the "ตั้งรหัส Supplier ตรวจแปลง" button with REAL permission plumbing', () => {
  it('renders the button after a real /me/permissions hydration containing plots.update', async () => {
    await useAuthStore.getState().hydrateFromServer();

    renderPlotDetail();

    expect(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy();
    expect(getMePermissionsMock).toHaveBeenCalled();
  });

  it('opens the modal when that button is clicked (configured=false is not a falsy gate)', async () => {
    await useAuthStore.getState().hydrateFromServer();

    renderPlotDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' }));

    // The trigger passes `configured` (false here) into state; the modal must
    // be gated on `!== null`, not on truthiness, or a plot with NO password —
    // exactly the case this rollout needs — could never open it.
    expect(await screen.findByRole('heading', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy();
  });

  it('hides the button when the hydrated permissions genuinely lack plots.update', async () => {
    getMePermissionsMock.mockResolvedValue(['plots.read']);
    await useAuthStore.getState().hydrateFromServer();

    renderPlotDetail();

    // The section still renders (status is readable by plots.read)...
    expect(await screen.findByText('รหัส Supplier ตรวจแปลง')).toBeTruthy();
    // ...but there is no way to act on it.
    expect(screen.queryByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeNull();
  });

  it('hides the button while the credential status is still loading, then shows it', async () => {
    let resolveStatus: (v: unknown) => void = () => {};
    getPlotInspectionCredentialMock.mockReturnValue(
      new Promise((resolve) => { resolveStatus = resolve; }),
    );
    await useAuthStore.getState().hydrateFromServer();

    renderPlotDetail();

    expect(await screen.findByText('กำลังโหลดสถานะรหัส Supplier ตรวจแปลง…')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /ตั้งรหัส Supplier ตรวจแปลง/ })).toBeNull();

    resolveStatus({ configured: false, credentialVersion: null, updatedAt: null });

    expect(await screen.findByRole('button', { name: 'ตั้งรหัส Supplier ตรวจแปลง' })).toBeTruthy();
  });

  it('hides the button when the credential-status GET fails, and offers a retry instead', async () => {
    // The other way a permitted admin sees no button: the status query errored,
    // so the section cannot tell "set" from "change". Distinguishable on screen
    // from the permission case above by the error text.
    getPlotInspectionCredentialMock.mockRejectedValue(new Error('status down'));
    await useAuthStore.getState().hydrateFromServer();

    renderPlotDetail();

    expect(await screen.findByText('โหลดสถานะรหัส Supplier ตรวจแปลงไม่สำเร็จ')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /ตั้งรหัส Supplier ตรวจแปลง/ })).toBeNull();
  });
});
