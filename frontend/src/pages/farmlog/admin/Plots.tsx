/**
 * Plots — paginated list per supplier + create / edit / deactivate / assign users.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ChevronDown,
  ClipboardCheck,
  Download,
  ExternalLink,
  Eye,
  Loader2,
  MapPin,
  Pencil,
  Phone,
  PowerOff,
  Plus,
  Printer,
  QrCode,
  Search,
  Sprout,
  Unlock,
  Upload,
  Users,
  X,
} from 'lucide-react';
import {
  assignPlotUsers,
  createPlotWithCycle,
  downloadPlotImportTemplate,
  fetchAllPlotsForSupplier,
  getPlot,
  listPlotCycleLabels,
  listPlotProvinces,
  listPlots,
  plotToQrLabel,
  searchPlotsByPhone,
  updatePlot,
  type PlotImportTemplateParams,
  type PlotStatusFilter,
  type PlotUpdatePayload,
  type PlotWithCycleCreatePayload,
  type PlotSummary,
} from '../../../api/plots';
import { downloadBlob } from '../../../lib/downloadBlob';
import { listSuppliers, type SupplierSummary } from '../../../api/suppliers';
import { listUsers } from '../../../api/users';
import { useHasPermission } from '../../../hooks/useHasPermission';
import { useAuthStore } from '../../../stores/auth';
import type { UserSummary } from '../../../types/auth';
import { PlotQrPrintSheet, type PlotQrLabelData } from '../../../components/farmlog/PlotQrPrintSheet';
import { PlotImportModal } from '../../../components/farmlog/PlotImportModal';
import { MasterDataSelect } from '../../../components/farmlog/MasterDataSelect';
import { SearchableFilterCombobox } from '../../../components/farmlog/SearchableFilterCombobox';
import {
  CyclePlanFields,
  cyclePlanFields,
  refineCyclePlan,
  toPayload as cycleValuesToPayload,
  DeactivatePlotModal,
  ReactivatePlotModal,
  ReactivatePlotWithCycleModal,
} from '../../../components/farmlog/PlotCycleModals';
import {
  PlotAccessPhoneFields,
  buildPlotAccessPhoneConfig,
  emptyPlotAccessPhoneFieldsValue,
  type PlotAccessPhoneFieldsValue,
} from '../../../components/farmlog/PlotAccessPhoneFields';
import { PlotAccessPhoneModal } from '../../../components/farmlog/PlotAccessPhoneModal';
import { listMasterData, masterDataQueryKey } from '../../../api/masterdata';
import { ActionMenu, type ActionMenuItem } from '../../../components/ActionMenu';
import { toNumberOrNull } from '../../../lib/numeric';
import { fetchAllPages } from '../../../lib/paginate';
import { formatThaiMobile } from '../../../lib/phone';
import {
  computeCurrentExpectedYield,
  describeYieldPlanGap,
  formatYieldQuantity,
} from '../../../lib/yield-planning';
import { plotHasActiveCycle } from '../../../lib/plot-cycle';
import { canViewVariety } from '../../../lib/variety-visibility';

/** Supplier display for a plot row (round 6.1) — prefers the denormalised
 * supplierCode/supplierName that now come with every plot; falls back to the
 * active-suppliers map only if those are somehow blank, and to a short id as
 * the absolute last resort. */
function plotSupplierDisplay(
  plot: PlotSummary,
  supplierById: Map<string, SupplierSummary>,
): { code: string; name: string } | null {
  if (plot.supplierCode) return { code: plot.supplierCode, name: plot.supplierName };
  const sup = supplierById.get(plot.supplierId);
  return sup ? { code: sup.code, name: sup.name } : null;
}

/** Compact "80% → 800 kg / 1,000 kg" summary for the Plots list (round 17),
 * or a clear "ยังไม่ตั้งแผนผลผลิต"-style warning (round 18) when the base
 * plan (plant count + expected yield at 100%) isn't set yet — never a
 * silent blank cell. */
function YieldCell({ plot, onPlanClick }: { plot: PlotSummary; onPlanClick?: () => void }) {
  // Round 8-6I — a permanently deactivated plot can't plan a yield at all
  // (checked BEFORE the active-cycle check below, since a deactivated plot
  // also has no active cycle and would otherwise show the more generic
  // "รอเริ่มรอบปลูก" — misleading, since the actual blocker is that the
  // plot itself is closed, not merely between cycles).
  if (!plot.isActive) {
    return (
      <span className="inline-flex items-center justify-center gap-1 rounded-full bg-gray-200 px-2.5 py-1 text-xs font-medium text-gray-600">
        ปิดใช้งาน
      </span>
    );
  }
  // Round 7.3 — a plot with no active planting cycle has nothing to plan a
  // yield against yet; say so distinctly from "plan exists but incomplete".
  if (!plotHasActiveCycle(plot)) {
    return (
      <span className="inline-flex items-center justify-center gap-1 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
        รอเริ่มรอบปลูก
      </span>
    );
  }

  // Round 7.4 — the yield PLAN (plant count + expected yield) is sourced from
  // the ACTIVE cycle (authoritative); fall back to the plot mirror only for a
  // pre-7.3.1 response shape that lacks activeCycle* (activeCycleId undefined).
  // currentYieldPct stays from the plot (inspection snapshot — no cycle field).
  const usesCycle = plot.activeCycleId != null;
  const planCount = usesCycle ? plot.activeCyclePlantCount : plot.plantCount;
  const planFull = usesCycle ? plot.activeCycleExpectedYieldFull : plot.expectedYieldFull;
  const planUnit = usesCycle ? plot.activeCycleExpectedYieldUnit : plot.expectedYieldUnit;

  const gap = describeYieldPlanGap(planCount, planFull);
  if (gap) {
    const content = (
      <>
        <AlertTriangle className="h-3 w-3 shrink-0" /> {gap}
      </>
    );
    if (onPlanClick) {
      return (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onPlanClick(); }}
          className="inline-flex items-center justify-center gap-1 rounded-full bg-warning/15 px-2.5 py-1 text-xs font-medium text-warning-readable transition-colors hover:bg-warning/25 focus:outline-none focus:ring-2 focus:ring-ring"
          title="เปิดหน้าตั้งค่าแผนผลผลิตของแปลงนี้"
        >
          {content}
        </button>
      );
    }
    return (
      <span className="inline-flex items-center justify-center gap-1 rounded-full bg-warning/15 px-2.5 py-1 text-xs font-medium text-warning-readable">
        {content}
      </span>
    );
  }

  const pct = toNumberOrNull(plot.currentYieldPct);
  const full = toNumberOrNull(planFull);
  const pctLabel = pct != null ? `${pct}%` : '—';
  const current = computeCurrentExpectedYield(planFull, plot.currentYieldPct);
  const currentLabel = current != null ? formatYieldQuantity(current, planUnit) : '—';
  const fullLabel = formatYieldQuantity(full, planUnit);
  return (
    <span className="font-semibold text-success-readable">
      {`${pctLabel} → ${currentLabel} / ${fullLabel}`}
    </span>
  );
}

/** Two labeled lines of planting-cycle info for the Plots list (round 8-3K —
 * replaces the old unlabeled " / "-joined plantingCycleText, whose combined
 * string buried "Lot" in an ambiguous run with no label). Sourced from the
 * ACTIVE cycle (round 7.4, authoritative). Falls back to the plot mirror
 * ONLY for a pre-7.3.1 response shape that lacks the read-model entirely
 * (activeCycleId undefined) — same null-vs-undefined distinction as
 * plotHasActiveCycle (lib/plot-cycle.ts). activeCycleId === null is backend
 * truth "no active cycle" and must NOT fall back to the mirror (round 7.6
 * fix: a plot with no active cycle was showing its stale pre-cycle
 * crop/variety/lot/planting-date here even though its status badge correctly
 * said "รอเริ่มรอบปลูก"/"ปิดแล้ว") — shows that same label here instead.
 * Line 1: "รอบ: <name> · พืช: <crop> / <variety>"
 * Line 2: "Lot: <lotNo> · ปลูก: <วันที่ไทย>"
 * Either line — or a whole part within a line — is omitted (not "null") when
 * unset; "—" only when there's genuinely nothing to show at all. */
function PlantingCycleCell({ plot, canSeeVariety }: { plot: PlotSummary; canSeeVariety: boolean }) {
  if (!plot.isActive) {
    // Round 8-6I.1 Part D — never say "รอเริ่มรอบปลูก" for an inactive
    // plot: that phrasing implies the user can start a cycle whenever
    // ready, but an inactive plot must be reactivated FIRST (the "ปิดใช้งาน"
    // badge next to the name already covers that). Neutral wording only —
    // checked BEFORE the active-cycle check below so it always wins for an
    // inactive plot, regardless of what plotHasActiveCycle would say.
    return <div className="text-xs text-muted-foreground">ไม่มีรอบปลูกที่เปิดอยู่</div>;
  }
  if (!plotHasActiveCycle(plot)) {
    return <div className="text-xs text-muted-foreground">รอเริ่มรอบปลูก</div>;
  }

  const hasReadModel = plot.activeCycleId !== undefined;
  const crop = hasReadModel ? plot.activeCycleCrop : plot.currentCrop;
  const variety = hasReadModel ? plot.activeCycleVariety : plot.currentVariety;
  const lotNo = hasReadModel ? plot.activeCycleLotNo : plot.currentLotNo;
  // Round 8-5B — PO / P.Code of the active cycle (denormalized read mirror).
  const poNumber = plot.activeCyclePoNumber;
  const pCode = plot.activeCyclePCode;
  const plantingDate = hasReadModel ? plot.activeCyclePlantingDate : plot.currentPlantingDate;
  // Round 8.0 — lead with the admin-chosen season name (activeCycleLabel, e.g.
  // "jun2026"); fall back to "รอบที่ <activeCycleNo>" when there's no label.
  const cycleName = plot.activeCycleLabel?.trim()
    || (plot.activeCycleNo != null ? `รอบที่ ${plot.activeCycleNo}` : null);
  // Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only; a Supplier-side
  // caller never sees it here, even joined into this one identity string.
  const identity = [crop, canSeeVariety ? variety : null].filter(Boolean).join(' / ');
  const plantingDateLabel = plantingDate
    ? new Date(plantingDate).toLocaleDateString('th-TH', { day: 'numeric', month: 'short', year: 'numeric' })
    : null;

  const line1 = [
    cycleName ? `รอบ: ${cycleName}` : null,
    identity ? `พืช: ${identity}` : null,
  ].filter((p): p is string => !!p);
  // Round 8-5B compact PO · P.Code · Lot line (— fallback per field).
  // Round 8-12B — "Lot ระบบ" now that a cycle can also carry a supplier lot.
  const line2 = `PO: ${poNumber || '—'} · P.Code: ${pCode || '—'} · Lot ระบบ: ${lotNo || '—'}`;
  // Round 8-12B — the SUPPLIER's own lot, on its own line and ONLY when the
  // active cycle actually has one: a "—" line on every row would cost height
  // on the majority of plots to say nothing. Read straight from the active-
  // cycle read model (backend truth) — there is no plot mirror for it, and the
  // no-active-cycle cases already returned above, so a stale value from an
  // older cycle can never appear here.
  const supplierLotNo = plot.activeCycleSupplierLotNo?.trim() || null;
  const line3 = plantingDateLabel ? `ปลูก: ${plantingDateLabel}` : null;

  return (
    <div className="text-xs text-muted-foreground">
      {line1.length > 0 && <div>{line1.join(' · ')}</div>}
      <div className="font-mono text-[11px]">{line2}</div>
      {supplierLotNo && (
        <div className="font-mono text-[11px]">Supplier Lot: {supplierLotNo}</div>
      )}
      {line3 && <div>{line3}</div>}
    </div>
  );
}

