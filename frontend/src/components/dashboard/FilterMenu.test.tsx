/**
 * FilterMenu (round 52) — collapsing three controls behind one button is
 * only safe if the button says the page is filtered. A reader taking a
 * filtered number for the company total is the mistake this exists to
 * prevent, so the count is the most important thing here.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  FilterMenu, activeFilterCount, CYCLE_STATUS_OPTIONS, type FilterMenuValue,
} from './FilterMenu';

const EMPTY: FilterMenuValue = { supplierId: null, pCode: null, cycleStatus: null };
const SUPPLIERS = [{ value: 'sup-5', label: 'SUP005 — Supplier Five' }];
const PCODES = [{ value: 'WM-141', label: 'WM-141 · แตงโมพันธุ์ก' }];

function renderMenu(value: FilterMenuValue = EMPTY, onChange = vi.fn()) {
  render(
    <FilterMenu
      value={value} onChange={onChange}
      supplierOptions={SUPPLIERS} pCodeOptions={PCODES}
    />,
  );
  return onChange;
}

describe('activeFilterCount', () => {
  it('is zero when nothing is set', () => {
    expect(activeFilterCount(EMPTY)).toBe(0);
  });

  it('counts each filter that is set', () => {
    expect(activeFilterCount({ ...EMPTY, supplierId: 'sup-5' })).toBe(1);
    expect(activeFilterCount({ supplierId: 'a', pCode: 'b', cycleStatus: 'active' })).toBe(3);
  });

  it('does not count an empty string as a filter', () => {
    expect(activeFilterCount({ supplierId: '', pCode: '', cycleStatus: '' })).toBe(0);
  });
});

describe('FilterMenu — the button', () => {
  it('says only "ตัวกรอง" when nothing is filtered', () => {
    renderMenu();
    const btn = screen.getByRole('button', { name: 'ตัวกรอง' });
    expect(btn.textContent).not.toMatch(/\d/);
  });

  it('shows how many filters are on, so a filtered page cannot look whole', () => {
    renderMenu({ supplierId: 'sup-5', pCode: 'WM-141', cycleStatus: null });
    expect(screen.getByRole('button', { name: 'ตัวกรอง 2 รายการ' })).toBeTruthy();
  });

  it('keeps the panel shut until it is asked for', () => {
    renderMenu();
    expect(screen.queryByRole('group', { name: 'ตัวกรองเพิ่มเติม' })).toBeNull();
  });

  it('opens and closes on click', () => {
    renderMenu();
    const btn = screen.getByRole('button', { name: 'ตัวกรอง' });
    fireEvent.click(btn);
    expect(screen.getByRole('group', { name: 'ตัวกรองเพิ่มเติม' })).toBeTruthy();
    fireEvent.click(btn);
    expect(screen.queryByRole('group', { name: 'ตัวกรองเพิ่มเติม' })).toBeNull();
  });

  it('closes on Escape', () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('group', { name: 'ตัวกรองเพิ่มเติม' })).toBeNull();
  });

  it('closes when the page behind it is clicked', () => {
    // Not a modal on purpose: a panel that blocks the page stops you
    // seeing what it changes.
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('group', { name: 'ตัวกรองเพิ่มเติม' })).toBeNull();
  });
});

describe('FilterMenu — the three filters', () => {
  it('offers all three, each searchable', () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    expect(screen.getByRole('button', { name: /กรอง Supplier/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /กรอง P\.Code/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /กรองสถานะรอบปลูก/ })).toBeTruthy();
  });

  it('offers the cycle statuses the backend stores', () => {
    // backend: plot_cycle.CYCLE_STATUSES
    expect(CYCLE_STATUS_OPTIONS.map((o) => o.value))
      .toEqual(['active', 'harvested', 'cancelled']);
  });

  it('reports a picked supplier without disturbing the others', () => {
    const onChange = renderMenu({ ...EMPTY, pCode: 'WM-141' });
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง 1 รายการ' }));
    fireEvent.click(screen.getByRole('button', { name: /กรอง Supplier/ }));
    fireEvent.click(screen.getByRole('option', { name: /SUP005/ }));
    expect(onChange).toHaveBeenCalledWith(
      { supplierId: 'sup-5', pCode: 'WM-141', cycleStatus: null },
    );
  });

  it('reports a picked cycle status', () => {
    const onChange = renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    fireEvent.click(screen.getByRole('button', { name: /กรองสถานะรอบปลูก/ }));
    fireEvent.click(screen.getByRole('option', { name: 'เก็บเกี่ยวแล้ว' }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ cycleStatus: 'harvested' }));
  });
});

describe('FilterMenu — clearing', () => {
  it('clears all three at once', () => {
    const onChange = renderMenu({ supplierId: 'a', pCode: 'b', cycleStatus: 'active' });
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง 3 รายการ' }));
    fireEvent.click(screen.getByRole('button', { name: /ล้างตัวกรองทั้งหมด/ }));
    expect(onChange).toHaveBeenCalledWith(EMPTY);
  });

  it('disables the clear button when there is nothing to clear', () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: 'ตัวกรอง' }));
    const clear = screen.getByRole('button', { name: /ล้างตัวกรองทั้งหมด/ }) as HTMLButtonElement;
    expect(clear.disabled).toBe(true);
  });
});
