import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useState } from 'react';
import {
  PlotAccessPhoneFields,
  buildPlotAccessPhoneConfig,
  emptyPlotAccessPhoneFieldsValue,
  accessPhoneConfigToFieldsValue,
  MAX_ADDITIONAL_PHONES,
  type PlotAccessPhoneFieldsValue,
} from './PlotAccessPhoneFields';

// --- buildPlotAccessPhoneConfig (pure function) -----------------------------

describe('buildPlotAccessPhoneConfig', () => {
  it('empty value is valid with a null config (no phones set)', () => {
    const { config, hasErrors } = buildPlotAccessPhoneConfig(emptyPlotAccessPhoneFieldsValue());
    expect(hasErrors).toBe(false);
    expect(config).toEqual({ primaryPhone: null, additionalPhones: [] });
  });

  it('primary only is valid and canonicalized', () => {
    const { config, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '084-555-2162', additionalPhones: [],
    });
    expect(hasErrors).toBe(false);
    expect(config).toEqual({ primaryPhone: '0845552162', additionalPhones: [] });
  });

  it('primary + additional valid and canonicalized', () => {
    const { config, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '0845552162', additionalPhones: ['081-234-5678', '089 111 2222'],
    });
    expect(hasErrors).toBe(false);
    expect(config).toEqual({
      primaryPhone: '0845552162', additionalPhones: ['0812345678', '0891112222'],
    });
  });

  it('blank additional rows are dropped, not sent, not an error', () => {
    const { config, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '0845552162', additionalPhones: ['', '  ', '0812345678'],
    });
    expect(hasErrors).toBe(false);
    expect(config?.additionalPhones).toEqual(['0812345678']);
  });

  it('additional without primary is rejected', () => {
    const { config, errors, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '', additionalPhones: ['0812345678'],
    });
    expect(hasErrors).toBe(true);
    expect(config).toBeNull();
    expect(errors.primary).toBeTruthy();
  });

  it('duplicate additional numbers (different formatting) rejected, not silently deduped', () => {
    const { config, errors, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '0899999999', additionalPhones: ['0812345678', '081-234-5678'],
    });
    expect(hasErrors).toBe(true);
    expect(config).toBeNull(); // NOT collapsed to one entry
    expect(errors.additional[0]).toBeTruthy();
    expect(errors.additional[1]).toBeTruthy();
  });

  it('primary duplicated in additional is rejected', () => {
    const { errors, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '0812345678', additionalPhones: ['081-234-5678'],
    });
    expect(hasErrors).toBe(true);
    expect(errors.primary).toBeTruthy();
    expect(errors.additional[0]).toBeTruthy();
  });

  it('invalid phone format surfaces a per-field error', () => {
    const { errors, hasErrors } = buildPlotAccessPhoneConfig({
      primaryPhone: '0712345678', additionalPhones: [],
    });
    expect(hasErrors).toBe(true);
    expect(errors.primary).toBeTruthy();
  });

  it('exactly 10 additional numbers is valid', () => {
    const ten = Array.from({ length: 10 }, (_, i) => `08100000${String(i).padStart(2, '0')}`);
    const { hasErrors } = buildPlotAccessPhoneConfig({ primaryPhone: '0845552162', additionalPhones: ten });
    expect(hasErrors).toBe(false);
  });
});

describe('accessPhoneConfigToFieldsValue', () => {
  it('converts a config to editable raw values', () => {
    const value = accessPhoneConfigToFieldsValue({
      primaryPhone: '0845552162', additionalPhones: ['0812345678'],
    });
    expect(value).toEqual({ primaryPhone: '0845552162', additionalPhones: ['0812345678'] });
  });

  it('null/undefined config becomes the empty value', () => {
    expect(accessPhoneConfigToFieldsValue(null)).toEqual(emptyPlotAccessPhoneFieldsValue());
    expect(accessPhoneConfigToFieldsValue(undefined)).toEqual(emptyPlotAccessPhoneFieldsValue());
  });
});

// --- <PlotAccessPhoneFields /> component ------------------------------------

function Harness({ initial }: { initial?: PlotAccessPhoneFieldsValue }) {
  const [value, setValue] = useState<PlotAccessPhoneFieldsValue>(initial ?? emptyPlotAccessPhoneFieldsValue());
  return <PlotAccessPhoneFields value={value} onChange={setValue} />;
}

describe('<PlotAccessPhoneFields />', () => {
  it('renders the primary input as a tel field', () => {
    render(<Harness />);
    const primary = screen.getByLabelText('เบอร์หลัก') as HTMLInputElement;
    expect(primary.type).toBe('tel');
    expect(primary.inputMode).toBe('tel');
    expect(primary.autocomplete).toBe('tel');
  });

  it('typing into primary updates the value', () => {
    render(<Harness />);
    const primary = screen.getByLabelText('เบอร์หลัก') as HTMLInputElement;
    fireEvent.change(primary, { target: { value: '0845552162' } });
    expect(primary.value).toBe('0845552162');
  });

  it('adding a row renders a new additional input', () => {
    render(<Harness />);
    expect(screen.queryByLabelText('เบอร์เสริมที่ 1')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'เพิ่มเบอร์เสริม' }));
    expect(screen.getByLabelText('เบอร์เสริมที่ 1')).toBeTruthy();
  });

  it('removing a row deletes it', () => {
    render(<Harness initial={{ primaryPhone: '0845552162', additionalPhones: ['0812345678', '0891112222'] }} />);
    expect(screen.getByLabelText('เบอร์เสริมที่ 2')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'ลบเบอร์เสริมที่ 1' }));
    expect(screen.queryByLabelText('เบอร์เสริมที่ 2')).toBeNull();
    expect((screen.getByLabelText('เบอร์เสริมที่ 1') as HTMLInputElement).value).toBe('0891112222');
  });

  it('the add button is disabled at the max of 10', () => {
    const ten = Array.from({ length: MAX_ADDITIONAL_PHONES }, (_, i) => `08100000${String(i).padStart(2, '0')}`);
    render(<Harness initial={{ primaryPhone: '0845552162', additionalPhones: ten }} />);
    const addBtn = screen.getByRole('button', { name: 'เพิ่มเบอร์เสริม' }) as HTMLButtonElement;
    expect(addBtn.disabled).toBe(true);
  });

  it('shows an inline error for a duplicate additional number', () => {
    render(<Harness initial={{ primaryPhone: '0899999999', additionalPhones: ['0812345678', '0812345678'] }} />);
    expect(screen.getAllByText('เบอร์นี้ซ้ำกับเบอร์เสริมแถวอื่น').length).toBeGreaterThan(0);
  });

  it('shows an inline error when primary is duplicated in additional', () => {
    render(<Harness initial={{ primaryPhone: '0812345678', additionalPhones: ['0812345678'] }} />);
    expect(screen.getByText('เบอร์นี้ซ้ำกับเบอร์เสริม')).toBeTruthy();
  });

  it('shows an inline error for additional without primary', () => {
    render(<Harness initial={{ primaryPhone: '', additionalPhones: ['0812345678'] }} />);
    expect(screen.getByText('กรุณากรอกเบอร์หลักก่อนเพิ่มเบอร์เสริม')).toBeTruthy();
  });

  it('additional rows do not carry autocomplete="tel" (primary only, per spec)', () => {
    render(<Harness initial={{ primaryPhone: '', additionalPhones: [''] }} />);
    const row = screen.getByLabelText('เบอร์เสริมที่ 1') as HTMLInputElement;
    expect(row.autocomplete).not.toBe('tel');
  });
});
