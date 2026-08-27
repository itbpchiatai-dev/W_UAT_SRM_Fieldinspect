/**
 * ThailandPlotMap — offline SVG map of Thailand with one marker per plot,
 * colored by crop. No map tiles / no runtime network (works under the strict
 * nginx CSP); the province outlines and the lng/lat→viewBox projection are a
 * bundled static asset (thailandGeo.ts). Decimal lat/lng come off the API as
 * strings, so every coordinate goes through toNumberOrNull first.
 *
 * Layout: the map sits on the LEFT of its row and an always-on info panel
 * fills the RIGHT — the panel shows an at-a-glance summary of whatever plots
 * are currently on the map (crop mix, average yield, total expected yield,
 * top provinces) and swaps to a single plot's crop + yield detail when a
 * marker is clicked.
 *
 * Zoom/pan is done purely by animating the SVG viewBox (no tiles, no lib):
 * a zoom slider + scroll wheel (toward the cursor) + drag to pan + reset.
 * The wheel handler is attached natively with { passive: false } so it can
 * preventDefault — otherwise the whole page would scroll while zooming.
 * Marker radii/strokes divide by the zoom factor so they keep a constant
 * on-screen size instead of ballooning as you zoom in.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Minus, Plus, RotateCcw, X } from 'lucide-react';
import type { PlotSummary } from '../../api/plots';
import { toNumberOrNull } from '../../lib/numeric';
import {
  computeCurrentExpectedYield,
  describeYieldPlanGap,
  formatYieldQuantity,
} from '../../lib/yield-planning';
import { cropColor, UNSPECIFIED_CROP_COLOR } from './cropColor';
import {
  PROVINCE_SHAPES,
  VIEW_HEIGHT,
  VIEW_WIDTH,
  isWithinThailand,
  projectLngLat,
} from './thailandGeo';

interface PlacedPlot {
  plot: PlotSummary;
  x: number;
  y: number;
  color: string;
}

interface HoverState {
  placed: PlacedPlot;
  /** pointer position within the SVG's client box, for tooltip placement */
  clientX: number;
  clientY: number;
}

interface ViewBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

const FULL_VIEW: ViewBox = { x: 0, y: 0, w: VIEW_WIDTH, h: VIEW_HEIGHT };
const MIN_ZOOM = 1; // never zoom out past the whole country
const MAX_ZOOM = 12;
const ZOOM_STEP = 1.4; // per button click / wheel notch

export interface ThailandPlotMapProps {
  plots: PlotSummary[];
  /** Optional: total plots before filtering, to show "N / M" coverage. */
  totalCount?: number;
}

