/**
 * Users page — admin password reset UX + Add Local User error recovery
 * (round 8-23B).
 *
 * Two behaviours under test:
 *
 *  1. The "ตั้งรหัสผ่านใหม่" entry point and its modal, including the three
 *     permission/provider/self gates the backend also enforces, and the
 *     UTF-8 72-BYTE client guard (bcrypt's real limit — round 8-23A.1).
 *
 *  2. The Add Local User hang. Before this round onSubmit awaited
 *     createUser() with no try/catch and the form had no error surface at
 *     all: react-hook-form v7 catches the rejection, clears isSubmitting,
 *     then re-throws it (`catch(e){s=e}` … `if(s) throw s`), so the user
 *     saw a stopped spinner, an open modal, and NOTHING else.
 *
 * Password constants here are obviously-fake local test values; assertions
 * about them are NOT-in checks proving they never leak.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Users } from './Users';
import { useAuthStore } from '../../stores/auth';
import type { UserDetail, UserSummary } from '../../types/auth';

const listUsersMock = vi.fn();
const getUserMock = vi.fn();
const createUserMock = vi.fn();
const updateUserMock = vi.fn();
const resetUserPasswordMock = vi.fn();

vi.mock('../../api/users', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/users')>();
  return {
    ...actual,
    listUsers: (...a: unknown[]) => listUsersMock(...a),
    getUser: (...a: unknown[]) => getUserMock(...a),
    createUser: (...a: unknown[]) => createUserMock(...a),
    updateUser: (...a: unknown[]) => updateUserMock(...a),
    resetUserPassword: (...a: unknown[]) => resetUserPasswordMock(...a),
    deactivateUser: vi.fn(),
    bulkApproveUsers: vi.fn(),
    setUserOverride: vi.fn(),
  };
});

vi.mock('../../api/roles', () => ({ listRoles: () => Promise.resolve([]) }));
vi.mock('../../api/suppliers', () => ({ listSuppliers: () => Promise.resolve([]) }));
vi.mock('../../api/permissions', () => ({
  listPermissions: () => Promise.resolve([]),
  groupByCategory: () => ({}),
}));

// i18n returns the key path, so assertions target keys (stable) rather
// than translated copy (which the product team may reword).
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && 'email' in opts ? `${key}:${opts.email}` : key,
  }),
}));

const CALLER_ID = 'caller-0000';
const TARGET_ID = 'target-1111';
const SECRET = 'Correct-Horse-Battery-42';

const RESET_ACTION = 'settings.users.resetPassword.action';
const AZURE_NOTICE = 'settings.users.resetPassword.azureNotice';
const SUBMIT = 'settings.users.resetPassword.submit';

function summary(overrides: Partial<UserSummary> = {}): UserSummary {
  return {
    id: TARGET_ID,
    email: 'target@example.invalid',
    fullName: 'Target User',
    authProvider: 'local',
    isActive: true,
    isApproved: true,
    lastLoginAt: null,
    roles: [],
    supplierId: null,
    isSupplierAdmin: false,
    ...overrides,
  };
}

function detail(overrides: Partial<UserDetail> = {}): UserDetail {
  return {
    ...summary(overrides as Partial<UserSummary>),
    emailVerified: true,
    businessUnitIds: [],
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  } as UserDetail;
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Users />
    </QueryClientProvider>,
  );
}

/** Open the Edit modal for the single listed user. Round 8-25H — the row's
 * edit action moved behind one ActionMenu trigger, so open that first; the
 * menu item is then identified by its accessible name (t('common.edit')),
 * not by DOM position. */
async function openEditor() {
  await screen.findByText('target@example.invalid');
  fireEvent.click(await screen.findByRole('button', { name: /common\.actions/ }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'common.edit' }));
  await screen.findByRole('dialog');
}

function setPerms(...keys: string[]) {
  useAuthStore.setState({ permissionKeys: new Set(keys) });
}

