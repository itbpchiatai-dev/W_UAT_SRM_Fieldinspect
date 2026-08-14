/**
 * InspectionProtocols admin (round 5.5) — renders the 5 stages grouped, and
 * an edit saves changed labels via PATCH.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { InspectionProtocols } from './InspectionProtocols';

const fetchAdminMock = vi.fn();
const bulkUpdateMock = vi.fn();

vi.mock('../../../api/inspectionProtocols', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/inspectionProtocols')>();
  return {
    ...actual,
    fetchAdminInspectionProtocols: (...a: unknown[]) => fetchAdminMock(...a),
    bulkUpdateInspectionProtocolCriteria: (...a: unknown[]) => bulkUpdateMock(...a),
  };
});

let canUpdate = true;
vi.mock('../../../hooks/useHasPermission', () => ({
  useHasPermission: () => canUpdate,
}));

function stage(growthStage: string, labels: string[], idPrefix: string) {
  const slots = ['fieldPrepScore', 'weatherScore', 'careScore', 'varietyResistanceScore'];
  return {
    growthStage,
    criteria: slots.map((slot, i) => ({
      id: `${idPrefix}-${i}`, growthStage, slot, label: labels[i], orderIndex: i, active: true,
    })),
  };
}

const RESPONSE = {
  version: 1,
  stages: [
    stage('ระยะงอก', ['การเตรียมแปลง', 'สภาพอากาศ', 'การดูแลรักษา', 'ความต้านทานของสายพันธุ์'], 'g'),
    stage('เจริญเติบโต', ['สภาพอากาศ', 'การดูแลรักษา', 'ความเสี่ยง', 'สภาพแปลง'], 'v'),
    stage('ออกดอก', ['ความสมบูรณ์ของดอก', 'สภาพอากาศ', 'การดูแลรักษา', 'ความเสี่ยงโรคและแมลง'], 'f'),
    stage('ติดผล', ['การติดผล', 'ความสมบูรณ์ของผล', 'การดูแลรักษา', 'ความเสี่ยงโรคและแมลง'], 'r'),
    stage('เก็บเกี่ยว', ['ความพร้อมเก็บเกี่ยว', 'คุณภาพผลผลิต', 'ปริมาณผลผลิตคาดการณ์', 'สภาพแปลงก่อนเก็บเกี่ยว'], 'h'),
  ],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
  const utils = render(
    <QueryClientProvider client={qc}>
      <InspectionProtocols />
    </QueryClientProvider>,
  );
  return { ...utils, qc, invalidateSpy };
}

function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls
    .map((c) => (c[0] as { queryKey?: unknown[] })?.queryKey?.[0])
    .filter((k): k is string => typeof k === 'string');
}

beforeEach(() => {
  fetchAdminMock.mockReset();
  bulkUpdateMock.mockReset();
  canUpdate = true;
  fetchAdminMock.mockResolvedValue(RESPONSE);
});

describe('InspectionProtocols admin', () => {
  it('renders all 5 stages grouped with their current labels', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'ระยะงอก' })).toBeTruthy();
    for (const s of ['เจริญเติบโต', 'ออกดอก', 'ติดผล', 'เก็บเกี่ยว']) {
      expect(screen.getByRole('heading', { name: s })).toBeTruthy();
    }
    // เจริญเติบโต's distinctive labels are shown.
    expect(screen.getByText('ความเสี่ยง')).toBeTruthy();
    expect(screen.getByText('สภาพแปลง')).toBeTruthy();
  });

  async function editFirstLabel(newValue: string) {
    const heading = await screen.findByRole('heading', { name: 'ระยะงอก' });
    const card = heading.closest('section') as HTMLElement;
    fireEvent.click(within(card).getByRole('button', { name: /แก้ไข/ }));
    fireEvent.change(within(card).getByLabelText('ชื่อเกณฑ์ ช่อง 1'), { target: { value: newValue } });
    fireEvent.click(within(card).getByRole('button', { name: 'บันทึก' }));
    return card;
  }

  it('saves only the changed labels via one atomic bulk PATCH', async () => {
    bulkUpdateMock.mockResolvedValue([]);
    renderPage();

    await editFirstLabel('การเตรียมแปลงใหม่');

    await waitFor(() => expect(bulkUpdateMock).toHaveBeenCalledOnce());
    // One call, carrying only the changed criterion with its id + trimmed label.
    expect(bulkUpdateMock).toHaveBeenCalledWith([{ id: 'g-0', label: 'การเตรียมแปลงใหม่' }]);
  });

  it('invalidates all three protocol query keys after a successful save', async () => {
    bulkUpdateMock.mockResolvedValue([]);
    const { invalidateSpy } = renderPage();

    await editFirstLabel('ชื่อใหม่');
    await waitFor(() => expect(bulkUpdateMock).toHaveBeenCalledOnce());

    await waitFor(() => {
      const keys = invalidatedKeys(invalidateSpy);
      expect(keys).toContain('admin-inspection-protocols');
      expect(keys).toContain('inspection-protocols');
      expect(keys).toContain('public-inspection-protocols');
    });
  });

  it('on save failure shows a reliable error and refetches the admin config', async () => {
    bulkUpdateMock.mockRejectedValue(new Error('boom'));
    const { invalidateSpy } = renderPage();

    const card = await editFirstLabel('ชื่อใหม่');
    await waitFor(() => expect(bulkUpdateMock).toHaveBeenCalledOnce());

    expect(await within(card).findByText(/บันทึกไม่สำเร็จ/)).toBeTruthy();
    // Re-sync so the UI can't show a half-applied state.
    expect(invalidatedKeys(invalidateSpy)).toContain('admin-inspection-protocols');
  });

  it('blocks saving a blank label before calling the API', async () => {
    renderPage();

    const card = await editFirstLabel('  ');

    expect(await within(card).findByText('ชื่อเกณฑ์ต้องไม่ว่าง')).toBeTruthy();
    expect(bulkUpdateMock).not.toHaveBeenCalled();
  });

  it('hides the edit button without masterdata.update', async () => {
    canUpdate = false;
    renderPage();

    await screen.findByRole('heading', { name: 'ระยะงอก' });
    expect(screen.queryByRole('button', { name: /แก้ไข/ })).toBeNull();
  });
});