export function ThailandPlotMap({ plots, totalCount }: ThailandPlotMapProps) {
  const [hover, setHover] = useState<HoverState | null>(null);
  const [selected, setSelected] = useState<PlacedPlot | null>(null);
  const [view, setView] = useState<ViewBox>(FULL_VIEW);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<{
    startClientX: number;
    startClientY: number;
    startView: ViewBox;
    moved: boolean;
  } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // Current zoom factor relative to the full-country view. Markers/strokes
  // divide by this so they stay a constant on-screen size.
  const zoom = VIEW_WIDTH / view.w;

  // Project only plots with a valid coordinate inside Thailand's bbox — a
  // missing/out-of-range point is counted separately rather than drawn
  // off-canvas.
  const { placed, skipped } = useMemo(() => {
    const out: PlacedPlot[] = [];
    let skip = 0;
    for (const plot of plots) {
      const lng = toNumberOrNull(plot.longitude);
      const lat = toNumberOrNull(plot.latitude);
      if (lng == null || lat == null || !isWithinThailand(lng, lat)) {
        skip++;
        continue;
      }
      const { x, y } = projectLngLat(lng, lat);
      out.push({ plot, x, y, color: cropColor(plot.currentCrop) });
    }
    return { placed: out, skipped: skip };
  }, [plots]);

  // A filtered-away selection shouldn't linger in the panel.
  useEffect(() => {
    if (selected && !plots.some((p) => p.id === selected.plot.id)) setSelected(null);
  }, [plots, selected]);

  /** Client (px) → SVG user-space coordinate under the current viewBox. */
  const clientToSvg = useCallback((clientX: number, clientY: number, v: ViewBox) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: v.x + v.w / 2, y: v.y + v.h / 2 };
    const fx = (clientX - rect.left) / rect.width;
    const fy = (clientY - rect.top) / rect.height;
    return { x: v.x + fx * v.w, y: v.y + fy * v.h };
  }, []);

  /** Set an absolute zoom level, keeping the anchor point put (defaults to the
   * current view center — used by the slider and the +/− buttons). */
  const zoomTo = useCallback(
    (nextZoomRaw: number, clientX?: number, clientY?: number) => {
      setView((v) => {
        const nextZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextZoomRaw));
        const w = VIEW_WIDTH / nextZoom;
        const h = VIEW_HEIGHT / nextZoom;
        const anchor =
          clientX != null && clientY != null
            ? clientToSvg(clientX, clientY, v)
            : { x: v.x + v.w / 2, y: v.y + v.h / 2 };
        const fx = (anchor.x - v.x) / v.w;
        const fy = (anchor.y - v.y) / v.h;
        let x = anchor.x - fx * w;
        let y = anchor.y - fy * h;
        x = Math.min(Math.max(0, x), VIEW_WIDTH - w);
        y = Math.min(Math.max(0, y), VIEW_HEIGHT - h);
        return { x, y, w, h };
      });
    },
    [clientToSvg],
  );

  // Native wheel listener with { passive: false } so preventDefault actually
  // stops the page from scrolling while the cursor zooms the map. React's
  // onWheel is passive, so it can't do this.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      zoomTo((VIEW_WIDTH / view.w) * factor, e.clientX, e.clientY);
    };
    svg.addEventListener('wheel', handler, { passive: false });
    return () => svg.removeEventListener('wheel', handler);
  }, [zoomTo, view.w]);

  const onPointerDown = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    dragRef.current = {
      startClientX: e.clientX,
      startClientY: e.clientY,
      startView: view,
      moved: false,
    };
    setIsDragging(true);
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }, [view]);

  const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const dxPx = e.clientX - drag.startClientX;
    const dyPx = e.clientY - drag.startClientY;
    if (Math.abs(dxPx) + Math.abs(dyPx) > 3) drag.moved = true;
    const dx = (dxPx / rect.width) * drag.startView.w;
    const dy = (dyPx / rect.height) * drag.startView.h;
    const x = Math.min(Math.max(0, drag.startView.x - dx), VIEW_WIDTH - drag.startView.w);
    const y = Math.min(Math.max(0, drag.startView.y - dy), VIEW_HEIGHT - drag.startView.h);
    setView({ x, y, w: drag.startView.w, h: drag.startView.h });
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
    setIsDragging(false);
  }, []);

  const resetView = useCallback(() => setView(FULL_VIEW), []);

  const markerR = 8 / zoom;
  const markerHoverR = 11 / zoom;
  // A larger transparent hit target so the small dots are easy to tap/click
  // even before hovering — clicking anywhere near a marker selects it.
  const markerHitR = 16 / zoom;
  const markerStroke = 1.5 / zoom;
  const selectedStroke = 2.5 / zoom;
  const provinceStroke = 0.6 / zoom;
  const atFullView = zoom <= MIN_ZOOM + 0.001;

  return (
    <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
      {/* Map column — sits toward the left; the panel fills the rest. */}
      <div className="relative min-w-0 lg:flex-1">
        {/* Zoom controls (buttons + slider) — sized for easy tapping. */}
        <div className="absolute right-2 top-2 z-10 flex flex-col items-center gap-1.5 rounded-lg border border-border bg-background/95 p-1.5 shadow-md">
          <button
            type="button"
            aria-label="ซูมเข้า"
            onClick={() => zoomTo(zoom * ZOOM_STEP)}
            className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-secondary active:bg-secondary/80"
          >
            <Plus className="h-6 w-6" />
          </button>
          <input
            type="range"
            aria-label="ระดับการซูม"
            min={MIN_ZOOM}
            max={MAX_ZOOM}
            step={0.1}
            value={zoom}
            onChange={(e) => zoomTo(Number(e.target.value))}
            className="h-28 w-11 cursor-pointer accent-primary"
            style={{ writingMode: 'vertical-lr', direction: 'rtl' }}
          />
          <button
            type="button"
            aria-label="ซูมออก"
            onClick={() => zoomTo(zoom / ZOOM_STEP)}
            className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-secondary active:bg-secondary/80"
          >
            <Minus className="h-6 w-6" />
          </button>
          <button
            type="button"
            aria-label="รีเซ็ตมุมมอง"
            onClick={resetView}
            disabled={atFullView}
            className="flex h-11 w-11 items-center justify-center rounded-md hover:bg-secondary active:bg-secondary/80 disabled:opacity-40"
          >
            <RotateCcw className="h-6 w-6" />
          </button>
        </div>

        <svg
          ref={svgRef}
          viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
          preserveAspectRatio="xMidYMid meet"
          className={`block max-h-[70vh] w-full touch-none ${isDragging ? 'cursor-grabbing' : 'cursor-grab'}`}
          style={{ aspectRatio: `${VIEW_WIDTH} / ${VIEW_HEIGHT}` }}
          role="img"
          aria-label="แผนที่ประเทศไทยแสดงตำแหน่งแปลง"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
        >
          {/* Province outlines */}
          <g fill="#eef2f0" stroke="#cbd5d1" strokeWidth={provinceStroke}>
            {PROVINCE_SHAPES.map((p) => (
              <path key={p.name} d={p.d} />
            ))}
          </g>

          {/* Plot markers */}
          <g>
            {placed.map((pp) => {
              const { plot, x, y, color } = pp;
              const isSel = selected?.plot.id === plot.id;
              const isHov = hover?.placed.plot.id === plot.id;
              const setHoverHere = (e: React.MouseEvent) =>
                setHover({ placed: pp, clientX: e.clientX, clientY: e.clientY });
              const selectHere = () => {
                // Suppress the click that ends a pan drag.
                if (dragRef.current?.moved) return;
                setSelected(pp);
              };
              return (
                <g key={plot.id} className="cursor-pointer">
                  {/* Visible dot */}
                  <circle
                    cx={x}
                    cy={y}
                    r={isSel || isHov ? markerHoverR : markerR}
                    fill={color}
                    fillOpacity={0.85}
                    stroke={isSel ? '#111827' : '#ffffff'}
                    strokeWidth={isSel ? selectedStroke : markerStroke}
                    className="pointer-events-none transition-[r]"
                  />
                  {/* Larger invisible hit target — makes the small dots easy
                      to tap/click without needing pixel-perfect aim. */}
                  <circle
                    data-marker={plot.id}
                    cx={x}
                    cy={y}
                    r={markerHitR}
                    fill="transparent"
                    onMouseEnter={setHoverHere}
                    onMouseMove={(e) =>
                      setHover((h) => (h ? { ...h, clientX: e.clientX, clientY: e.clientY } : h))
                    }
                    onMouseLeave={() => setHover(null)}
                    onClick={selectHere}
                  />
                </g>
              );
            })}
          </g>
        </svg>

        {hover && (
          <div
            className="pointer-events-none fixed z-50 max-w-[240px] rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md"
            style={{ left: hover.clientX + 12, top: hover.clientY + 12 }}
          >
            <div className="font-semibold text-foreground">{hover.placed.plot.plotCode}</div>
            {hover.placed.plot.name && (
              <div className="text-muted-foreground">{hover.placed.plot.name}</div>
            )}
            <div className="mt-1 flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: hover.placed.color }}
              />
              <span className="text-foreground">{hover.placed.plot.currentCrop ?? 'ไม่ระบุพืช'}</span>
            </div>
            {hover.placed.plot.province && (
              <div className="mt-0.5 text-muted-foreground">{hover.placed.plot.province}</div>
            )}
          </div>
        )}

        <p className="mt-2 text-center text-xs text-muted-foreground">
          แสดง {placed.length} แปลงบนแผนที่
          {typeof totalCount === 'number' && totalCount !== placed.length
            ? ` จาก ${totalCount} แปลง`
            : ''}
          {skipped > 0 ? ` · ${skipped} แปลงไม่มีพิกัด/นอกขอบเขต` : ''}
          {' · '}คลิกหมุดดูรายละเอียด · เลื่อนล้อ/สไลเดอร์เพื่อซูม
        </p>
      </div>

      {/* Right info panel — overview by default, plot detail when selected. */}
      <div className="w-full shrink-0 lg:w-80">
        {selected ? (
          <PlotDetailPanel placed={selected} onClose={() => setSelected(null)} />
        ) : (
          <MapOverviewPanel plots={placed.map((p) => p.plot)} />
        )}
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-right text-sm font-medium text-foreground">{value}</span>
    </div>
  );
}

