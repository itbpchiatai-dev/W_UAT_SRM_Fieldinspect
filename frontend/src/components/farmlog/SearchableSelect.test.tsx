/**
 * SearchableSelect (round Z) — the one searchable dropdown behind every
 * chooser in the app: the filter bar's comboboxes and, new this round, the
 * FORM fields (Supplier, จังหวัด, ชนิดพืช, P.Code) that were plain <select>
 * boxes with no way to search 77 provinces or 500 P.Codes.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SearchableSelect } from './SearchableSelect';

const OPTIONS = [
  { value: 'cm', label: 'เชียงใหม่' },
  { value: 'cr', label: 'เชียงราย' },
  { value: 'bkk', label: 'กรุงเทพมหานคร' },
];

function renderSelect(overrides: Partial<React.ComponentProps<typeof SearchableSelect>> = {}) {
  const onChange = vi.fn();
  render(
    <SearchableSelect
      label="— เลือกจังหวัด —"
      options={OPTIONS}
      value={null}
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange };
}

function open(label = '— เลือกจังหวัด —') {
  fireEvent.click(screen.getByRole('button', { name: label }));
}

describe('SearchableSelect', () => {
  it('shows the placeholder until something is chosen', () => {
    renderSelect();
    expect(screen.getByRole('button', { name: '— เลือกจังหวัด —' }).textContent)
      .toContain('— เลือกจังหวัด —');
  });

  it('shows the chosen option label, not its value', () => {
    renderSelect({ value: 'cm' });
    expect(screen.getByRole('button', { name: '— เลือกจังหวัด —' }).textContent).toContain('เชียงใหม่');
  });

  it('is closed until asked', () => {
    renderSelect();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('opens with every option listed', () => {
    renderSelect();
    open();
    const listbox = screen.getByRole('listbox');
    for (const o of OPTIONS) expect(within(listbox).getByText(o.label)).toBeTruthy();
  });

  it('searches, which is the whole point of this control', () => {
    renderSelect();
    open();
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'เชียง' } });

    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('เชียงใหม่')).toBeTruthy();
    expect(within(listbox).getByText('เชียงราย')).toBeTruthy();
    expect(within(listbox).queryByText('กรุงเทพมหานคร')).toBeNull();
  });

  it('ignores case and surrounding spaces while searching', () => {
    renderSelect({
      options: [{ value: 'a', label: 'CTT-507 — RWA 412' }, { value: 'b', label: 'WM-141 — พริก' }],
    });
    open();
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: '  ctt ' } });

    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('CTT-507 — RWA 412')).toBeTruthy();
    expect(within(listbox).queryByText('WM-141 — พริก')).toBeNull();
  });

  it('reports the VALUE of what was clicked, and closes', () => {
    const { onChange } = renderSelect();
    open();
    fireEvent.click(screen.getByRole('option', { name: 'เชียงราย' }));

    expect(onChange).toHaveBeenCalledWith('cr');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('forgets the search text between openings', () => {
    renderSelect();
    open();
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'เชียง' } });
    fireEvent.click(screen.getByRole('option', { name: 'เชียงใหม่' }));
    open();

    expect((screen.getByPlaceholderText('ค้นหา...') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('option', { name: 'กรุงเทพมหานคร' })).toBeTruthy();
  });

  it('says when a search matches nothing, instead of showing an empty box', () => {
    renderSelect();
    open();
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'ภูเก็ต' } });

    expect(screen.getByText('ไม่พบรายการ')).toBeTruthy();
  });

  it('closes on Escape and on a click outside', () => {
    renderSelect();
    open();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();

    open();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('clears the field when the caller offers a clear option', () => {
    const { onChange } = renderSelect({ clearLabel: 'ทุกจังหวัด' });
    open();
    fireEvent.click(screen.getByRole('option', { name: 'ทุกจังหวัด' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('a required field has nothing to clear to — only the real options', () => {
    // Its own render: a leftover clear option from another render in the same
    // container would make this pass while the field really had one.
    renderSelect();
    open();
    const options = within(screen.getByRole('listbox')).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['เชียงใหม่', 'เชียงราย', 'กรุงเทพมหานคร']);
  });

  it('does not open when disabled', () => {
    renderSelect({ disabled: true });
    // jsdom swallows a click on a disabled button either way, so assert the
    // BUTTON is disabled — otherwise this test passes even when the guard is
    // gone.
    const trigger = screen.getByRole('button', { name: '— เลือกจังหวัด —' });
    expect((trigger as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(trigger);
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('shows a stored value that is no longer on offer, rather than going blank', () => {
    // A deactivated master-data value must not look like data loss — the rule
    // the plain <select> followed before this round.
    renderSelect({ value: 'gone', staleLabel: 'จังหวัดเก่า (ปิดใช้งาน/ค่าเดิม)' });
    expect(screen.getByRole('button', { name: '— เลือกจังหวัด —' }).textContent)
      .toContain('จังหวัดเก่า (ปิดใช้งาน/ค่าเดิม)');
  });

  it('tells the caller when there is nothing to choose from', () => {
    renderSelect({ options: [], emptyMessage: 'ชนิดพืชนี้ยังไม่มี P.Code' });
    open();
    expect(screen.getByText('ชนิดพืชนี้ยังไม่มี P.Code')).toBeTruthy();
  });
});
