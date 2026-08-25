import { describe, it, expect } from 'vitest';
import { canViewVariety } from './variety-visibility';

describe('canViewVariety', () => {
  it('shows variety for internal:* roles', () => {
    expect(canViewVariety([{ name: 'internal:admin' }])).toBe(true);
    expect(canViewVariety([{ name: 'internal:super_admin' }])).toBe(true);
  });

  it('shows variety for farmlog:supervisor specifically (not the whole farmlog: prefix)', () => {
    expect(canViewVariety([{ name: 'farmlog:supervisor' }])).toBe(true);
  });

  it('hides variety for supplier-affiliated roles', () => {
    expect(canViewVariety([{ name: 'supplier:owner' }])).toBe(false);
    expect(canViewVariety([{ name: 'farmlog:field_officer' }])).toBe(false);
  });

  it('hides variety for external roles', () => {
    expect(canViewVariety([{ name: 'external:user' }])).toBe(false);
  });

  it('hides variety when roles is empty, undefined, or null — default is hide, not show', () => {
    expect(canViewVariety([])).toBe(false);
    expect(canViewVariety(undefined)).toBe(false);
    expect(canViewVariety(null)).toBe(false);
  });

  it('shows variety if ANY held role is internal, even mixed with a non-internal one', () => {
    expect(canViewVariety([{ name: 'supplier:owner' }, { name: 'internal:admin' }])).toBe(true);
  });

  it('does not match an unrelated role that merely contains "internal:" as a substring, not a prefix', () => {
    expect(canViewVariety([{ name: 'not-internal:admin' }])).toBe(false);
  });
});