beforeEach(() => {
  listUsersMock.mockReset();
  getUserMock.mockReset();
  createUserMock.mockReset();
  updateUserMock.mockReset();
  resetUserPasswordMock.mockReset();

  listUsersMock.mockResolvedValue([summary()]);
  getUserMock.mockResolvedValue(detail());
  resetUserPasswordMock.mockResolvedValue({
    status: 'ok', userId: TARGET_ID, authVersion: 1, sessionsInvalidated: true,
  });

  setPerms('users.reset_password');
  useAuthStore.setState({
    user: {
      id: CALLER_ID,
      email: 'caller@example.invalid',
      fullName: 'Caller',
      authProvider: 'local',
      isActive: true,
      emailVerified: true,
      roles: [],
    },
  });
});

// --- gates ---------------------------------------------------------------

describe('reset-password gates', () => {
  it('shows the action for a LOCAL target when the caller holds users.reset_password', async () => {
    renderPage();
    await openEditor();
    expect(await screen.findByText(RESET_ACTION)).toBeTruthy();
  });

  it('hides the action when the caller lacks users.reset_password', async () => {
    setPerms();
    renderPage();
    await openEditor();
    expect(screen.queryByText(RESET_ACTION)).toBeNull();
  });

  it('hides the action when the target IS the caller (no self-reset)', async () => {
    listUsersMock.mockResolvedValue([summary({ id: CALLER_ID })]);
    getUserMock.mockResolvedValue(detail({ id: CALLER_ID }));
    renderPage();
    await openEditor();
    expect(screen.queryByText(RESET_ACTION)).toBeNull();
  });

  it('shows the Microsoft notice and NO reset form for an azure_ad target', async () => {
    listUsersMock.mockResolvedValue([summary({ authProvider: 'azure_ad' })]);
    getUserMock.mockResolvedValue(detail({ authProvider: 'azure_ad' }));
    renderPage();
    await openEditor();
    expect(await screen.findByText(AZURE_NOTICE)).toBeTruthy();
    expect(screen.queryByText(RESET_ACTION)).toBeNull();
  });
});

// --- modal validation ----------------------------------------------------

async function openResetModal() {
  renderPage();
  await openEditor();
  fireEvent.click(await screen.findByText(RESET_ACTION));
  return await screen.findByRole('dialog', { name: 'settings.users.resetPassword.title' });
}

function fillReset(dialog: HTMLElement, pw: string, confirm = pw) {
  const inputs = within(dialog).getAllByDisplayValue('');
  fireEvent.change(inputs[0], { target: { value: pw } });
  fireEvent.change(inputs[1], { target: { value: confirm } });
}

describe('reset-password modal validation', () => {
  it('blocks mismatched passwords without calling the API', async () => {
    const dialog = await openResetModal();
    fillReset(dialog, SECRET, SECRET + 'x');
    fireEvent.click(within(dialog).getByText(SUBMIT));

    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'settings.users.resetPassword.errors.mismatch',
    );
    expect(resetUserPasswordMock).not.toHaveBeenCalled();
  });

  it('blocks a password shorter than the policy minimum', async () => {
    const dialog = await openResetModal();
    fillReset(dialog, 'Short1!');
    fireEvent.click(within(dialog).getByText(SUBMIT));

    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'settings.users.resetPassword.errors.tooShort',
    );
    expect(resetUserPasswordMock).not.toHaveBeenCalled();
  });

  it('accepts a password of EXACTLY 72 UTF-8 bytes', async () => {
    const pw = 'Aa1' + 'ก'.repeat(23); // 3 + 69 = 72 bytes
    expect(new TextEncoder().encode(pw).length).toBe(72);

    const dialog = await openResetModal();
    fillReset(dialog, pw);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    await waitFor(() => expect(resetUserPasswordMock).toHaveBeenCalledWith(TARGET_ID, pw));
  });

  it('blocks a password of 73 UTF-8 bytes', async () => {
    const pw = 'Aa1!' + 'ก'.repeat(23); // 4 + 69 = 73 bytes
    expect(new TextEncoder().encode(pw).length).toBe(73);

    const dialog = await openResetModal();
    fillReset(dialog, pw);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'settings.users.resetPassword.errors.tooLong',
    );
    expect(resetUserPasswordMock).not.toHaveBeenCalled();
  });

  it('blocks a SHORT Thai password that still exceeds 72 bytes (the 8-23A.1 case)', async () => {
    const pw = 'รหัสผ่านยาวมากของฉันนะจ๊ะA1'; // 27 chars, 77 bytes
    expect(pw.length).toBeLessThan(72);
    expect(new TextEncoder().encode(pw).length).toBeGreaterThan(72);

    const dialog = await openResetModal();
    fillReset(dialog, pw);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'settings.users.resetPassword.errors.tooLong',
    );
    expect(resetUserPasswordMock).not.toHaveBeenCalled();
  });

  it('never truncates an over-long password before sending', async () => {
    const pw = 'Aa1!' + 'ก'.repeat(23);
    const dialog = await openResetModal();
    fillReset(dialog, pw);
    fireEvent.click(within(dialog).getByText(SUBMIT));
    await within(dialog).findByRole('alert');
    // Rejected outright — never silently shortened and sent.
    expect(resetUserPasswordMock).not.toHaveBeenCalled();
  });
});