/** At-a-glance summary of the plots currently on the map — derived entirely
 * from data the map already has (no extra fetch). */
function MapOverviewPanel({ plots }: { plots: PlotSummary[] }) {
  const stats = useMemo(() => {
    const total = plots.length;

    // Average current yield %.
    const pcts = plots.map((p) => toNumberOrNull(p.currentYieldPct)).filter((v): v is number => v != null);
    const avgYield = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;

    // Total expected yield (current), grouped by unit; report the dominant unit.
    const byUnit = new Map<string, number>();
    for (const p of plots) {
      const cur = computeCurrentExpectedYield(p.expectedYieldFull, p.currentYieldPct);
      if (cur == null) continue;
      const unit = p.expectedYieldUnit ?? '';
      byUnit.set(unit, (byUnit.get(unit) ?? 0) + cur);
    }
    let totalExpected: number | null = null;
    let totalUnit: string | null = null;
    for (const [unit, sum] of byUnit) {
      if (totalExpected == null || sum > totalExpected) {
        totalExpected = sum;
        totalUnit = unit || null;
      }
    }

    // Crop mix (top 6 + "อื่นๆ").
    const cropCounts = new Map<string, number>();
    for (const p of plots) {
      const key = (p.currentCrop ?? '').trim() || '__none__';
      cropCounts.set(key, (cropCounts.get(key) ?? 0) + 1);
    }
    const sortedCrops = [...cropCounts.entries()].sort((a, b) => b[1] - a[1]);
    const topCrops = sortedCrops.slice(0, 6).map(([key, count]) => ({
      label: key === '__none__' ? 'ไม่ระบุพืช' : key,
      color: key === '__none__' ? UNSPECIFIED_CROP_COLOR : cropColor(key),
      count,
    }));
    const otherCount = sortedCrops.slice(6).reduce((s, [, c]) => s + c, 0);

    // Plots missing a yield plan.
    const missingPlan = plots.filter(
      (p) => describeYieldPlanGap(p.plantCount, p.expectedYieldFull) != null,
    ).length;

    // Top provinces.
    const provCounts = new Map<string, number>();
    for (const p of plots) {
      if (p.province) provCounts.set(p.province, (provCounts.get(p.province) ?? 0) + 1);
    }
    const topProvinces = [...provCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);

    return { total, avgYield, totalExpected, totalUnit, topCrops, otherCount, missingPlan, topProvinces };
  }, [plots]);

  if (stats.total === 0) {
    return (
      <aside className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground shadow-sm">
        ไม่มีแปลงในมุมมองนี้
      </aside>
    );
  }

  const maxCrop = Math.max(...stats.topCrops.map((c) => c.count), 1);

  return (
    <aside className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-foreground">ภาพรวมแปลงบนแผนที่</h3>

      {/* Headline metrics */}
      <div className="mb-4 grid grid-cols-2 gap-2">
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-lg font-semibold text-foreground">{stats.total.toLocaleString('th-TH')}</div>
          <div className="text-xs text-muted-foreground">แปลงที่แสดง</div>
        </div>
        <div className="rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-lg font-semibold text-green-700">
            {stats.avgYield != null ? `${stats.avgYield.toFixed(0)}%` : '—'}
          </div>
          <div className="text-xs text-muted-foreground">Yield เฉลี่ย</div>
        </div>
        <div className="col-span-2 rounded-md bg-secondary/40 px-3 py-2">
          <div className="text-lg font-semibold text-foreground">
            {stats.totalExpected != null
              ? formatYieldQuantity(stats.totalExpected, stats.totalUnit)
              : '—'}
          </div>
          <div className="text-xs text-muted-foreground">ผลผลิตรวมที่คาดว่าจะได้</div>
        </div>
      </div>

      {/* Crop mix mini-bars */}
      <div className="mb-4">
        <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          สัดส่วนชนิดพืช
        </div>
        <div className="space-y-1.5">
          {stats.topCrops.map((c) => (
            <div key={c.label} className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: c.color }} />
              <span className="w-20 shrink-0 truncate text-xs text-foreground" title={c.label}>{c.label}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary/60">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${(c.count / maxCrop) * 100}%`, backgroundColor: c.color }}
                />
              </div>
              <span className="w-6 shrink-0 text-right text-xs tabular-nums text-muted-foreground">{c.count}</span>
            </div>
          ))}
          {stats.otherCount > 0 && (
            <div className="pl-4 text-xs text-muted-foreground">+ อื่นๆ {stats.otherCount} แปลง</div>
          )}
        </div>
      </div>

      {/* Top provinces */}
      {stats.topProvinces.length > 0 && (
        <div className="mb-3">
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            จังหวัดที่มีแปลงมากสุด
          </div>
          <div className="flex flex-wrap gap-1.5">
            {stats.topProvinces.map(([name, count]) => (
              <span key={name} className="rounded-full bg-secondary px-2 py-0.5 text-xs text-foreground">
                {name} · {count}
              </span>
            ))}
          </div>
        </div>
      )}

      {stats.missingPlan > 0 && (
        <p className="rounded-md bg-orange-50 px-2 py-1.5 text-xs text-orange-700">
          {stats.missingPlan} แปลงยังไม่ตั้งแผนผลผลิต
        </p>
      )}

      <p className="mt-3 text-center text-xs text-muted-foreground">คลิกหมุดเพื่อดูรายละเอียดแต่ละแปลง</p>
    </aside>
  );
}