/** เบอร์หลัก cell — full formatted number, or a clear "ยังไม่ตั้ง" badge (never
 * a silent blank) so an admin notices a plot with no access phone yet. */
function PrimaryPhoneCell({ phone }: { phone: string | null }) {
  if (!phone) {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
        ยังไม่ตั้ง
      </span>
    );
  }
  return <span className="font-mono text-xs">{formatThaiMobile(phone)}</span>;
}

/** เบอร์เสริม cell (round 8-3C) — none: "—"; 1-2: full formatted numbers;
 * >2: the first two + "อีก N เบอร์", with a title tooltip listing every
 * number so nothing is truly hidden, just compacted. */
function AdditionalPhonesCell({ phones }: { phones: string[] }) {
  if (phones.length === 0) return <span className="text-muted-foreground">—</span>;
  const formatted = phones.map(formatThaiMobile);
  if (formatted.length <= 2) {
    return <span className="font-mono text-xs">{formatted.join(', ')}</span>;
  }
  const shown = formatted.slice(0, 2).join(', ');
  const restCount = formatted.length - 2;
  return (
    <span className="font-mono text-xs" title={formatted.join(', ')}>
      {shown} <span className="text-muted-foreground">อีก {restCount} เบอร์</span>
    </span>
  );
}

function supplierLabel(supplier: SupplierSummary): string {
  return `${supplier.code} — ${supplier.name}`;
}

