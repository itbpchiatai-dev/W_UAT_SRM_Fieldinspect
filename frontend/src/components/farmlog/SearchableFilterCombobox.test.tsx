import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { SearchableFilterCombobox } from './SearchableFilterCombobox';

const OPTIONS = ['jun2026', 'aug2026', 'dec2026'];

function renderCombobox(overrides: Partial<React.ComponentProps<typeof SearchableFilterCombobox>> = {}) {
  const onChange = vi.fn();
  render(
    <SearchableFilterCombobox
      label="กรองรอบปลูกปัจจุบัน"
      allLabel="ทุกรอบปลูก"
      options={OPTIONS}
      value=""
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange };
}

describe('SearchableFilterCombobox', () => {
  it('shows allLabel on the trigger when value is empty', () => {
    renderCombobox();
    expect(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }).textContent).toContain('ทุกรอบปลูก');
  });

  it('shows the selected value on the trigger when set', () => {
    renderCombobox({ value: 'jun2026' });
    expect(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }).textContent).toContain('jun2026');
  });

  it('is closed by default (no listbox rendered)', () => {
    renderCombobox();
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('opens the listbox with every option on click', () => {
    renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));

    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('ทุกรอบปลูก')).toBeTruthy();
    for (const opt of OPTIONS) {
      expect(within(listbox).getByText(opt)).toBeTruthy();
    }
  });

  it('typing in the search box narrows the visible options', () => {
    renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'jun' } });

    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getByText('jun2026')).toBeTruthy();
    expect(within(listbox).queryByText('aug2026')).toBeNull();
    expect(within(listbox).queryByText('dec2026')).toBeNull();
  });

  it('search is case-insensitive', () => {
    renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'JUN' } });

    expect(within(screen.getByRole('listbox')).getByText('jun2026')).toBeTruthy();
  });

  it('shows the empty message when search matches nothing', () => {
    renderCombobox({ emptyMessage: 'ไม่พบรอบปลูก' });
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'no-such-label' } });

    expect(screen.getByText('ไม่พบรอบปลูก')).toBeTruthy();
  });

  it('selecting an option calls onChange with it and closes the dropdown', () => {
    const { onChange } = renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.click(within(screen.getByRole('listbox')).getByText('aug2026'));

    expect(onChange).toHaveBeenCalledWith('aug2026');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('selecting the "allLabel" option calls onChange with an empty string (clears the filter)', () => {
    const { onChange } = renderCombobox({ value: 'jun2026' });
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.click(within(screen.getByRole('listbox')).getByText('ทุกรอบปลูก'));

    expect(onChange).toHaveBeenCalledWith('');
  });

  it('selecting an option resets the search text (reopening shows every option again)', () => {
    renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.change(screen.getByPlaceholderText('ค้นหา...'), { target: { value: 'jun' } });
    fireEvent.click(within(screen.getByRole('listbox')).getByText('jun2026'));

    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    const listbox = screen.getByRole('listbox');
    for (const opt of OPTIONS) {
      expect(within(listbox).getByText(opt)).toBeTruthy();
    }
  });

  it('pressing Escape closes the dropdown without changing the value', () => {
    const { onChange } = renderCombobox();
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('listbox')).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('clicking outside the combobox closes it', () => {
    render(
      <div>
        <SearchableFilterCombobox
          label="กรองรอบปลูกปัจจุบัน"
          allLabel="ทุกรอบปลูก"
          options={OPTIONS}
          value=""
          onChange={vi.fn()}
        />
        <button type="button">outside</button>
      </div>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }));
    expect(screen.getByRole('listbox')).toBeTruthy();

    fireEvent.mouseDown(screen.getByRole('button', { name: 'outside' }));
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('disabled prevents opening the dropdown', () => {
    renderCombobox({ disabled: true });
    const trigger = screen.getByRole('button', { name: 'กรองรอบปลูกปัจจุบัน' }) as HTMLButtonElement;
    expect(trigger.disabled).toBe(true);
    fireEvent.click(trigger);
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
