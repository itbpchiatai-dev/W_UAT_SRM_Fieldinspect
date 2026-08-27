import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ThailandPlotMap } from './ThailandPlotMap';
import type { PlotSummary } from '../../api/plots';
import { VIEW_WIDTH } from './thailandGeo';

function plot(overrides: Partial<PlotSummary>): PlotSummary {
  return {
    id: 'p1', supplierId: 'sup-1', plotCode: 'SUP001-P001', name: 'แปลง',
    village: null, district: null, province: 'เชียงใหม่',
    latitude: '18.79', longitude: '98.98',
    isActive: true, assignedCount: 0, qrKey: null,
    currentYieldPct: null, expectedYieldFull: null, expectedYieldUnit: null, plantCount: null,
    currentCrop: 'พริก', currentVariety: null, currentLotNo: null, currentPlantingDate: null,
    ...overrides,
  } as PlotSummary;
}

function mapSvg(container: HTMLElement): SVGSVGElement {
  return container.querySelector('svg[aria-label]') as SVGSVGElement;
}

function viewBoxWidth(container: HTMLElement): number {
  const vb = mapSvg(container).getAttribute('viewBox')!.split(' ').map(Number);
  return vb[2];
}

describe('ThailandPlotMap zoom/pan', () => {
  it('starts at the full-country viewBox width', () => {
    const { container } = render(<ThailandPlotMap plots={[plot({})]} />);
    expect(viewBoxWidth(container)).toBeCloseTo(VIEW_WIDTH, 1);
  });

  it('zoom-in shrinks the viewBox; reset restores it', () => {
    const { container } = render(<ThailandPlotMap plots={[plot({})]} />);
    const full = viewBoxWidth(container);

    fireEvent.click(screen.getByLabelText('ซูมเข้า'));
    const zoomed = viewBoxWidth(container);
    expect(zoomed).toBeLessThan(full);

    fireEvent.click(screen.getByLabelText('รีเซ็ตมุมมอง'));
    expect(viewBoxWidth(container)).toBeCloseTo(full, 1);
  });

  it('does not zoom out beyond the full country (reset disabled at full view)', () => {
    const { container } = render(<ThailandPlotMap plots={[plot({})]} />);
    const full = viewBoxWidth(container);

    // Reset is disabled when already at full view.
    expect((screen.getByLabelText('รีเซ็ตมุมมอง') as HTMLButtonElement).disabled).toBe(true);

    // Zooming out from full view stays clamped at full width.
    fireEvent.click(screen.getByLabelText('ซูมออก'));
    expect(viewBoxWidth(container)).toBeCloseTo(full, 1);
  });

  it('zooms via the slider (scroll bar)', () => {
    const { container } = render(<ThailandPlotMap plots={[plot({})]} />);
    const full = viewBoxWidth(container);

    fireEvent.change(screen.getByLabelText('ระดับการซูม'), { target: { value: '4' } });
    // Zoom 4× → viewBox width ~= full / 4.
    expect(viewBoxWidth(container)).toBeCloseTo(full / 4, 0);
  });
});

describe('ThailandPlotMap detail panel', () => {
  it('opens a side panel with crop + yield detail when a marker is clicked', () => {
    const { container } = render(
      <ThailandPlotMap
        plots={[
          plot({
            plotCode: 'SUP001-P001',
            currentCrop: 'พริก',
            currentVariety: 'พริกขี้หนู',
            plantCount: 1200,
            expectedYieldFull: '1000',
            expectedYieldUnit: 'kg',
            currentYieldPct: '80',
          }),
        ]}
      />,
    );

    // No panel until a marker is clicked.
    expect(screen.queryByText('ผลผลิต (Yield)')).toBeNull();

    const marker = mapSvg(container).querySelector('[data-marker]') as SVGCircleElement;
    fireEvent.click(marker);

    expect(screen.getByText('ผลผลิต (Yield)')).toBeTruthy();
    expect(screen.getByText('จำนวนต้น/จำนวนปลูก')).toBeTruthy();
    expect(screen.getByText('1,200')).toBeTruthy();
    expect(screen.getByText('เป้าผลิต')).toBeTruthy();
    expect(screen.getByText('1,000 kg')).toBeTruthy();
    // 80% of 1000 kg = 800 kg (computed "ที่คาดว่าจะได้").
    expect(screen.getByText('800 kg')).toBeTruthy();
    expect(screen.getByText('พริกขี้หนู')).toBeTruthy();
  });

  it('shows a yield-gap hint when the plan is incomplete', () => {
    const { container } = render(
      <ThailandPlotMap
        plots={[plot({ plantCount: null, expectedYieldFull: null })]}
      />,
    );
    fireEvent.click(mapSvg(container).querySelector('[data-marker]') as SVGCircleElement);
    expect(screen.getByText('ยังไม่ตั้งแผนผลผลิต')).toBeTruthy();
  });

  it('closes the panel via the close button', () => {
    const { container } = render(<ThailandPlotMap plots={[plot({})]} />);
    fireEvent.click(mapSvg(container).querySelector('[data-marker]') as SVGCircleElement);
    expect(screen.getByText('ผลผลิต (Yield)')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('ปิดรายละเอียด'));
    expect(screen.queryByText('ผลผลิต (Yield)')).toBeNull();
  });
});

describe('ThailandPlotMap overview panel', () => {
  it('shows aggregate stats for the plots on the map by default', () => {
    render(
      <ThailandPlotMap
        plots={[
          plot({ id: 'p1', currentCrop: 'พริก', currentYieldPct: '80', expectedYieldFull: '1000', expectedYieldUnit: 'kg', province: 'เชียงใหม่' }),
          plot({ id: 'p2', currentCrop: 'พริก', currentYieldPct: '100', expectedYieldFull: '1000', expectedYieldUnit: 'kg', province: 'เชียงใหม่', latitude: '19.2', longitude: '100.7' }),
          plot({ id: 'p3', currentCrop: 'ข้าวโพด', currentYieldPct: '60', expectedYieldFull: '500', expectedYieldUnit: 'kg', province: 'น่าน', latitude: '18.5', longitude: '100.1' }),
        ]}
      />,
    );

    // Overview visible by default (no marker selected).
    expect(screen.getByText('ภาพรวมแปลงบนแผนที่')).toBeTruthy();
    expect(screen.getByText('Yield เฉลี่ย')).toBeTruthy();
    // Avg of 80/100/60 = 80%.
    expect(screen.getByText('80%')).toBeTruthy();
    // Total current expected = 800 + 1000 + 300 = 2100 kg.
    expect(screen.getByText('2,100 kg')).toBeTruthy();
    expect(screen.getByText('สัดส่วนชนิดพืช')).toBeTruthy();
  });

  it('switches from overview to plot detail when a marker is clicked, and back on close', () => {
    const { container } = render(
      <ThailandPlotMap plots={[plot({ plotCode: 'SUP001-P001' })]} />,
    );
    expect(screen.getByText('ภาพรวมแปลงบนแผนที่')).toBeTruthy();

    fireEvent.click(mapSvg(container).querySelector('[data-marker]') as SVGCircleElement);
    expect(screen.queryByText('ภาพรวมแปลงบนแผนที่')).toBeNull();
    expect(screen.getByText('ผลผลิต (Yield)')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('ปิดรายละเอียด'));
    expect(screen.getByText('ภาพรวมแปลงบนแผนที่')).toBeTruthy();
  });
});
