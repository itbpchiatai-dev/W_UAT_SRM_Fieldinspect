/**
 * YieldQuantityInput (round 8-8B) — the shared kg/percentage Yield input
 * used identically by RecordForm and PublicInspect (contract #11). Tests
 * the component in isolation: kg -> pct sync, pct -> kg sync, the
 * no-comparable-target disabled state, and inline error rendering.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { YieldQuantityInput } from './YieldQuantityInput';

describe('YieldQuantityInput — kg is primary, slider stays in sync', () => {
  it('typing a kg amount recomputes the percentage against the target', () => {
    const onChange = vi.fn();
    render(
      <YieldQuantityInput
        quantityKg={null} yieldPct={null}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '800' } });
    expect(onChange).toHaveBeenCalledWith({ quantityKg: 800, yieldPct: 80 });
  });

  it('dragging the slider recomputes kg, rounded to 2 decimals', () => {
    const onChange = vi.fn();
    render(
      <YieldQuantityInput
        quantityKg={0} yieldPct={0}
        expectedYieldFull={3} expectedYieldUnit="kg"
        onChange={onChange}
      />,
    );
    // 33.33% of 3 kg = 0.9999 -> rounds to 1.00
    fireEvent.change(screen.getByRole('slider'), { target: { value: '33.33' } });
    expect(onChange).toHaveBeenCalledWith({ quantityKg: 1, yieldPct: 33.33 });
  });

  it('clearing the kg input sets both quantityKg and yieldPct to null', () => {
    const onChange = vi.fn();
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '' } });
    expect(onChange).toHaveBeenCalledWith({ quantityKg: null, yieldPct: null });
  });

  it('shows the percentage with 1 decimal place', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('80.0%')).toBeTruthy();
  });

  it('shows an em dash when yieldPct is null', () => {
    render(
      <YieldQuantityInput
        quantityKg={null} yieldPct={null}
        expectedYieldFull={null} expectedYieldUnit={null}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('shows the target at 100%', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/เทียบกับเป้าหมาย 1,000 kg \(ที่ 100%\)/)).toBeTruthy();
  });
});

describe('YieldQuantityInput — no comparable kg target (contract #7)', () => {
  it('kg input STAYS enabled even with no target', () => {
    render(
      <YieldQuantityInput
        quantityKg={10} yieldPct={null}
        expectedYieldFull={null} expectedYieldUnit={null}
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('spinbutton') as HTMLInputElement).disabled).toBe(false);
  });

  it('the percentage slider is disabled', () => {
    render(
      <YieldQuantityInput
        quantityKg={10} yieldPct={null}
        expectedYieldFull={null} expectedYieldUnit={null}
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  });

  it('shows the "no kg target" note instead of the target line', () => {
    render(
      <YieldQuantityInput
        quantityKg={10} yieldPct={null}
        expectedYieldFull={1000} expectedYieldUnit="ผล"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('รอบปลูกนี้ไม่มีเป้าหมายหน่วย kg สำหรับคำนวณเปอร์เซ็นต์')).toBeTruthy();
  });

  it('percentage display stays "—", never a faked 100%', () => {
    render(
      <YieldQuantityInput
        quantityKg={10} yieldPct={null}
        expectedYieldFull={1000} expectedYieldUnit="ลัง"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('—')).toBeTruthy();
    expect(screen.queryByText('100.0%')).toBeNull();
  });
});

describe('YieldQuantityInput — inline error (never alert())', () => {
  it('renders the error prop as inline text with role="alert"', () => {
    render(
      <YieldQuantityInput
        quantityKg={-1} yieldPct={null}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
        error="ปริมาณผลผลิตต้องไม่ติดลบ"
      />,
    );
    expect(screen.getByRole('alert').textContent).toBe('ปริมาณผลผลิตต้องไม่ติดลบ');
  });

  it('renders no alert when error is absent', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('YieldQuantityInput — latest-inspection hint', () => {
  it('shows a compact "ล่าสุด" hint next to the target when latestYieldPct is given', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        latestYieldPct={62}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/· ล่าสุด 62%/)).toBeTruthy();
  });

  it('omits the hint when latestYieldPct is not given', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/ล่าสุด/)).toBeNull();
  });
});

describe('YieldQuantityInput — legacy/null data never crashes', () => {
  it('renders with every prop null/undefined (a brand-new form, no plot yet)', () => {
    expect(() => render(
      <YieldQuantityInput
        quantityKg={null} yieldPct={null}
        expectedYieldFull={undefined} expectedYieldUnit={undefined}
        onChange={vi.fn()}
      />,
    )).not.toThrow();
  });

  it('accepts a Decimal-serialized-as-string expectedYieldFull, same as a number', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull="1000.00" expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/เทียบกับเป้าหมาย 1,000 kg/)).toBeTruthy();
  });
});

describe('YieldQuantityInput — disabled prop disables both controls', () => {
  it('disabled=true disables the kg input even with a comparable target', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        disabled
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('spinbutton') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  });
});

// --- round 8-8B.1: >150% is a non-blocking amber warning, not a form error -

describe('YieldQuantityInput — non-blocking warning past 150% (round 8-8B.1)', () => {
  it('shows the amber warning when yieldPct > 150', () => {
    render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText('ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก')).toBeTruthy();
  });

  it('the warning uses role="status", never role="alert" (that role is reserved for the blocking error prop)', () => {
    render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('status').textContent).toBe(
      'ผลผลิตสูงกว่า 150% ของเป้าหมาย กรุณาตรวจสอบความถูกต้องก่อนบันทึก',
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('does not use a red error class for the warning (amber, not red)', () => {
    render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    const warning = screen.getByRole('status');
    expect(warning.className).toMatch(/amber/);
    expect(warning.className).not.toMatch(/text-red/);
  });

  it('no warning at exactly 150%', () => {
    render(
      <YieldQuantityInput
        quantityKg={1500} yieldPct={150}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('no warning when yieldPct is null (no comparable target)', () => {
    render(
      <YieldQuantityInput
        quantityKg={10} yieldPct={null}
        expectedYieldFull={null} expectedYieldUnit={null}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('the warning disappears once the value is brought back to <= 150 (rerender)', () => {
    const { rerender } = render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('status')).toBeTruthy();

    rerender(
      <YieldQuantityInput
        quantityKg={1400} yieldPct={140}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('kg input above 150% of target still updates yieldPct correctly via onChange', () => {
    const onChange = vi.fn();
    render(
      <YieldQuantityInput
        quantityKg={null} yieldPct={null}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1600' } });
    expect(onChange).toHaveBeenCalledWith({ quantityKg: 1600, yieldPct: 160 });
  });

  it('a blocking error (e.g. negative kg) still renders as role="alert" even when a warning would otherwise show', () => {
    render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
        error="ปริมาณผลผลิตต้องไม่ติดลบ"
      />,
    );
    expect(screen.getByRole('alert').textContent).toBe('ปริมาณผลผลิตต้องไม่ติดลบ');
    expect(screen.getByRole('status')).toBeTruthy();
  });
});

describe('YieldQuantityInput — dynamic slider max (round 8-8B.1)', () => {
  it('slider max stays 150 at/under the warning threshold', () => {
    render(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('150');
  });

  it('160% expands slider max to 200 (next 50-point tier)', () => {
    render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('200');
  });

  it('225% expands slider max to 250', () => {
    render(
      <YieldQuantityInput
        quantityKg={2250} yieldPct={225}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('250');
  });

  it('510% expands slider max to 550', () => {
    render(
      <YieldQuantityInput
        quantityKg={5100} yieldPct={510}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('550');
  });

  it('never exceeds the 9999.9 storage ceiling even for a huge value', () => {
    render(
      <YieldQuantityInput
        quantityKg={99999} yieldPct={9999.9}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect(Number((screen.getByRole('slider') as HTMLInputElement).max)).toBeLessThanOrEqual(9999.9);
  });

  it('the slider VALUE itself is never clamped to 150 — shows the real value', () => {
    render(
      <YieldQuantityInput
        quantityKg={5100} yieldPct={510}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).value).toBe('510');
    expect(screen.getByText('510.0%')).toBeTruthy();
  });

  it('slider max returns to 150 once the value drops back to <=150, without altering the value', () => {
    const { rerender } = render(
      <YieldQuantityInput
        quantityKg={1600} yieldPct={160}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('200');

    rerender(
      <YieldQuantityInput
        quantityKg={800} yieldPct={80}
        expectedYieldFull={1000} expectedYieldUnit="kg"
        onChange={vi.fn()}
      />,
    );
    expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('150');
    expect((screen.getByRole('slider') as HTMLInputElement).value).toBe('80');
  });
});
