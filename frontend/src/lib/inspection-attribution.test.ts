import { describe, it, expect } from 'vitest';
import {
  hasPhoneAttribution, phoneTypeLabel, inspectorTypeLabel, formattedPhoneSnapshot,
  INSPECTOR_TYPE_OPTIONS,
} from './inspection-attribution';

describe('hasPhoneAttribution', () => {
  it('is true when submittedPhoneSnapshot is set', () => {
    expect(hasPhoneAttribution({
      submittedPhoneSnapshot: '0845552162', submittedPhoneType: 'primary', inspectorType: null,
    })).toBe(true);
  });

  it('is true when inspectorType is set even without a phone snapshot', () => {
    expect(hasPhoneAttribution({
      submittedPhoneSnapshot: null, submittedPhoneType: null, inspectorType: 'farmer',
    })).toBe(true);
  });

  it('is false when both are null (logged-in flow / legacy record)', () => {
    expect(hasPhoneAttribution({
      submittedPhoneSnapshot: null, submittedPhoneType: null, inspectorType: null,
    })).toBe(false);
  });
});

describe('phoneTypeLabel', () => {
  it.each([
    ['primary', 'เบอร์หลัก'],
    ['additional', 'เบอร์เสริม'],
  ] as const)('%s -> %s', (type, label) => {
    expect(phoneTypeLabel(type)).toBe(label);
  });

  it('returns null for null', () => {
    expect(phoneTypeLabel(null)).toBeNull();
  });
});

// Round 8-11A — the canonical contract is farmer/supplier/chiatai, and this
// mapping is the ONE place the visible Thai labels are defined (item 22).
describe('inspectorTypeLabel', () => {
  it.each([
    ['farmer', 'เกษตรกร'],
    ['supplier', 'บริษัทผู้ผลิต'],
    ['chiatai', 'Chiatai'],
  ] as const)('%s -> %s', (type, label) => {
    expect(inspectorTypeLabel(type)).toBe(label);
  });

  it('returns null for null', () => {
    expect(inspectorTypeLabel(null)).toBeNull();
  });

  // Item 16/17 — the retired wording must be gone from the shared mapping,
  // and the inspector option must not read "Supplier" (the Supplier ENTITY
  // on Admin/Plots keeps that word; this is the inspector's role).
  it('no longer maps anything to the retired "ส่งเสริม" or bare "Supplier" label', () => {
    const labels = INSPECTOR_TYPE_OPTIONS.map((o) => o.label);
    expect(labels).not.toContain('ส่งเสริม');
    expect(labels).not.toContain('Supplier');
    expect(labels).toEqual(['เกษตรกร', 'บริษัทผู้ผลิต', 'Chiatai']);
  });

  it('exposes exactly the three canonical values, in form order (item 15/22)', () => {
    expect(INSPECTOR_TYPE_OPTIONS.map((o) => o.value)).toEqual(['farmer', 'supplier', 'chiatai']);
  });
});

describe('formattedPhoneSnapshot', () => {
  it('formats a canonical phone for display', () => {
    expect(formattedPhoneSnapshot('0845552162')).toBe('084-555-2162');
  });

  it('returns null for null', () => {
    expect(formattedPhoneSnapshot(null)).toBeNull();
  });
});