// --- success / failure ---------------------------------------------------

describe('reset-password outcomes', () => {
  it('on success closes the modal, wipes the fields, and reports session revocation', async () => {
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'settings.users.resetPassword.title' })).toBeNull(),
    );
    expect((await screen.findByRole('status')).textContent).toContain(
      'settings.users.resetPassword.success',
    );
    // No input anywhere still holds the secret.
    const leaked = screen.queryAllByDisplayValue(SECRET);
    expect(leaked).toHaveLength(0);
  });

  it('on a 400 keeps the modal open, stops the spinner, and shows the backend message', async () => {
    resetUserPasswordMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: 'รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย' } },
    });
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    expect((await within(dialog).findByRole('alert')).textContent).toContain(
      'รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย',
    );
    // Still open, and the submit button is interactive again.
    expect(screen.getByRole('dialog', { name: 'settings.users.resetPassword.title' })).toBeTruthy();
    await waitFor(() =>
      expect((within(dialog).getByText(SUBMIT).closest('button') as HTMLButtonElement).disabled)
        .toBe(false),
    );
  });

  it('on a network error (no response) shows a safe fallback, not a raw error', async () => {
    resetUserPasswordMock.mockRejectedValue(new Error('Network Error'));
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('settings.users.resetPassword.errors.failed');
    expect(alert.textContent).not.toContain('Network Error');
  });

  it('an error message never contains the submitted password', async () => {
    resetUserPasswordMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: 'รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย' } },
    });
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).not.toContain(SECRET);
  });

  it('does not fire a second request on double submit', async () => {
    let release: (v: unknown) => void = () => {};
    resetUserPasswordMock.mockImplementation(
      () => new Promise((res) => { release = res; }),
    );
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    const submit = within(dialog).getByText(SUBMIT);

    fireEvent.click(submit);
    fireEvent.click(submit);
    fireEvent.click(submit);

    await waitFor(() => expect(resetUserPasswordMock).toHaveBeenCalledTimes(1));
    release({ status: 'ok', userId: TARGET_ID, authVersion: 1, sessionsInvalidated: true });
  });

  it('cannot be dismissed while the request is in flight', async () => {
    resetUserPasswordMock.mockImplementation(() => new Promise(() => {}));
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));

    await waitFor(() =>
      expect((within(dialog).getByText('common.cancel').closest('button') as HTMLButtonElement).disabled)
        .toBe(true),
    );
    fireEvent.click(dialog); // backdrop click
    expect(screen.getByRole('dialog', { name: 'settings.users.resetPassword.title' })).toBeTruthy();
  });

  it('never writes the password to storage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const dialog = await openResetModal();
    fillReset(dialog, SECRET);
    fireEvent.click(within(dialog).getByText(SUBMIT));
    await waitFor(() => expect(resetUserPasswordMock).toHaveBeenCalled());

    const persisted = setItem.mock.calls.map((c) => String(c[1])).join('|');
    expect(persisted).not.toContain(SECRET);
    setItem.mockRestore();
  });
});

// --- Add Local User error recovery --------------------------------------