function PlotDetailPanel({ placed, onClose }: { placed: PlacedPlot; onClose: () => void }) {
  const { plot, color } = placed;
  const currentExpected = computeCurrentExpectedYield(plot.expectedYieldFull, plot.currentYieldPct);
  const pct = toNumberOrNull(plot.currentYieldPct);
  const gap = describeYieldPlanGap(plot.plantCount, plot.expectedYieldFull);

  return (
    <aside className="rounded-lg border border-border bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-semibold text-foreground">{plot.plotCode}</div>
          {plot.name && <div className="text-xs text-muted-foreground">{plot.name}</div>}
        </div>
        <button
          type="button"
          aria-label="ปิดรายละเอียด"
          onClick={onClose}
          className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {plot.province && <div className="mb-3 text-xs text-muted-foreground">{plot.province}</div>}

      {/* Crop */}
      <div className="mb-3 flex items-center gap-2 rounded-md bg-secondary/40 px-3 py-2">
        <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: color }} />
        <div>
          <div className="text-sm font-medium text-foreground">{plot.currentCrop ?? 'ไม่ระบุพืช'}</div>
          {plot.currentVariety && (
            <div className="text-xs text-muted-foreground">{plot.currentVariety}</div>
          )}
        </div>
      </div>

      {/* Yield detail */}
      <div className="border-t border-border pt-2">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          ผลผลิต (Yield)
        </div>
        <DetailRow
          label="จำนวนต้น/จำนวนปลูก"
          value={plot.plantCount != null ? plot.plantCount.toLocaleString('th-TH') : '—'}
        />
        <DetailRow
          label="เป้าผลิต"
          value={formatYieldQuantity(plot.expectedYieldFull, plot.expectedYieldUnit) ?? '—'}
        />
        <DetailRow
          label={`ที่คาดว่าจะได้${pct != null ? ` (${pct}%)` : ''}`}
          value={
            currentExpected != null ? (
              <span className="text-green-700">
                {formatYieldQuantity(currentExpected, plot.expectedYieldUnit)}
              </span>
            ) : (
              '—'
            )
          }
        />
        {gap && (
          <p className="mt-2 rounded-md bg-orange-50 px-2 py-1 text-xs text-orange-700">{gap}</p>
        )}
      </div>
    </aside>
  );
}

export default ThailandPlotMap;