function SupplierFilterCombobox({
  suppliers,
  value,
  onChange,
}: {
  suppliers: SupplierSummary[];
  value: string;
  onChange: (supplierId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);
  const selected = suppliers.find((supplier) => supplier.id === value) ?? null;
  const normalizedSearch = search.trim().toLowerCase();
  const visibleSuppliers = normalizedSearch
    ? suppliers.filter((supplier) => supplierLabel(supplier).toLowerCase().includes(normalizedSearch))
    : suppliers;

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative min-w-[260px] flex-1 sm:max-w-sm">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-label="กรอง Supplier"
        className="flex w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-secondary/60 focus:outline-none focus:ring-2 focus:ring-ring"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={selected ? 'truncate text-foreground' : 'truncate text-muted-foreground'}>
          {selected ? supplierLabel(selected) : '— ทุก Supplier —'}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-lg">
          <label className="relative block">
            <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ค้นหา Supplier..."
              className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
          </label>
          <div role="listbox" className="mt-2 max-h-64 overflow-y-auto">
            <button
              type="button"
              role="option"
              aria-selected={value === ''}
              onClick={() => { onChange(''); setSearch(''); setOpen(false); }}
              className="flex w-full items-center rounded-md px-3 py-2 text-left text-sm hover:bg-secondary"
            >
              — ทุก Supplier —
            </button>
            {visibleSuppliers.map((supplier) => (
              <button
                key={supplier.id}
                type="button"
                role="option"
                aria-selected={supplier.id === value}
                onClick={() => { onChange(supplier.id); setSearch(''); setOpen(false); }}
                className={`flex w-full flex-col rounded-md px-3 py-2 text-left text-sm hover:bg-secondary ${
                  supplier.id === value ? 'bg-primary/10 text-primary' : ''
                }`}
              >
                <span className="font-medium">{supplier.code}</span>
                <span className="text-xs text-muted-foreground">{supplier.name}</span>
              </button>
            ))}
            {visibleSuppliers.length === 0 && (
              <p className="px-3 py-3 text-sm text-muted-foreground">ไม่พบ Supplier</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Round 8-6G — mirrors backend app/api/deps/scope.py's _FULL_ACCESS_ROLES
// exactly (internal:super_admin / internal:admin / farmlog:supervisor →
// scope "all"). This is a UX nicety only — it decides whether the "ทุก
// Supplier" option even APPEARS, never whether the request succeeds; the
// backend re-checks via the same _resolve_scope helper and 403s anyone
// else regardless of what this constant says (never infer full access from
// Supplier count or anything client-only — Part E).
const FULL_SCOPE_ROLE_NAMES = new Set([
  'internal:super_admin', 'internal:admin', 'farmlog:supervisor',
]);

/** "ดาวน์โหลด Excel" split-button + dropdown (round 8-6G Part E) — replaces
 * the old single "Excel ตามตัวกรอง" button. Same open/outside-click/Escape
 * pattern as SupplierFilterCombobox above (no portal/library). The
 * "ทุก Supplier" item only renders for a full-scope caller (see
 * FULL_SCOPE_ROLE_NAMES); a Supplier Owner/Field Officer never sees it at
 * all, on top of the backend's own 403 if it were ever reached directly. */
function DownloadTemplateMenu({
  onFiltered,
  onAllSuppliers,
  showAllSuppliers,
  pending,
}: {
  onFiltered: () => void;
  onAllSuppliers: () => void;
  showAllSuppliers: boolean;
  pending: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={pending}
        aria-haspopup="menu"
        aria-expanded={open}
        className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary disabled:opacity-60"
      >
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
        ดาวน์โหลด Excel
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label="ดาวน์โหลด Excel"
          className="absolute right-0 z-20 mt-1 w-72 max-w-[90vw] overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-lg"
        >
          <button
            type="button"
            role="menuitem"
            // Explicit aria-label: without it, the accessible name is the
            // concatenation of BOTH spans below (label + description) per
            // the "name from content" rule — a screen reader would read one
            // long run-on sentence, and `getByRole(..., {name: 'ตามตัวกรอง
            // ปัจจุบัน'})` wouldn't match. aria-label overrides content-derived
            // naming entirely, so the name stays just the short label.
            aria-label="ตามตัวกรองปัจจุบัน"
            onClick={() => { setOpen(false); onFiltered(); }}
            className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-secondary"
          >
            <span className="text-sm font-medium text-foreground">ตามตัวกรองปัจจุบัน</span>
            <span className="text-xs text-muted-foreground">
              ดาวน์โหลดแปลงที่ใช้งานอยู่ตาม Supplier และตัวกรองปัจจุบัน
            </span>
          </button>
          {showAllSuppliers && (
            <button
              type="button"
              role="menuitem"
              aria-label="ทุก Supplier"
              onClick={() => { setOpen(false); onAllSuppliers(); }}
              className="flex w-full flex-col items-start gap-0.5 border-t border-border px-3 py-2 text-left hover:bg-secondary"
            >
              <span className="text-sm font-medium text-foreground">ทุก Supplier</span>
              <span className="text-xs text-muted-foreground">
                ดาวน์โหลดแปลงที่ใช้งานอยู่ของทุก Supplier ที่คุณมีสิทธิ์ดู
              </span>
            </button>
          )}
          {/* Round 8-27E — the "รายการที่ไม่รวม" sheet is gone; anything left
              out of the file is reported on screen after the download instead
              (see templateExcludedCount). */}
          <div className="border-t border-border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
            ไฟล์มี 2 ชีต: &quot;นำเข้ารอบใหม่&quot; (ชีตที่ระบบอ่าน) และ &quot;ตัวอย่าง&quot;
            — ถ้ามีแปลงที่ไม่ได้อยู่ในไฟล์ ระบบจะแจ้งหลังดาวน์โหลด
          </div>
        </div>
      ) : null}
    </div>
  );
}

// Rows-per-page options for the Plots list. "all" fetches every matching
// plot across pages (via fetchAllPages) rather than a single window.
// Round 8-25D — added 500 so this stays the SAME [100, 200, 500, 'ทั้งหมด']
// contract now used consistently across Suppliers/RecordList/both reports.
const PAGE_SIZE_OPTIONS = [100, 200, 500, 'all'] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
const DEFAULT_PAGE_SIZE: PageSize = 100;
// Per-request cap when fetching "all" — paged through under the hood.
const ALL_FETCH_CHUNK = 200;

// Round 8-17A.2 Part B — "ใช้งาน" (active) is now the baseline plot-status
// filter, not "ทั้งหมด" (all): a fresh page load, and the "ล้างค่า" button,
// both land here. Kept as one named constant so the default state, the
// clear-filters reset, and the "is this narrowed away from baseline" checks
// (hasActiveFilters, templateFilterSummary) can't drift apart.
const DEFAULT_PLOT_STATUS: PlotStatusFilter = 'active';

// Round 8-18B.1 — partial search bounds for the two search boxes.
// Identity: a 1-character fragment matches nearly every plot in scope, which
// is a slow, useless query rather than a search.
const MIN_NAME_CODE_SEARCH_CHARS = 2;
// Access number: digits only, 4-10 of them. The backend re-checks this by
// hand before it queries (api/v1/plots.py) — this is the immediate-feedback
// copy, never the security boundary. /public/inspect is unaffected and still
// requires a complete canonical 10-digit number.
const MIN_ACCESS_NUMBER_DIGITS = 4;
const MAX_ACCESS_NUMBER_DIGITS = 10;
const ACCESS_NUMBER_FRAGMENT_RE = new RegExp(
  `^[0-9]{${MIN_ACCESS_NUMBER_DIGITS},${MAX_ACCESS_NUMBER_DIGITS}}$`,
);

function isValidAccessNumberFragment(value: string): boolean {
  return ACCESS_NUMBER_FRAGMENT_RE.test(value);
}

function describeQueryError(error: unknown): string {
  const maybeResponse = error as {
    response?: { status?: number; data?: { detail?: unknown; message?: unknown } };
    message?: string;
  };
  const status = maybeResponse.response?.status;
  const detail = maybeResponse.response?.data?.detail ?? maybeResponse.response?.data?.message;
  if (status) return `HTTP ${status}${detail ? ` — ${detail}` : ''}`;
  return maybeResponse.message ?? 'unknown error';
}

// Round 8-6B — filtered template filename (Part D). Uses the Supplier CODE
// (never the UUID) from the already-loaded active-suppliers map; adds the
// province when one is selected. Sanitizes both segments defensively (the
// backend never derives a filename from user input, but this builds one
// client-side from filter state, so it gets the same treatment as
// api/plots.ts's own sanitizeDownloadFilename). Falls back to a generic name
// if the supplier code can't be resolved (should not normally happen — a
// download requires filterSupplier to already be a valid, loaded supplier id).
function sanitizeFilenameSegment(value: string): string {
  return value.replace(/[/\\:*?"<>|]/g, '').trim();
}

function buildTemplateFilename(
  supplierId: string,
  province: string,
  supplierById: Map<string, SupplierSummary>,
): string {
  const code = sanitizeFilenameSegment(supplierById.get(supplierId)?.code ?? '');
  if (!code) return 'plot-next-cycle-template.xlsx';
  const provinceSegment = province ? sanitizeFilenameSegment(province) : '';
  const name = provinceSegment ? `plot-next-cycle-${code}-${provinceSegment}` : `plot-next-cycle-${code}`;
  return `${name}.xlsx`;
}

const optionalNumberInput = z.preprocess(
  (value) => (value === '' || value === undefined ? undefined : value),
  z.coerce.number().min(0).optional(),
);

const optionalCoordinateInput = (min: number, max: number) => z.preprocess(
  (value) => (value === '' || value === undefined ? undefined : value),
  z.coerce.number().min(min).max(max).optional(),
);

// Physical-plot fields ONLY (round 8.0.4 ownership lock) — shared by both
// the Create and Edit forms below. Planting-cycle identity + yield planning
// moved to PlotCycle (see cyclePlanFields, imported from PlotCycleModals —
// same field defs/validation the Start/Edit/Rollover cycle modals use, so
// the two can't drift).
const physicalPlotFields = {
  village: z.string().max(255).optional().or(z.literal('')),
  district: z.string().max(255).optional().or(z.literal('')),
  province: z.string().max(100).optional().or(z.literal('')),
  latitude: optionalCoordinateInput(-90, 90),
  longitude: optionalCoordinateInput(-180, 180),
  rai: optionalNumberInput,
};

const editPlotSchema = z.object({
  name: z.string().min(1, 'กรุณาระบุชื่อแปลง').max(255),
  ...physicalPlotFields,
});
type EditPlotFormValues = z.infer<typeof editPlotSchema>;

// Create = physical fields + the plot's first active cycle in one atomic
// request (round 8.0.4) — see createPlotWithCycle. requireUnitWithYield is
// the same expectedYieldUnit-required-with-expectedYieldFull rule the cycle
// modals enforce, reused here so it can't drift.
const createPlotSchema = z.object({
  supplierId: z.string().min(1, 'กรุณาเลือก Supplier'),
  plotCode: z.string().min(1, 'กรุณาระบุรหัสแปลง').max(50),
  name: z.string().min(1, 'กรุณาระบุชื่อแปลง').max(255),
  ...physicalPlotFields,
  ...cyclePlanFields,
}).superRefine(refineCyclePlan);
type CreatePlotFormValues = z.infer<typeof createPlotSchema>;

function numberOrNull(value: number | undefined): number | null {
  return value === undefined ? null : value;
}

function plotMutationErrorMessage(error: unknown): string {
  if (!error) return '';
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    const data = error.response?.data as { detail?: unknown; message?: unknown } | string | undefined;
    let detail = '';
    if (typeof data === 'string') {
      detail = data;
    } else if (data?.detail) {
      detail = Array.isArray(data.detail)
        ? data.detail.map((item) => {
            if (typeof item === 'string') return item;
            if (item && typeof item === 'object' && 'msg' in item) return String(item.msg);
            return String(item);
          }).join(', ')
        : String(data.detail);
    } else if (data?.message) {
      detail = String(data.message);
    }
    return `${status ? `HTTP ${status}` : 'Network error'}${detail ? `: ${detail}` : ''}`;
  }
  return error instanceof Error ? error.message : 'Network error';
}

export function Plots() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  const [q, setQ] = useState('');
  // Round 8-18B — "ชื่อแปลงปลูก หรือรหัสแปลง" is now its own dedicated box
  // (searchText), separate from the phone box (phoneText) below — no more
  // single combined field that also matched จังหวัด/หมายเลขเข้าตรวจ.
  const [searchText, setSearchText] = useState('');
  const [phoneText, setPhoneText] = useState('');
  // Guard-rail error for the NAME/CODE box only: a phone-shaped entry typed
  // there must never be sent as GET q (would leak digits into the URL/access
  // log) — same defensive reasoning as phoneSearchError below, just scoped to
  // the other box now that phone has its own dedicated input.
  const [nameCodeError, setNameCodeError] = useState<string | null>(null);
  const [filterSupplier, setFilterSupplier] = useState<string>('');
  const [filterProvince, setFilterProvince] = useState<string>('');
  const [filterCrop, setFilterCrop] = useState<string>('');
  const [filterVariety, setFilterVariety] = useState<string>('');
  // Round 8-18 — "รอบปลูกปัจจุบัน" (cycleLabel) filter: matches ONLY a
  // plot's ACTIVE PlotCycle, exact match, never a closed/historical cycle.
  const [filterCycleLabel, setFilterCycleLabel] = useState<string>('');
  // Round 8-25K — "วันที่เริ่ม...ถึง": matches ONLY the plot's ACTIVE
  // PlotCycle.plantingDate (same "active cycle only" scope as
  // filterCycleLabel above), never a closed/historical cycle's. Deliberately
  // NOT forwarded to the Excel template download (handleDownloadTemplate
  // below) — same precedent as the access-number search box, which the
  // template's own filter summary already calls out as list-only.
  const [filterPlantingDateFrom, setFilterPlantingDateFrom] = useState<string>('');
  const [filterPlantingDateTo, setFilterPlantingDateTo] = useState<string>('');
  // Round 8-6I Part D — plot status filter ("สถานะแปลง"): ทั้งหมด/ใช้งาน/
  // ปิดใช้งาน. Round 8-17A.2 Part B — default changed from 'all' to 'active'
  // (DEFAULT_PLOT_STATUS): a fresh visit to the Plots page should show only
  // in-use plots, not every inactive one mixed in.
  const [filterPlotStatus, setFilterPlotStatus] = useState<PlotStatusFilter>(DEFAULT_PLOT_STATUS);
  // Round 8-17A.2 Part C/D — secure access-number search. Set only by
  // applySearch(); null means "not currently searching by number" (plain
  // q/text search, or no search at all). Round 8-18B.1 — this now holds a
  // validated 4-10 DIGIT FRAGMENT, not a normalized full number, which is
  // why it is no longer called phoneSearchNormalized.
  // phoneSearchError is a LOCAL validation message (malformed input) shown
  // WITHOUT ever sending a request — never derived from a failed API call.
  const [phoneSearchDigits, setPhoneSearchDigits] = useState<string | null>(null);
  const [phoneSearchError, setPhoneSearchError] = useState<string | null>(null);
  // Round 8-17A.2.1 — a plain counter, bumped on every successful phone
  // search (see applySearch below). This is the React Query key
  // discriminator for phone mode; it replaces a phone-derived hash
  // (round 8-17A.2's cacheKeyDigest) so the query key carries NEITHER the
  // raw phone NOR anything deterministically derived from it.
  const [phoneSearchNonce, setPhoneSearchNonce] = useState(0);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [assigningId, setAssigningId] = useState<string | null>(null);
  const [managingPhonesId, setManagingPhonesId] = useState<string | null>(null);
  const [deactivatingId, setDeactivatingId] = useState<string | null>(null);
  const [reactivatingId, setReactivatingId] = useState<string | null>(null);
  const [reactivatingWithCycleId, setReactivatingWithCycleId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);
  // Round 8-27E — replaces the workbook's old "รายการที่ไม่รวม" sheet. How
  // many plots matched the filters but are NOT in the downloaded file (their
  // Supplier is deactivated, or their own status contradicts the status
  // filter). Shown here rather than inside the file, because the moment a
  // user needs to know part of their request is missing is right after they
  // click Download — not several sheets into a workbook they may never open.
  const [templateExcludedCount, setTemplateExcludedCount] = useState(0);
  const [printItems, setPrintItems] = useState<PlotQrLabelData[] | null>(null);
  const [supplierPrintLoading, setSupplierPrintLoading] = useState(false);
  const [supplierPrintError, setSupplierPrintError] = useState('');
  // Round 8-6I Part G — success feedback for reactivate/reactivate-with-cycle.
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const canCreate = useHasPermission('plots.create');
  const canUpdate = useHasPermission('plots.update');
  const canAssign = useHasPermission('plots.assign');
  const canInspect = useHasPermission('records.create');
  // Round 8-6I Part E — activation privilege, same permission the backend's
  // deactivate/reactivate endpoints require (plots.delete). "เปิดใช้งานและ
  // เริ่มรอบปลูกใหม่" additionally requires plots.update (canUpdate above).
  const canReactivate = useHasPermission('plots.delete');

  // Round 8-6G Part E — "ทุก Supplier" only ever appears for a full-scope
  // caller; never inferred from Supplier count or anything else client-only.
  // The backend's own 403 (via the same _resolve_scope helper) is the real
  // authority regardless of what this shows.
  const currentUser = useAuthStore((s) => s.user);
  const hasFullSupplierScope = !!currentUser?.roles?.some((r) => FULL_SCOPE_ROLE_NAMES.has(r.name));
  // Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only.
  const canSeeVariety = canViewVariety(currentUser?.roles);

  useEffect(() => {
    const manage = searchParams.get('manage');
    const plotId = searchParams.get('plotId');
    if (!plotId) return;

    if (manage === 'edit' && canUpdate) {
      setCreating(false);
      setAssigningId(null);
      setEditingId(plotId);
      setSearchParams({}, { replace: true });
    } else if (manage === 'assign' && canAssign) {
      setCreating(false);
      setEditingId(null);
      setAssigningId(plotId);
      setSearchParams({}, { replace: true });
    }
  }, [canAssign, canUpdate, searchParams, setSearchParams]);

  const {
    data: suppliers = [],
    isError: suppliersIsError,
    error: suppliersError,
  } = useQuery<SupplierSummary[]>({
    queryKey: ['suppliers-active'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
    staleTime: 5 * 60 * 1000,
  });

  const {
    data: plots = [],
    isLoading,
    isError: plotsIsError,
    error: plotsError,
  } = useQuery({
    // Round 8-17A.2.1 — phone mode uses phoneSearchNonce, NEVER the raw
    // phoneSearchDigits or anything derived from it, so the query key
    // (visible to React Query devtools / any cache inspection) can't carry
    // the PII itself.
    queryKey: phoneSearchDigits
      ? ['plots', 'phone', page, pageSize, phoneSearchNonce, q, filterSupplier, filterProvince, filterCrop, filterVariety, filterPlotStatus, filterCycleLabel, filterPlantingDateFrom, filterPlantingDateTo]
      : ['plots', 'text', page, pageSize, q, filterSupplier, filterProvince, filterCrop, filterVariety, filterPlotStatus, filterCycleLabel, filterPlantingDateFrom, filterPlantingDateTo],
    queryFn: () => {
      // Round 8-17A.2 Part C/D — secure phone search: POST body, never
      // listPlots' GET ?q=. The other filters (supplier/province/crop/
      // variety/status/cycleLabel) apply identically either way; round
      // 8-18B adds `q` (ชื่อ/รหัสแปลง) to the body too, so both search
      // boxes intersect in ONE request instead of the number having to
      // fall back to a GET.
      if (phoneSearchDigits) {
        const phoneFilters = {
          phone: phoneSearchDigits,
          q: q || undefined,
          supplierId: filterSupplier || undefined,
          province: filterProvince || undefined,
          crop: filterCrop || undefined,
          variety: filterVariety || undefined,
          plotStatus: filterPlotStatus,
          cycleLabel: filterCycleLabel || undefined,
          plantingDateFrom: filterPlantingDateFrom || undefined,
          plantingDateTo: filterPlantingDateTo || undefined,
        };
        if (pageSize === 'all') {
          return fetchAllPages(
            (offset, limit) => searchPlotsByPhone({ ...phoneFilters, limit, offset }),
            ALL_FETCH_CHUNK,
          );
        }
        return searchPlotsByPhone({ ...phoneFilters, limit: pageSize, offset: page * pageSize });
      }
      const filters = {
        q: q || undefined,
        supplierId: filterSupplier || undefined,
        province: filterProvince || undefined,
        crop: filterCrop || undefined,
        variety: filterVariety || undefined,
        plotStatus: filterPlotStatus,
        cycleLabel: filterCycleLabel || undefined,
        plantingDateFrom: filterPlantingDateFrom || undefined,
        plantingDateTo: filterPlantingDateTo || undefined,
      };
      // "ทั้งหมด": page through every match under the hood (single logical
      // page, so prev/next are disabled below). Otherwise fetch one window.
      if (pageSize === 'all') {
        return fetchAllPages(
          (offset, limit) => listPlots({ ...filters, limit, offset }),
          ALL_FETCH_CHUNK,
        );
      }
      return listPlots({ ...filters, limit: pageSize, offset: page * pageSize });
    },
    staleTime: 60 * 1000,
  });

  const {
    data: provinces = [],
  } = useQuery<string[]>({
    queryKey: ['plot-provinces', filterSupplier, filterPlotStatus],
    queryFn: () => listPlotProvinces({
      supplierId: filterSupplier || undefined,
      plotStatus: filterPlotStatus,
    }),
    staleTime: 5 * 60 * 1000,
  });

  // Round 8-18 — SearchableFilterCombobox's real option list: distinct
  // cycleLabel values from ACTIVE PlotCycle rows only, within the caller's
  // scope (RLS + this same supplier_id/plot_status narrowing the provinces
  // query above uses).
  const {
    data: cycleLabels = [],
  } = useQuery<string[]>({
    queryKey: ['plot-cycle-labels', filterSupplier, filterPlotStatus],
    queryFn: () => listPlotCycleLabels({
      supplierId: filterSupplier || undefined,
      plotStatus: filterPlotStatus,
    }),
    staleTime: 5 * 60 * 1000,
  });

  // Crop/variety filter options come from master data (the same canonical
  // values the create/edit modal writes to plots.current_crop/current_variety)
  // — same source the Plot Status report's crop filter uses. Variety options
  // narrow to the chosen crop via the master-data parent link.
  const { data: cropOptions = [] } = useQuery({
    queryKey: masterDataQueryKey('crop', null, true),
    queryFn: () => listMasterData({ type: 'crop', activeOnly: true }),
    staleTime: 5 * 60 * 1000,
  });
  const { data: varietyOptions = [] } = useQuery({
    queryKey: masterDataQueryKey('variety', filterCrop || null, true),
    queryFn: () => listMasterData({
      type: 'variety',
      parent: filterCrop || undefined,
      activeOnly: true,
    }),
    staleTime: 5 * 60 * 1000,
  });

  // Surface a fetch failure explicitly — without this, `data = []` on error
  // (the default above) renders identically to a genuinely empty list, so a
  // failed request (expired session, backend error, etc.) silently looks
  // like "ไม่พบข้อมูล" instead of an error a user could act on.
  const loadErrorMessage = plotsIsError
    ? `โหลดรายการแปลงไม่สำเร็จ: ${describeQueryError(plotsError)}`
    : suppliersIsError
      ? `โหลดรายชื่อ Supplier ไม่สำเร็จ: ${describeQueryError(suppliersError)}`
      : null;

  // Round 8-17A.2 Part B — DEFAULT_PLOT_STATUS ('active') is the baseline,
  // so it is no longer counted as an "extra" filter (matches filterProvince
  // === '' etc. above, each compared against ITS OWN unfiltered baseline).
  const hasActiveFilters = q.trim() !== '' || searchText.trim() !== '' || phoneText.trim() !== ''
    || filterSupplier !== ''
    || filterProvince !== '' || filterCrop !== '' || filterVariety !== '' || filterCycleLabel !== ''
    || filterPlantingDateFrom !== '' || filterPlantingDateTo !== ''
    || filterPlotStatus !== DEFAULT_PLOT_STATUS || phoneSearchDigits !== null;

  function applySearch() {
    const nameCode = searchText.trim();
    const phoneRaw = phoneText.trim();
    setPage(0);
    // Round 8-6C Part B — the applied search feeds the filtered download
    // (Part C), so a stale download error from before this search must not
    // linger once the applied filter has actually changed.
    setTemplateError(null);

    // Round 8-18B.1 — the old looksLikePhoneAttempt guard is GONE from this
    // box: an all-digit entry like "002" is a perfectly ordinary plot-code
    // fragment, and blocking it made partial identity search unusable. The
    // number never reaches a GET ?q= regardless, because the number box has
    // its own state (phoneText) and only ever travels in a POST body.
    if (nameCode && nameCode.length < MIN_NAME_CODE_SEARCH_CHARS) {
      setNameCodeError(`กรุณากรอกอย่างน้อย ${MIN_NAME_CODE_SEARCH_CHARS} ตัวอักษรสำหรับค้นหาชื่อ/รหัสแปลง`);
      return;
    }

    // Round 8-18B — the two boxes INTERSECT: with a number filled in, the
    // name/code text rides along in the POST body as `q` (never a GET ?q=),
    // so "this number AND this plot" is one secure request.
    if (phoneRaw) {
      // Round 8-18B.1 — a PARTIAL number is the point now, so
      // normalizeThaiMobile (which demands a complete canonical 10-digit
      // mobile) can't be the validator here. Digits only, 4-10 of them —
      // the same bounds the backend re-checks by hand before querying.
      // /public/inspect is untouched and still requires the full number.
      if (!isValidAccessNumberFragment(phoneRaw)) {
        // Generic Thai message; nothing is sent at all — not even the
        // name/code half, which would otherwise silently show a wider
        // result set than the user actually asked for.
        setPhoneSearchError(
          `กรุณากรอกหมายเลขสำหรับเข้าตรวจเป็นตัวเลข ${MIN_ACCESS_NUMBER_DIGITS}-${MAX_ACCESS_NUMBER_DIGITS} หลัก`,
        );
        return;
      }
      setNameCodeError(null);
      setPhoneSearchError(null);
      setQ(nameCode);
      setPhoneSearchDigits(phoneRaw);
      // Round 8-17A.2.1 — a new nonce every valid search, even a same-number
      // re-search, so the queryKey always changes and a fresh fetch fires (no
      // stale cached result for a different underlying access-phone
      // assignment served from an old slot).
      setPhoneSearchNonce((n) => n + 1);
      return;
    }

    setNameCodeError(null);
    setPhoneSearchError(null);
    setPhoneSearchDigits(null);
    setQ(nameCode);
  }

  function clearFilters() {
    setPage(0);
    setSearchText('');
    setPhoneText('');
    setNameCodeError(null);
    setQ('');
    setPhoneSearchDigits(null);
    setPhoneSearchError(null);
    setFilterSupplier('');
    setFilterProvince('');
    setFilterCrop('');
    setFilterVariety('');
    setFilterCycleLabel('');
    setFilterPlantingDateFrom('');
    setFilterPlantingDateTo('');
    setFilterPlotStatus(DEFAULT_PLOT_STATUS);
    setSupplierPrintError('');
    setTemplateError(null);
  }

  // Id → supplier lookup map, rebuilt only when the suppliers list changes —
  // avoids an O(n) suppliers.find() per plot on every render (table rows +
  // printableItems both need one).
  const supplierById = useMemo(
    () => new Map(suppliers.map((s: SupplierSummary) => [s.id, s])),
    [suppliers],
  );

  // Printable = the current page's plots. Round 6.1: every plot summary now
  // carries supplierCode/supplierName, so a QR label is built straight from
  // it (plotToQrLabel) — no dependency on the capped active-suppliers list.
  const printableItems: PlotQrLabelData[] = plots.map((p: PlotSummary) => plotToQrLabel(p));

  // "พิมพ์ QR ทั้ง Supplier" — unlike printableItems above, this fetches every
  // active plot for the filtered supplier across all pages, not just what's
  // currently on screen (see api/plots.ts fetchAllPlotsForSupplier).
  async function handlePrintBySupplier() {
    if (!filterSupplier) { setSupplierPrintError('กรุณาเลือก Supplier ก่อน'); return; }
    setSupplierPrintError('');
    setSupplierPrintLoading(true);
    try {
      const allPlots = await fetchAllPlotsForSupplier(filterSupplier);
      if (allPlots.length === 0) {
        const label = supplierById.get(filterSupplier)?.code ?? 'ที่เลือก';
        setSupplierPrintError(`Supplier "${label}" ไม่มีแปลงที่ใช้งานอยู่ให้พิมพ์`);
        return;
      }
      // Each plot brings its own supplier data — no supplier arg needed.
      setPrintItems(allPlots.map((p) => plotToQrLabel(p)));
    } catch {
      setSupplierPrintError('โหลดรายการแปลงไม่สำเร็จ กรุณาลองใหม่');
    } finally {
      setSupplierPrintLoading(false);
    }
  }

  // Round 6.1 — one place that refreshes everything a plot mutation can
  // affect, without over-broad invalidation: the list, that plot's detail,
  // the province filter options (create/edit can introduce a new province),
  // and the Plot Status report (derived from the plot list). Pass the plot id
  // to also refresh its detail cache; omit `withProvinces` for mutations that
  // can't change a province (deactivate/assign).
  function invalidatePlotQueries(plotId?: string | null, withProvinces = false) {
    qc.invalidateQueries({ queryKey: ['plots'] });
    qc.invalidateQueries({ queryKey: ['report-plot-status'] });
    if (withProvinces) qc.invalidateQueries({ queryKey: ['plot-provinces'] });
    if (plotId) {
      qc.invalidateQueries({ queryKey: ['plot', plotId] });
      // Create Plot now creates the plot's first cycle in the same request
      // (round 8.0.4) — invalidate its cycle list too so Plot Detail's
      // history isn't stale if it's already cached.
      qc.invalidateQueries({ queryKey: ['plot-cycles', plotId] });
    }
  }

  // Round 8-6I Part G — reactivate/reactivate-with-cycle can flip is_active
  // (affecting the plot-status filter's result set + province options) and,
  // for the with-cycle variant, start a brand-new cycle (affecting the Cycle
  // Yield report too) — invalidate every key the round's Part G lists.
  function invalidateReactivateQueries(plotId: string) {
    invalidatePlotQueries(plotId, true);
    qc.invalidateQueries({ queryKey: ['report-cycle-yield'] });
  }

  // Round 8-6B — "Excel ตามตัวกรอง" downloads the round 8-6A filtered/
  // contextual template: every ACTIVE plot matching the current Supplier/
  // province/crop/variety/applied-search filter, seeded with start_next_cycle
  // rows. Requires a Supplier (Part A/C item 1) — checked in
  // handleDownloadTemplate BEFORE mutate() is ever called, so an unselected
  // Supplier never reaches the API at all.
  const templateM = useMutation({
    mutationFn: (params: PlotImportTemplateParams) => downloadPlotImportTemplate(params),
    // Round 8-6C Part A — filename must reflect the SUBMITTED request, not
    // whatever filterSupplier/filterProvince happen to be by the time the
    // response arrives. TanStack Query passes the mutate() variables as the
    // 2nd onSuccess arg — use that snapshot, never the live filter state
    // (which the user may have already changed while the request was in
    // flight).
    onSuccess: ({ blob, excludedCount }, submittedParams) => {
      setTemplateError(null);
      setTemplateExcludedCount(excludedCount);
      // Round 8-6G — all_suppliers has its own fixed filename (no
      // Supplier/province to embed); still read from submittedParams, never
      // live filter state, for the same click-time-snapshot reason as the
      // filtered branch below (round 8-6C Part A).
      const filename = submittedParams.templateMode === 'all_suppliers'
        ? 'plot-next-cycle-ALL-SUPPLIERS.xlsx'
        : buildTemplateFilename(submittedParams.supplierId ?? '', submittedParams.province ?? '', supplierById);
      downloadBlob(blob, filename);
    },
    onError: (error) => {
      // downloadPlotImportTemplate already normalizes any error (JSON error
      // blob or network failure) into a PlotImportReportError with a
      // ready-to-show Thai message — no separate blob-parsing helper needed
      // here (see api/plots.ts's extractErrorDetailFromBlob).
      setTemplateError(error instanceof Error ? error.message : 'ดาวน์โหลด Excel ไม่สำเร็จ');
      // A failed download excluded nothing — clear any notice from a previous
      // one so it can't be read as belonging to this attempt.
      setTemplateExcludedCount(0);
    },
  });

  function handleDownloadTemplate() {
    if (!filterSupplier) {
      setTemplateError('กรุณาเลือก Supplier ก่อนดาวน์โหลด Excel ตามตัวกรอง');
      return;
    }
    setTemplateError(null);
    templateM.mutate({
      supplierId: filterSupplier,
      province: filterProvince || undefined,
      crop: filterCrop || undefined,
      variety: filterVariety || undefined,
      cycleLabel: filterCycleLabel || undefined,
      // Applied search only (Part C: "ใช้ q ซึ่งเป็น applied search" — never
      // the still-being-typed searchText).
      q: q || undefined,
      // Round 8-6J Part H — the SAME plot-status filter currently applied to
      // the on-screen list, so "active" downloads only start_next_cycle
      // rows, "inactive" only reactivate_plot_with_cycle rows, and "all"
      // (the default) mixes both in one sheet.
      plotStatus: filterPlotStatus,
    });
  }

  // Round 8-6G Part E/F — "ทุก Supplier": explicit template_mode, deliberately
  // NEVER combined with the current Supplier/province/crop/variety/q filter
  // state (downloadPlotImportTemplate itself also drops them defensively —
  // api/plots.ts). No Supplier-required guard here; the backend's own scope
  // check (403 for anyone not full-scope) is the real authority, and this
  // menu item is only ever shown to a full-scope caller in the first place.
  function handleDownloadAllSuppliers() {
    setTemplateError(null);
    // Round 8-6J Part H — plotStatus is the one filter still forwarded
    // alongside templateMode:'all_suppliers'; every other filter stays
    // dropped (downloadPlotImportTemplate's own build-query step drops them
    // defensively too — api/plots.ts).
    templateM.mutate({ templateMode: 'all_suppliers', plotStatus: filterPlotStatus });
  }

  // Round 8-6B Part E — compact "what's about to download" summary; only the
  // filters actually selected are shown (no empty "จังหวัด: " placeholders).
  const templateFilterSummary = useMemo(() => {
    if (!filterSupplier) return '';
    const supplier = supplierById.get(filterSupplier);
    const parts = [`Supplier: ${supplier ? `${supplier.code} — ${supplier.name}` : filterSupplier}`];
    if (filterProvince) parts.push(`จังหวัด: ${filterProvince}`);
    if (filterCrop) parts.push(`พืช: ${filterCrop}`);
    if (filterVariety) parts.push(`พันธุ์: ${filterVariety}`);
    // Round 8-18 — cycleLabel shown the same way, only when selected.
    if (filterCycleLabel) parts.push(`รอบปลูก: ${filterCycleLabel}`);
    // Round 8-6C Part C — q (the APPLIED search) affects the downloaded
    // file too, so it belongs in the summary; unapplied searchText does not,
    // since typing alone doesn't change what a download would contain.
    if (q) parts.push(`คำค้นหา: ${q}`);
    // Round 8-6J Part H — only shown when narrowed away from the default
    // (round 8-17A.2: DEFAULT_PLOT_STATUS 'active', was 'all' before Part B
    // — matches the pattern above: only selected filters are listed).
    if (filterPlotStatus !== DEFAULT_PLOT_STATUS) {
      parts.push(`สถานะแปลง: ${filterPlotStatus === 'active' ? 'ใช้งาน' : filterPlotStatus === 'inactive' ? 'ปิดใช้งาน' : 'ทั้งหมด'}`);
    }
    return parts.join(' · ');
  }, [filterSupplier, filterProvince, filterCrop, filterVariety, filterCycleLabel, q, filterPlotStatus, supplierById]);

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">แปลง (Plots)</h1>
          <p className="mt-1 text-sm text-muted-foreground">จัดการข้อมูลแปลงและรอบปลูก</p>
        </div>

        {/* Primary action (เพิ่มแปลง) stands apart from the secondary group
            below it — round 20: one clear "main" action instead of every
            button carrying equal visual weight. */}
        <div className="flex flex-col gap-2 sm:items-end">
          {canCreate && (
            <button
              type="button"
              onClick={() => { setEditingId(null); setCreating(true); }}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              เพิ่มแปลง
            </button>
          )}
          <div className="flex flex-wrap gap-2 sm:justify-end">
            {/* Round 8-25P — the Excel download/import share ONE column
                layout with the import parser (plot_import.py), so the
                variety column can't be safely dropped just for this file
                without risking that shared contract. Simpler and safe:
                hide both entry points from a Supplier-side caller entirely,
                same canSeeVariety gate as the rest of round 8-25O. */}
            {canSeeVariety && (
              <DownloadTemplateMenu
                onFiltered={handleDownloadTemplate}
                onAllSuppliers={handleDownloadAllSuppliers}
                showAllSuppliers={hasFullSupplierScope}
                pending={templateM.isPending}
              />
            )}
            {canSeeVariety && (canCreate || canUpdate) && (
              <button
                type="button"
                onClick={() => setImporting(true)}
                title="นำเข้าแปลงและรอบปลูกจากไฟล์ Excel"
                className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary"
              >
                <Upload className="h-4 w-4" />
                นำเข้า Excel
              </button>
            )}
            <button
              type="button"
              onClick={() => setPrintItems(printableItems)}
              disabled={printableItems.length === 0}
              title="พิมพ์ QR ของแปลงทั้งหมดที่แสดงอยู่"
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary disabled:opacity-60"
            >
              <Printer className="h-4 w-4" />
              พิมพ์ QR รายการนี้
            </button>
            <button
              type="button"
              onClick={handlePrintBySupplier}
              disabled={!filterSupplier || supplierPrintLoading}
              title="พิมพ์ QR ทุกแปลงที่ใช้งานอยู่ของ Supplier ที่เลือก (ทุกหน้า)"
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary disabled:opacity-60"
            >
              {supplierPrintLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Printer className="h-4 w-4" />}
              พิมพ์ QR ทั้ง Supplier
            </button>
            {/* Round 8-25I — shortcut to the PUBLIC (no-login) inspection
                entry point, for admins who want to open/demo it without a
                phone. Relative path so it resolves correctly in every
                environment (dev/UAT/prod) without hardcoding a domain.
                Opens in a new tab: this leaves the authenticated admin app
                entirely, and text/icon deliberately differ from the other
                buttons here (which all act ON this page's own data) to
                signal that. */}
            <a
              href="/public/inspect"
              target="_blank"
              rel="noopener noreferrer"
              title="เปิดหน้าตรวจแปลงสำหรับ Field Officer (ไม่ต้อง login) ในแท็บใหม่"
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium text-muted-foreground shadow-sm transition-colors hover:bg-secondary hover:text-foreground"
            >
              <ExternalLink className="h-4 w-4" />
              เปิดหน้าตรวจแปลง (Public)
            </a>
          </div>
        </div>
      </header>

      {/* Round 8-6B Part E — compact guidance next to the download button;
          the FULL explanation (Sheet-by-sheet walkthrough, etc.) lives in
          PlotImportModal instead of repeating it here at length. */}
      <div className="mt-3 space-y-1 text-xs text-muted-foreground">
        {/* Round 8-6J Part H — wording now matches whichever plot-status
            filter is selected: "all" (default) mixes both actions in one
            sheet, so it must never claim the file is active-only. */}
        <p>
          แปลงที่ใช้งาน: เริ่มรอบถัดไป · แปลงที่ปิด: เปิดแปลงพร้อมเริ่มรอบใหม่
        </p>
        {templateFilterSummary && (
          <p className="flex flex-wrap items-baseline gap-x-1">
            <span className="font-medium text-foreground">กำลังจะดาวน์โหลด:</span>
            <span>{templateFilterSummary}</span>
          </p>
        )}
        {/* Round 8-17A.2 Part E — the template download is a GET request, so
            the phone number currently being searched can NEVER be forwarded
            to it (that would put it right back in a URL query string). This
            is stated explicitly rather than silently dropped — the file
            still uses every OTHER active filter (Supplier/province/พืช/
            สถานะ), just not the search number. */}
        {templateFilterSummary && phoneSearchDigits && (
          <p className="flex items-start gap-1 text-amber-700">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>
              Template ใช้ตัวกรองอื่นที่เลือกไว้ทั้งหมด (Supplier/จังหวัด/พืช/พันธุ์/รอบปลูก/สถานะแปลง
              รวมถึงชื่อ-รหัสแปลงที่ค้นหา) แต่ไม่ใช้หมายเลขสำหรับเข้าตรวจ
              (การดาวน์โหลดไม่ส่งหมายเลขผ่าน URL)
            </span>
          </p>
        )}
        {templateFilterSummary && (
          <p>
            ช่องสีเหลืองในชีต &quot;นำเข้ารอบใหม่&quot; คือข้อมูลที่ต้องตรวจ/แก้ (cycleLabel ต้องเปลี่ยนเป็นชื่อรอบใหม่เสมอ ทั้งเริ่มรอบถัดไปและเปิดใช้งานแปลง) ·
            คอลัมน์ currentPlotStatus ไว้อ้างอิงเท่านั้น แก้ค่าช่องนี้ไม่ทำให้สถานะแปลงเปลี่ยน ·
            ชีต &quot;ตัวอย่าง&quot; สีแดงระบบไม่นำเข้า · การดาวน์โหลด/ตรวจสอบไฟล์ยังไม่เปลี่ยนข้อมูลใดๆ
            ระบบจะเปลี่ยนข้อมูลก็ต่อเมื่อกดยืนยันนำเข้า (Commit) สำเร็จเท่านั้น
          </p>
        )}
      </div>

      {templateError && (
        <div className="mt-2 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">{templateError}</span>
        </div>
      )}

      {templateExcludedCount > 0 && (
        <div className="mt-2 flex items-start justify-between gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <span className="min-w-0 break-words">
            มี {templateExcludedCount.toLocaleString()} แปลงที่ตรงตัวกรอง แต่ไม่ได้อยู่ในไฟล์
            — แปลงหรือ Supplier ที่ปิดใช้งาน หรือไม่ตรงกับตัวกรองสถานะแปลงที่เลือก
          </span>
          <button
            type="button"
            onClick={() => setTemplateExcludedCount(0)}
            aria-label="ปิดข้อความแจ้งเตือน"
            className="shrink-0 text-amber-700 hover:text-amber-900"
          >
            ✕
          </button>
        </div>
      )}

      {/* Round 8-6I Part G — reactivate/reactivate-with-cycle success feedback. */}
      {successMessage && (
        <div className="mt-2 flex items-start justify-between gap-2 rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800">
          <span className="min-w-0 break-words">{successMessage}</span>
          <button
            type="button"
            onClick={() => setSuccessMessage(null)}
            aria-label="ปิดข้อความ"
            className="shrink-0 text-green-700 hover:text-green-900"
          >
            ✕
          </button>
        </div>
      )}

      {supplierPrintError && (
        <p className="mt-3 text-sm text-destructive">{supplierPrintError}</p>
      )}

      <div className="mt-5 rounded-lg border border-border bg-card p-3 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          {/* Round 8-18B — the old combined box ("รหัส/ชื่อแปลง/จังหวัด หรือ
              หมายเลขสำหรับเข้าตรวจ") is gone, split into these two. This one
              matches plot name/code ONLY — จังหวัด has its own filter below,
              and a number belongs in the box beside it. */}
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">ชื่อแปลงหรือรหัสแปลง</span>
            <span className="relative flex items-center">
              <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
              <input
                type="search"
                value={searchText}
                onChange={(e) => {
                  setSearchText(e.target.value);
                  if (nameCodeError) setNameCodeError(null);
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') applySearch(); }}
                placeholder="ค้นหาบางส่วนได้ เช่น 002 หรือ เมล่อน"
                aria-invalid={nameCodeError ? true : undefined}
                className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </span>
          </label>
          {/* Round 8-18B — dedicated access-number box: one search finds EVERY
              plot this number is authorized on, whether it is that plot's
              เบอร์หลัก or เบอร์เสริม (search_plots_by_phone's EXISTS covers both
              access_type values already — no separate mode, no duplicate rows).
              autoComplete off: this is a lookup key, never the user's own
              number, so browser autofill would only offer the wrong value. */}
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">หมายเลขสำหรับเข้าตรวจ</span>
            <span className="relative flex items-center">
              <Phone className="absolute left-2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                inputMode="numeric"
                autoComplete="off"
                value={phoneText}
                maxLength={MAX_ACCESS_NUMBER_DIGITS}
                onChange={(e) => {
                  // Round 8-18B.1 — digits only: strip anything else as it
                  // is typed/pasted, so the box can never hold a value the
                  // backend would reject (and a '%'/'_' can never reach the
                  // LIKE pattern).
                  setPhoneText(e.target.value.replace(/\D/g, '').slice(0, MAX_ACCESS_NUMBER_DIGITS));
                  // Round 8-17A.2 Part D — a stale format error must not
                  // linger once the user starts editing their input again.
                  if (phoneSearchError) setPhoneSearchError(null);
                  if (nameCodeError) setNameCodeError(null);
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') applySearch(); }}
                placeholder={`ค้นหาบางส่วนได้ ${MIN_ACCESS_NUMBER_DIGITS}-${MAX_ACCESS_NUMBER_DIGITS} หลัก เช่น 5552`}
                aria-invalid={phoneSearchError ? true : undefined}
                className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </span>
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={applySearch}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
            >
              <Search className="h-4 w-4" />
              ค้นหา
            </button>
            <button
              type="button"
              onClick={clearFilters}
              disabled={!hasActiveFilters}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary disabled:opacity-60"
            >
              <X className="h-4 w-4" />
              ล้างค่า
            </button>
          </div>
        </div>
        {/* Round 8-18B.1 — identity box guard: too short to be a useful
            search. Nothing is sent. */}
        {nameCodeError && (
          <p role="alert" className="mt-2 text-sm text-destructive">{nameCodeError}</p>
        )}
        {/* Round 8-17A.2 Part D item 6 — malformed number: a generic Thai
            message, NOTHING sent (never a fallback to GET q, which would
            leak the entered digits into the URL/access log). */}
        {phoneSearchError && (
          <p role="alert" className="mt-2 text-sm text-destructive">{phoneSearchError}</p>
        )}
        {phoneSearchDigits && !phoneSearchError && (
          // Deliberately does NOT echo the searched digits — a full 10-digit
          // entry would otherwise render a complete phone number on screen.
          <p className="mt-2 text-sm text-muted-foreground">
            กำลังค้นหาแปลงที่หมายเลขที่ระบุเข้าตรวจได้
            (ค้นทั้งเบอร์หลักและเบอร์เสริม){q ? ` และตรงกับ "${q}"` : ''}
          </p>
        )}
        {/* Round 8-25G — 6 filters (Supplier + สถานะ + จังหวัด + ชนิดพืช +
            พันธุ์ + รอบปลูก) at their min-widths add up to 1200px+; without
            flex-wrap they overflowed the card sideways on narrower screens
            instead of dropping to a second line. */}
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
          <SupplierFilterCombobox
            suppliers={suppliers}
            value={filterSupplier}
            onChange={(supplierId) => {
              setPage(0);
              setFilterSupplier(supplierId);
              setFilterProvince('');
              setSupplierPrintError('');
              setTemplateError(null);
            }}
          />
          {/* Round 8-6I Part D — "สถานะแปลง": ทั้งหมด/ใช้งาน/ปิดใช้งาน. Never
              adds a status COLUMN back to the table (Part D explicitly
              forbids that) — inactive plots show a badge under their name
              and a distinct YieldCell instead. */}
          <select
            value={filterPlotStatus}
            aria-label="กรองสถานะแปลง"
            onChange={(e) => {
              setPage(0);
              setFilterPlotStatus(e.target.value as PlotStatusFilter);
              // Round 8-6I.1 Part C — the province list reloads scoped to
              // plotStatus (backend Part B); a province chosen under the
              // PREVIOUS status may not be an option under the new one
              // (e.g. a province where every plot is active would vanish
              // from the "ปิดใช้งาน" dropdown), leaving filterProvince as an
              // invisible hidden filter with no matching <option> on screen.
              // Clearing it here keeps the visible dropdown and the actual
              // request in sync.
              setFilterProvince('');
              setTemplateError(null);
            }}
            className="min-w-[160px] rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="all">สถานะแปลง: ทั้งหมด</option>
            <option value="active">สถานะแปลง: ใช้งาน</option>
            <option value="inactive">สถานะแปลง: ปิดใช้งาน</option>
          </select>
          {/* Round 8-18A — searchable, same UX pattern as SupplierFilterCombobox:
              must select from the real province list, never free text. */}
          <SearchableFilterCombobox
            label="กรองจังหวัด"
            allLabel="ทุกจังหวัด"
            options={provinces}
            value={filterProvince}
            onChange={(province) => { setPage(0); setFilterProvince(province); setTemplateError(null); }}
            placeholder="ค้นหาจังหวัด..."
            emptyMessage="ไม่พบข้อมูล"
          />
          <SearchableFilterCombobox
            label="กรองชนิดพืช"
            allLabel="ทุกชนิดพืช"
            options={cropOptions.map((item) => item.value)}
            value={filterCrop}
            onChange={(crop) => {
              setPage(0);
              setFilterCrop(crop);
              // A variety chosen under the previous crop no longer makes
              // sense once the crop filter changes (same reset rule as the
              // create/edit form's crop→variety pair).
              setFilterVariety('');
              setTemplateError(null);
            }}
            placeholder="ค้นหาชนิดพืช..."
            emptyMessage="ไม่พบข้อมูล"
          />
          {/* Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only; the
              filter control itself is hidden, not just its results, for a
              Supplier-side caller. */}
          {canSeeVariety && (
            <SearchableFilterCombobox
              label="กรองพันธุ์/สายพันธุ์"
              allLabel="ทุกพันธุ์"
              options={varietyOptions.map((item) => item.value)}
              value={filterVariety}
              onChange={(variety) => { setPage(0); setFilterVariety(variety); setTemplateError(null); }}
              placeholder="ค้นหาพันธุ์..."
              emptyMessage="ไม่พบข้อมูล"
            />
          )}
          {/* Round 8-18 — "รอบปลูกปัจจุบัน" (cycleLabel): matches ONLY a
              plot's ACTIVE PlotCycle, never a closed/historical one. Options
              are the real distinct labels in scope (cycleLabels query
              above), not free text. */}
          <SearchableFilterCombobox
            label="กรองรอบปลูกปัจจุบัน"
            allLabel="ทุกรอบปลูก"
            options={cycleLabels}
            value={filterCycleLabel}
            onChange={(label) => {
              setPage(0);
              setFilterCycleLabel(label);
              setTemplateError(null);
            }}
            placeholder="ค้นหารอบปลูก..."
            emptyMessage="ไม่พบรอบปลูก"
          />
          {/* Round 8-25K — "วันที่เริ่ม...ถึง": matches ONLY the plot's
              ACTIVE PlotCycle.plantingDate (same scope as the รอบปลูกปัจจุบัน
              combobox above it). Real <label htmlFor> + id, not title/
              aria-label alone — round 8-25E already established that a
              placeholder is not a substitute for a visible label on a date
              input (native browsers ignore it, showing only mm/dd/yyyy). */}
          <div className="min-w-[160px]">
            <label htmlFor="plot-filter-planting-date-from" className="mb-1 block text-xs font-medium text-muted-foreground">
              วันที่เริ่ม (จาก)
            </label>
            <input
              id="plot-filter-planting-date-from"
              type="date"
              value={filterPlantingDateFrom}
              onChange={(e) => {
                setPage(0);
                setFilterPlantingDateFrom(e.target.value);
                setTemplateError(null);
              }}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="min-w-[160px]">
            <label htmlFor="plot-filter-planting-date-to" className="mb-1 block text-xs font-medium text-muted-foreground">
              วันที่เริ่ม (ถึง)
            </label>
            <input
              id="plot-filter-planting-date-to"
              type="date"
              value={filterPlantingDateTo}
              onChange={(e) => {
                setPage(0);
                setFilterPlantingDateTo(e.target.value);
                setTemplateError(null);
              }}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>
      </div>

      {loadErrorMessage && (
        <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {loadErrorMessage}
        </p>
      )}

      <section className="mt-4 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="px-4 py-2">รหัสแปลง</th>
                <th className="px-4 py-2">ชื่อแปลง / รอบปลูก</th>
                <th className="hidden px-4 py-2 sm:table-cell">จังหวัด</th>
                <th className="hidden px-4 py-2 sm:table-cell">Supplier</th>
                {/* Round 8-3C — access-phone columns are desktop-only (hidden
                    on mobile, same convention as จังหวัด/Supplier above); a
                    mobile user opens Plot Detail for the full phone list. */}
                <th className="hidden px-4 py-2 sm:table-cell">เบอร์หลัก</th>
                <th className="hidden px-4 py-2 sm:table-cell">เบอร์เสริม</th>
                <th className="px-4 py-2 text-center">Yield</th>
                <th className="px-4 py-2 text-right">จัดการ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {plots.length === 0 && !plotsIsError && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">
                    ไม่พบข้อมูล
                  </td>
                </tr>
              )}
              {plots.map((p: PlotSummary) => {
                const supplierDisplay = plotSupplierDisplay(p, supplierById);
                const detailPath = `/farmlog/admin/plots/${p.id}`;

                // Round 20 — "ดูรายละเอียด" is the row's primary action, so
                // it's a real Link (keyboard-focusable, Enter-activates) at
                // the plot name; everything else (QR/assign/edit/deactivate)
                // moves into one ActionMenu. clicking anywhere in the row
                // that isn't itself an interactive element (link/button/
                // select) also navigates, as a mouse-only convenience layered
                // on top of the always-present keyboard path.
                const actionItems: ActionMenuItem[] = [
                  { key: 'view', label: 'ดูรายละเอียด', icon: Eye, onClick: () => navigate(detailPath) },
                  ...(canInspect && p.isActive && plotHasActiveCycle(p) ? [{
                    key: 'inspect', label: 'ตรวจแปลง', icon: ClipboardCheck,
                    onClick: () => navigate(
                      `/farmlog/records/new?supplierId=${encodeURIComponent(p.supplierId)}&plotId=${encodeURIComponent(p.id)}`,
                    ),
                  }] : []),
                  // Round 8.0.4: split into physical-plot edit (this modal,
                  // physical fields only) vs planting-cycle/yield management
                  // (Plot Detail, where the active cycle's EditCycleModal
                  // and StartCycleModal live) — the two are no longer one
                  // combined action/modal.
                  ...(canUpdate ? [{
                    key: 'edit', label: 'แก้ไขข้อมูลแปลง', icon: Pencil,
                    onClick: () => { setCreating(false); setEditingId(p.id); },
                  }, {
                    key: 'manage-cycle', label: 'จัดการรอบปลูก / แผนผลผลิต', icon: Sprout,
                    onClick: () => navigate(detailPath),
                  }, {
                    // Round 8-3C
                    key: 'manage-phones', label: 'จัดการเบอร์เข้าตรวจ', icon: Phone,
                    onClick: () => setManagingPhonesId(p.id),
                  }] : []),
                  // QR needs no suppliers map now — the plot carries its own
                  // supplier data (round 6.1), so it always prints.
                  {
                    key: 'qr', label: 'พิมพ์ QR', icon: QrCode,
                    onClick: () => setPrintItems([plotToQrLabel(p)]),
                  },
                  // Assignment remains hidden per round 8.0. Deactivation is
                  // available again for active plots, with a confirmation
                  // modal and the same plots.delete gate as the backend.
                  ...(p.isActive && canReactivate ? [{
                    key: 'deactivate', label: 'ปิดใช้งานแปลง', icon: PowerOff,
                    onClick: () => setDeactivatingId(p.id),
                  }] : []),

                  // Round 8-6I Part E — reactivation actions, INACTIVE plots
                  // only (an active plot must never show either). "เปิดใช้งาน
                  // แปลง" needs only the activation privilege (plots.delete);
                  // "เปิดใช้งานและเริ่มรอบปลูกใหม่" additionally needs
                  // plots.update, matching the backend's stacked permission
                  // requirement on POST /{plotId}/reactivate-with-cycle.
                  ...(!p.isActive && canReactivate ? [{
                    key: 'reactivate', label: 'เปิดใช้งานแปลง', icon: Unlock,
                    onClick: () => setReactivatingId(p.id),
                  }] : []),
                  ...(!p.isActive && canReactivate && canUpdate ? [{
                    key: 'reactivate-with-cycle', label: 'เปิดใช้งานและเริ่มรอบปลูกใหม่', icon: Sprout,
                    onClick: () => setReactivatingWithCycleId(p.id),
                  }] : []),
                ];

                return (
                  <tr
                    key={p.id}
                    className="cursor-pointer transition-colors hover:bg-primary/5"
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest('a, button, input, select')) return;
                      navigate(detailPath);
                    }}
                  >
                    <td className="px-4 py-2 font-mono">{p.plotCode}</td>
                    <td className="px-4 py-2">
                      <Link
                        to={detailPath}
                        className="font-medium text-foreground hover:text-primary hover:underline"
                      >
                        {p.name}
                      </Link>
                      {/* Round 8-6I Part D — no status COLUMN; an inactive
                          plot shows this badge under its name instead. */}
                      {!p.isActive && (
                        <span className="ml-2 inline-flex items-center rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium text-gray-600">
                          ปิดใช้งาน
                        </span>
                      )}
                      <PlantingCycleCell plot={p} canSeeVariety={canSeeVariety} />
                    </td>
                    <td className="hidden px-4 py-2 text-muted-foreground sm:table-cell">{p.province ?? '—'}</td>
                    <td className="hidden px-4 py-2 text-xs sm:table-cell">
                      {supplierDisplay ? (
                        <span className="inline-flex max-w-[220px] flex-col rounded-md bg-primary/10 px-2 py-1 text-primary">
                          <span className="font-semibold">{supplierDisplay.code}</span>
                          <span className="truncate text-muted-foreground">{supplierDisplay.name}</span>
                        </span>
                      ) : (
                        <span className="text-muted-foreground">{p.supplierId.slice(0, 8)}</span>
                      )}
                    </td>
                    <td className="hidden px-4 py-2 sm:table-cell">
                      <PrimaryPhoneCell phone={p.primaryPhone} />
                    </td>
                    <td className="hidden px-4 py-2 sm:table-cell">
                      <AdditionalPhonesCell phones={p.additionalPhones} />
                    </td>
                    <td className="px-4 py-2 text-center text-xs">
                      <YieldCell
                        plot={p}
                        // Round 8.0.4 — yield planning is now cycle-owned;
                        // this navigates to Plot Detail (which offers "เริ่ม
                        // รอบปลูกใหม่"/"แก้รอบปลูก") instead of opening the
                        // physical-plot Edit modal.
                        onPlanClick={canUpdate ? () => navigate(detailPath) : undefined}
                      />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <div className="flex justify-end">
                        <ActionMenu
                          ariaLabel={`ตัวเลือกเพิ่มเติมสำหรับแปลง ${p.plotCode}`}
                          items={actionItems}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <label htmlFor="plots-page-size">แสดง</label>
          <select
            id="plots-page-size"
            value={String(pageSize)}
            onChange={(e) => {
              setPage(0);
              const v = e.target.value;
              setPageSize(v === 'all' ? 'all' : (Number(v) as PageSize));
            }}
            className="rounded-md border border-input bg-background px-2 py-1 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {PAGE_SIZE_OPTIONS.map((opt) => (
              <option key={opt} value={String(opt)}>
                {opt === 'all' ? 'ทั้งหมด' : `${opt} แถว`}
              </option>
            ))}
          </select>
        </div>
        {pageSize === 'all' ? (
          <span>{plots.length} แปลง</span>
        ) : (
          <div className="flex items-center gap-4">
            <button type="button" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} className="disabled:opacity-40">← ก่อนหน้า</button>
            <span>หน้า {page + 1}</span>
            <button type="button" disabled={plots.length < pageSize} onClick={() => setPage((p) => p + 1)} className="disabled:opacity-40">ถัดไป →</button>
          </div>
        )}
      </div>

      {creating && (
        <CreatePlotModal
          suppliers={suppliers}
          onClose={() => setCreating(false)}
          onSaved={(newPlotId) => {
            setCreating(false);
            // create can introduce a new province → refresh the filter too.
            invalidatePlotQueries(newPlotId, true);
          }}
        />
      )}

      {editingId && (
        <EditPlotModal
          plotId={editingId}
          onClose={() => setEditingId(null)}
          onSaved={() => {
            const savedId = editingId;
            setEditingId(null);
            invalidatePlotQueries(savedId, true);
          }}
        />
      )}

      {assigningId && (
        <AssignModal
          plotId={assigningId}
          onClose={() => setAssigningId(null)}
          onSaved={() => {
            const savedId = assigningId;
            setAssigningId(null);
            invalidatePlotQueries(savedId);
          }}
        />
      )}

      {managingPhonesId && (
        <PlotAccessPhoneModal
          plotId={managingPhonesId}
          plotLabel={plots.find((p: PlotSummary) => p.id === managingPhonesId)?.plotCode}
          onClose={() => setManagingPhonesId(null)}
          onSaved={() => setManagingPhonesId(null)}
        />
      )}

      {deactivatingId && (
        <DeactivatePlotModal
          plotId={deactivatingId}
          plotCode={plots.find((p: PlotSummary) => p.id === deactivatingId)?.plotCode ?? ''}
          onClose={() => setDeactivatingId(null)}
          onSaved={() => {
            const savedId = deactivatingId;
            setDeactivatingId(null);
            invalidateReactivateQueries(savedId);
            setSuccessMessage('ปิดใช้งานแปลงแล้ว');
          }}
        />
      )}

      {/* Round 8-6I Part E — reactivate (reopen only) / reactivate-with-cycle
          (atomic reopen + first cycle), each calling exactly ONE endpoint. */}
      {reactivatingId && (
        <ReactivatePlotModal
          plotId={reactivatingId}
          plotCode={plots.find((p: PlotSummary) => p.id === reactivatingId)?.plotCode ?? ''}
          onClose={() => setReactivatingId(null)}
          onSaved={() => {
            const savedId = reactivatingId;
            setReactivatingId(null);
            invalidateReactivateQueries(savedId);
            setSuccessMessage('เปิดใช้งานแปลงแล้ว');
          }}
        />
      )}

      {reactivatingWithCycleId && (
        <ReactivatePlotWithCycleModal
          plotId={reactivatingWithCycleId}
          supplierCode={plots.find((p: PlotSummary) => p.id === reactivatingWithCycleId)?.supplierCode ?? ''}
          onClose={() => setReactivatingWithCycleId(null)}
          onSaved={() => {
            const savedId = reactivatingWithCycleId;
            setReactivatingWithCycleId(null);
            invalidateReactivateQueries(savedId);
            setSuccessMessage('เปิดใช้งานแปลงและเริ่มรอบปลูกใหม่แล้ว');
          }}
        />
      )}

      {printItems && (
        <PlotQrPrintSheet items={printItems} onClose={() => setPrintItems(null)} />
      )}

      {importing && (
        <PlotImportModal
          onClose={() => setImporting(false)}
          onImported={() => {
            invalidatePlotQueries(null, true);
            // An import can roll cycles over (close_and_start_new_cycle /
            // start_next_cycle), freezing final-estimate snapshots — refresh
            // the Cycle Yield report too (round 8-2.8B). Kept here, not in the
            // shared invalidatePlotQueries, so plain plot edits don't
            // over-invalidate it.
            qc.invalidateQueries({ queryKey: ['report-cycle-yield'] });
          }}
        />
      )}
    </div>
  );
}


/**
 * Create Plot (round 8.0.4) — two sections, ONE atomic request
 * (createPlotWithCycle): physical plot data, then the plot's first active
 * planting cycle. Never calls createPlot() + createPlotCycle() as two
 * separate requests — see createPlotWithCycle's docstring for why.
 */
function CreatePlotModal({
  suppliers,
  onClose,
  onSaved,
}: {
  suppliers: SupplierSummary[];
  onClose: () => void;
  onSaved: (plotId?: string) => void;
}) {
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<CreatePlotFormValues>({
    resolver: zodResolver(createPlotSchema),
    // Round 8-5B — the first cycle needs PO/pCode (required) and defaults its
    // lot to Auto.
    defaultValues: { lotMode: 'auto', poNumber: '', pCode: '' },
  });

  // Access phones (round 8-3C) — a plain controlled value alongside the RHF
  // form (PlotAccessPhoneFields is not react-hook-form-registered so it can be
  // reused unchanged inside PlotAccessPhoneModal too). Validated the same way
  // the modal does: buildPlotAccessPhoneConfig blocks submit on any error
  // (additional-without-primary, duplicates, invalid format).
  const [accessPhoneValue, setAccessPhoneValue] = useState<PlotAccessPhoneFieldsValue>(
    emptyPlotAccessPhoneFieldsValue(),
  );
  const phoneResult = buildPlotAccessPhoneConfig(accessPhoneValue);

  // Round 8-12B — the Auto Lot preview needs the SUPPLIER's code, which the
  // user picks by id here. Read it off the already-loaded suppliers list
  // rather than asking the user to retype it; '' until a supplier is chosen,
  // which makes the preview show a placeholder instead of a fabricated code.
  // Never the supplier NAME — the lot is built from the code.
  const selectedSupplierCode =
    suppliers.find((s: SupplierSummary) => s.id === watch('supplierId'))?.code ?? '';

  // Supplier self-service: a supplier-scoped user's suppliers list holds
  // only their own supplier (backend get_supplier_scope_filter), so with
  // exactly one option there's nothing to choose — preselect it.
  useEffect(() => {
    if (suppliers.length === 1) {
      setValue('supplierId', suppliers[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suppliers]);

  const createM = useMutation({
    mutationFn: (p: PlotWithCycleCreatePayload) => createPlotWithCycle(p),
  });

  async function onSubmit(values: CreatePlotFormValues) {
    // Additional-without-primary / duplicate / invalid-format phone errors
    // block the create outright — same rule PlotAccessPhoneModal enforces.
    // (The Save button is also disabled in this state; this is belt-and-
    // suspenders against an Enter-key submit from another field.)
    if (phoneResult.hasErrors) return;

    const hasAnyPhone = phoneResult.config != null && (
      phoneResult.config.primaryPhone != null || phoneResult.config.additionalPhones.length > 0
    );

    const payload: PlotWithCycleCreatePayload = {
      plot: {
        supplierId: values.supplierId,
        plotCode: values.plotCode,
        name: values.name,
        village: values.village || null,
        district: values.district || null,
        province: values.province || null,
        latitude: numberOrNull(values.latitude),
        longitude: numberOrNull(values.longitude),
        rai: numberOrNull(values.rai),
      },
      cycle: cycleValuesToPayload(values),
      // Omitted entirely when no phone was entered — the SAME single
      // createPlotWithCycle request either way, never a second PUT.
      ...(hasAnyPhone ? { accessPhones: phoneResult.config } : {}),
    };
    let result: Awaited<ReturnType<typeof createPlotWithCycle>>;
    try {
      result = await createM.mutateAsync(payload);
    } catch {
      // Already reflected in createM.error (rendered below) — caught here
      // (rather than left to reject) so a failed create doesn't surface as
      // an unhandled promise rejection from the form's synchronous submit
      // handler (same reasoning as RolloverCycleModal's onSubmit). The
      // modal stays open — no partial plot is left behind since the
      // backend's single transaction never committed one.
      return;
    }
    onSaved(result.plot.id);
  }

  // Live "planning still incomplete" hint (round 18) — same wording as
  // Plot List/Detail via describeYieldPlanGap, so admins see the exact
  // same message everywhere this data shows up.
  const plantCountWatch = watch('plantCount');
  const expectedYieldFullWatch = watch('expectedYieldFull');
  const yieldPlanGap = describeYieldPlanGap(
    plantCountWatch === undefined ? null : plantCountWatch,
    expectedYieldFullWatch === undefined ? null : expectedYieldFullWatch,
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <MapPin className="h-4 w-4" />
            เพิ่มแปลงใหม่
          </h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
          <div>
            <h3 className="mb-3 text-sm font-semibold text-foreground">ข้อมูลแปลง</h3>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Supplier *" error={errors.supplierId?.message} className="col-span-2">
                <select {...register('supplierId')} className="field-input">
                  <option value="">— เลือก Supplier —</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={s.id}>{s.code} — {s.name}</option>
                  ))}
                </select>
              </Field>
              <Field label="รหัสแปลง *" error={errors.plotCode?.message}>
                <input {...register('plotCode')} className="field-input uppercase" placeholder="P001" />
              </Field>
              <Field label="ชื่อแปลง *" error={errors.name?.message}>
                <input {...register('name')} className="field-input" />
              </Field>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">ที่ตั้ง / พิกัด / พื้นที่</h3>
            <div className="grid grid-cols-3 gap-4">
              <Field label="หมู่บ้าน/ตำบล" error={errors.village?.message}>
                <input {...register('village')} className="field-input" />
              </Field>
              <Field label="อำเภอ" error={errors.district?.message}>
                <input {...register('district')} className="field-input" />
              </Field>
              <Field label="จังหวัด" error={errors.province?.message}>
                {/* Master-data-driven (was free text) — same "province"
                    category the Master Data admin manages; avoids
                    เชียงใหม่/เชียงไหม่ drift. */}
                <MasterDataSelect
                  type="province"
                  placeholder="— เลือกจังหวัด —"
                  value={watch('province') || null}
                  onChange={(v) => setValue('province', v ?? '', { shouldDirty: true })}
                />
              </Field>
            </div>

            <div className="mt-4 grid grid-cols-3 gap-4">
              <Field label="Latitude" error={errors.latitude?.message}>
                <input {...register('latitude')} type="number" step="0.0000001" className="field-input" placeholder="13.7563" />
              </Field>
              <Field label="Longitude" error={errors.longitude?.message}>
                <input {...register('longitude')} type="number" step="0.0000001" className="field-input" placeholder="100.5018" />
              </Field>
              <Field label="พื้นที่ (ไร่)" error={errors.rai?.message}>
                <input {...register('rai')} type="number" step="0.01" className="field-input" placeholder="0.00" />
              </Field>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">รอบปลูกแรก</h3>
            <div className="space-y-4">
              <CyclePlanFields register={register} errors={errors} watch={watch} setValue={setValue}
                supplierCode={selectedSupplierCode} mode="create" />
            </div>
            {yieldPlanGap && (
              <p className="mt-2 flex items-center gap-1 text-xs text-amber-600">
                <span aria-hidden="true">⚠</span> {yieldPlanGap} — กรอกทั้งสองช่องเพื่อให้ระบบคำนวณผลผลิตที่คาดว่าจะได้
              </p>
            )}
          </div>

          <div className="border-t border-border pt-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">เบอร์โทรสำหรับเข้าตรวจ</h3>
            {/* Round 8-3C — omitted from the request entirely when left
                blank; when filled, sent as `accessPhones` on the SAME
                createPlotWithCycle request (never a separate PUT). */}
            <PlotAccessPhoneFields value={accessPhoneValue} onChange={setAccessPhoneValue} />
          </div>

          {/* Sticky footer — stays pinned to the modal bottom while the
              fields scroll behind it, so ยกเลิก/สร้าง are always reachable.
              -mx-6 -mb-5 cancel the form's px-6 py-5 padding to span full
              width; still inside <form> so the submit button works. */}
          <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
            {createM.error && (
              <p className="mb-3 text-sm text-destructive">
                {plotMutationErrorMessage(createM.error)}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
              <button
                type="submit"
                disabled={isSubmitting || phoneResult.hasErrors}
                title={phoneResult.hasErrors ? 'กรุณาแก้ไขเบอร์โทรที่ไม่ถูกต้องก่อน' : undefined}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                สร้าง
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * Edit Plot (round 8.0.4) — physical plot data ONLY. Planting-cycle plan/
 * yield data is edited via EditCycleModal on Plot Detail instead (see the
 * "จัดการรอบปลูก / แผนผลผลิต" row action).
 */
function EditPlotModal({
  plotId,
  onClose,
  onSaved,
}: {
  plotId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { data: existing, isLoading } = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
  });

  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<EditPlotFormValues>({
    resolver: zodResolver(editPlotSchema),
    values: existing
      ? {
          name: existing.name,
          village: existing.village ?? '',
          district: existing.district ?? '',
          province: existing.province ?? '',
          latitude: toNumberOrNull(existing.latitude) ?? undefined,
          longitude: toNumberOrNull(existing.longitude) ?? undefined,
          rai: toNumberOrNull(existing.rai) ?? undefined,
        }
      : undefined,
  });

  const updateM = useMutation({ mutationFn: (p: PlotUpdatePayload) => updatePlot(plotId, p) });

  async function onSubmit(values: EditPlotFormValues) {
    try {
      await updateM.mutateAsync({
        name: values.name,
        village: values.village || null,
        district: values.district || null,
        province: values.province || null,
        latitude: numberOrNull(values.latitude),
        longitude: numberOrNull(values.longitude),
        rai: numberOrNull(values.rai),
      });
    } catch {
      // Already reflected in updateM.error (rendered below) — same
      // unhandled-rejection guard as CreatePlotModal.onSubmit.
      return;
    }
    onSaved();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <MapPin className="h-4 w-4" />
            แก้ไขข้อมูลแปลง
          </h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
            <div>
              <h3 className="mb-3 text-sm font-semibold text-foreground">ข้อมูลแปลง</h3>
              <Field label="ชื่อแปลง *" error={errors.name?.message}>
                <input {...register('name')} className="field-input" />
              </Field>
            </div>

            <div className="border-t border-border pt-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">ที่ตั้ง / พิกัด / พื้นที่</h3>
              <div className="grid grid-cols-3 gap-4">
                <Field label="หมู่บ้าน/ตำบล" error={errors.village?.message}>
                  <input {...register('village')} className="field-input" />
                </Field>
                <Field label="อำเภอ" error={errors.district?.message}>
                  <input {...register('district')} className="field-input" />
                </Field>
                <Field label="จังหวัด" error={errors.province?.message}>
                  <MasterDataSelect
                    type="province"
                    placeholder="— เลือกจังหวัด —"
                    value={watch('province') || null}
                    onChange={(v) => setValue('province', v ?? '', { shouldDirty: true })}
                  />
                </Field>
              </div>

              <div className="mt-4 grid grid-cols-3 gap-4">
                <Field label="Latitude" error={errors.latitude?.message}>
                  <input {...register('latitude')} type="number" step="0.0000001" className="field-input" placeholder="13.7563" />
                </Field>
                <Field label="Longitude" error={errors.longitude?.message}>
                  <input {...register('longitude')} type="number" step="0.0000001" className="field-input" placeholder="100.5018" />
                </Field>
                <Field label="พื้นที่ (ไร่)" error={errors.rai?.message}>
                  <input {...register('rai')} type="number" step="0.01" className="field-input" placeholder="0.00" />
                </Field>
              </div>
            </div>

            <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
              {updateM.error && (
                <p className="mb-3 text-sm text-destructive">
                  {plotMutationErrorMessage(updateM.error)}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
                <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">
                  {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  บันทึก
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}


function AssignModal({
  plotId,
  onClose,
  onSaved,
}: {
  plotId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { data: plot, isLoading: loadingPlot } = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId),
  });
  const { data: allUsers = [] } = useQuery({
    queryKey: ['users', 0, ''],
    queryFn: () => listUsers({ limit: 200 }),
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Sync the checklist from the loaded plot exactly once — using
  // `selected.size === 0` as the trigger (the previous approach) misfires
  // every time the admin unchecks the last user, silently reverting their
  // "unassign everyone" action back to the plot's original assignees.
  const syncedRef = useRef(false);
  useEffect(() => {
    if (plot && !syncedRef.current) {
      setSelected(new Set(plot.assignedUsers.map((u) => u.userId)));
      syncedRef.current = true;
    }
  }, [plot]);

  const assignM = useMutation({
    mutationFn: (ids: string[]) => assignPlotUsers(plotId, ids),
    onSuccess: onSaved,
  });

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Users className="h-4 w-4" />
            มอบหมาย user ให้แปลง {plot?.plotCode}
          </h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="max-h-80 overflow-y-auto px-6 py-4">
          {loadingPlot ? (
            <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>
          ) : (
            <ul className="space-y-1">
              {(allUsers as UserSummary[]).map((u) => (
                <li key={u.id}>
                  <label className="flex items-center gap-3 rounded px-2 py-1.5 hover:bg-secondary cursor-pointer text-sm">
                    <input
                      type="checkbox"
                      checked={selected.has(u.id)}
                      onChange={() => toggle(u.id)}
                    />
                    <span className="flex-1 truncate">{u.fullName}</span>
                    <span className="text-xs text-muted-foreground truncate max-w-[160px]">{u.email}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex justify-between border-t border-border px-6 py-4">
          <span className="text-sm text-muted-foreground">เลือก {selected.size} คน</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button
              type="button"
              onClick={() => assignM.mutate(Array.from(selected))}
              disabled={assignM.isPending}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {assignM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              บันทึก
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}


function Field({
  label, error, children, className,
}: {
  label: string; error?: string; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={`flex flex-col gap-1 ${className ?? ''}`}>
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