async function openCreateModal() {
  renderPage();
  // 'settings.users.new' labels BOTH the page button and the modal
  // heading, so target the button by role and await the dialog itself.
  fireEvent.click(await screen.findByRole('button', { name: 'settings.users.new' }));
  return await screen.findByRole('dialog');
}

function fillCreate(dialog: HTMLElement, password: string) {
  // RHF's register() puts a `name` on each input, which is unambiguous —
  // querying by label text is not (the same key labels a table header).
  const q = <T extends Element>(sel: string) => dialog.querySelector(sel) as T;
  fireEvent.change(q<HTMLInputElement>('input[name="email"]'), {
    target: { value: 'new.user@example.invalid' },
  });
  fireEvent.change(q<HTMLInputElement>('input[name="fullName"]'), {
    target: { value: 'New User' },
  });
  fireEvent.change(q<HTMLInputElement>('input[name="password"]'), {
    target: { value: password },
  });
}

describe('Add Local User error recovery', () => {
  it('creates a local user successfully', async () => {
    createUserMock.mockResolvedValue(detail());
    const dialog = await openCreateModal();
    fillCreate(dialog, SECRET);
    fireEvent.click(within(dialog).getByText('common.save'));

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
    expect(createUserMock.mock.calls[0][0]).toMatchObject({
      email: 'new.user@example.invalid',
      authProvider: 'local',
      password: SECRET,
    });
  });

  it.each([
    [400, 'รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย'],
    [409, 'Email already exists'],
    [422, null],
  ])('shows an error and keeps the modal open on HTTP %i', async (status, detailMsg) => {
    createUserMock.mockRejectedValue({
      isAxiosError: true,
      response: {
        status,
        data: detailMsg ? { detail: detailMsg } : { detail: [{ msg: 'x', loc: ['body'] }] },
      },
    });
    const dialog = await openCreateModal();
    fillCreate(dialog, SECRET);
    fireEvent.click(within(dialog).getByText('common.save'));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toBeTruthy();
    // A pydantic 422 array must NOT be rendered raw.
    if (detailMsg) expect(alert.textContent).toContain(detailMsg);
    else expect(alert.textContent).toContain('settings.users.saveError');
    // Modal still open; non-password fields preserved.
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(within(dialog).getByDisplayValue('new.user@example.invalid')).toBeTruthy();
  });

  it('stops the spinner and stays open on a network error', async () => {
    createUserMock.mockRejectedValue(new Error('Network Error'));
    const dialog = await openCreateModal();
    fillCreate(dialog, SECRET);
    fireEvent.click(within(dialog).getByText('common.save'));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('settings.users.saveError');
    expect(alert.textContent).not.toContain('Network Error');
    await waitFor(() =>
      expect((within(dialog).getByText('common.save').closest('button') as HTMLButtonElement).disabled)
        .toBe(false),
    );
  });

  it('an Add User failure never surfaces the submitted password', async () => {
    createUserMock.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: 'รหัสผ่านไม่ผ่านเกณฑ์ความปลอดภัย' } },
    });
    const dialog = await openCreateModal();
    fillCreate(dialog, SECRET);
    fireEvent.click(within(dialog).getByText('common.save'));

    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).not.toContain(SECRET);
  });

  it('omits the password entirely for an azure_ad account', async () => {
    createUserMock.mockResolvedValue(detail({ authProvider: 'azure_ad' }));
    const dialog = await openCreateModal();
    fillCreate(dialog, SECRET);
    const providerSelect = dialog.querySelector('select[name="authProvider"]') as HTMLSelectElement;
    fireEvent.change(providerSelect, { target: { value: 'azure_ad' } });
    fireEvent.click(within(dialog).getByText('common.save'));

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
    expect(createUserMock.mock.calls[0][0].password).toBeUndefined();
  });
});

// Round 8-25H — row actions moved behind one ActionMenu trigger, matching
// the farmlog admin tables.
describe('row actions — ActionMenu (round 8-25H)', () => {
  it('hides both actions behind one trigger until opened', async () => {
    renderPage();
    await screen.findByText('target@example.invalid');

    expect(screen.queryByRole('menuitem', { name: 'common.edit' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'settings.users.deactivate' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'common.edit' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'settings.users.deactivate' })).toBeTruthy();
  });

  it('disables the deactivate item for an already-inactive user', async () => {
    listUsersMock.mockResolvedValue([summary({ isActive: false })]);
    renderPage();
    await screen.findByText('target@example.invalid');

    fireEvent.click(screen.getByRole('button', { name: /common\.actions/ }));

    expect(screen.getByRole('menuitem', { name: 'settings.users.deactivate' }).hasAttribute('disabled'))
      .toBe(true);
  });
});

// Round 8-25J — before this round, resetting a deactivated user's password
// (or editing any other field) never reactivated them: the row's ActionMenu
// only offers "ปิดการใช้งาน" (disabled once inactive) and the edit form had
// no isActive control at all. The toggle here closes that gap.
describe('activate/deactivate toggle in the edit form (round 8-25J)', () => {
  it('shows the toggle, reflecting the target\'s current isActive state', async () => {
    renderPage();
    await openEditor();

    // The list header behind the modal uses this SAME i18n key for its own
    // "ใช้งาน" column — waitFor a COUNT of 2 (list header + the form's own
    // label), not just findAllByText, which would resolve the instant the
    // first (list-only) match appears, before existing/defaults load.
    await waitFor(() => expect(screen.getAllByText('settings.users.fields.active').length).toBe(2));
    expect(await screen.findByRole('button', { name: 'settings.users.deactivate' })).toBeTruthy();
  });

  it('hides the toggle entirely when the target IS the caller (no self-deactivation)', async () => {
    listUsersMock.mockResolvedValue([summary({ id: CALLER_ID })]);
    getUserMock.mockResolvedValue(detail({ id: CALLER_ID }));
    renderPage();
    await openEditor();
    // Let the getUser() query settle before asserting an absence — an early
    // query means "not rendered yet", not "correctly hidden".
    await waitFor(() => expect(getUserMock).toHaveBeenCalled());

    // Only the list header's column title remains — not the form's copy too.
    expect(screen.getAllByText('settings.users.fields.active').length).toBe(1);
    expect(screen.queryByRole('button', { name: 'settings.users.deactivate' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'settings.users.activate' })).toBeNull();
  });

  it('flips to inactive after confirm, and Save sends isActive:false', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    updateUserMock.mockResolvedValue(detail());
    renderPage();
    await openEditor();

    fireEvent.click(await screen.findByRole('button', { name: 'settings.users.deactivate' }));
    // The toggle re-labels itself once off — proves the flip actually
    // landed in form state, not just that confirm() was called.
    expect(await screen.findByRole('button', { name: 'settings.users.activate' })).toBeTruthy();

    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(updateUserMock).toHaveBeenCalledOnce());
    expect(updateUserMock.mock.calls[0][1]).toMatchObject({ isActive: false });
  });

  it('does not flip when the confirm dialog is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderPage();
    await openEditor();

    fireEvent.click(await screen.findByRole('button', { name: 'settings.users.deactivate' }));

    // Still on — the cancelled confirm must not have changed form state.
    expect(screen.getByRole('button', { name: 'settings.users.deactivate' })).toBeTruthy();
  });

  it('omits isActive from the payload entirely when saving your own profile', async () => {
    listUsersMock.mockResolvedValue([summary({ id: CALLER_ID })]);
    getUserMock.mockResolvedValue(detail({ id: CALLER_ID }));
    updateUserMock.mockResolvedValue(detail({ id: CALLER_ID }));
    renderPage();
    await openEditor();
    // Neither the reset-password section nor the isActive toggle render for
    // a self-target (both correctly hidden), so unlike the other tests in
    // this block there is no on-screen element whose APPEARANCE proves
    // getUser() has resolved and react-hook-form has reset its `values` from
    // it. Wait on the fullName field's actual value instead — clicking Save
    // before that lands submits the form's still-blank initial defaults and
    // fails validation (email/fullName both "required"), which looks
    // identical to "updateUser was never called" from the outside.
    await screen.findByDisplayValue('Target User');

    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(updateUserMock).toHaveBeenCalledOnce());
    expect(updateUserMock.mock.calls[0][1]).not.toHaveProperty('isActive');
  });
});
