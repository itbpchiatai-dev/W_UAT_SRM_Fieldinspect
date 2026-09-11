/**
 * PlotDetail — current status + inspection history for one plot (round 15).
 * Route: /farmlog/admin/plots/:plotId
 *
 * Current Status is read verbatim from plots.current_* (via getPlot) — this
 * page never re-derives "latest" by scanning records itself; the backend
 * already keeps that snapshot in sync on every record create (round 12).
 */
import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Archive, ArrowLeft, ChevronDown, ChevronUp, ClipboardCheck, FileText, KeyRound, Loader2, MapPin, Navigation, Pencil, Phone, PowerOff, Printer, RefreshCw, Sprout, Unlock } from 'lucide-react';
import { getPlot, getPlotInspectionAccessCredential, listPlotCycles, plotToQrLabel, type PlotCycle, type PlotDetail as PlotDetailData } from '../../../api/plots';
import { listRecords, getRecord, type RecordSummary } from '../../../api/records';
import { AuthenticatedPhoto } from '../../../components/farmlog/AuthenticatedPhoto';
import { PlotQrPrintSheet, type PlotQrLabelData } from '../../../components/farmlog/PlotQrPrintSheet';
import {
  StartCycleModal, EditCycleModal, CloseCycleModal, CancelCycleModal, RolloverCycleModal, lotSourceBadge,
  ReactivatePlotModal, ReactivatePlotWithCycleModal,
} from '../../../components/farmlog/PlotCycleModals';
import { plotCodeSourceBadge } from '../../../lib/plot-code';
import {
  canReactivateWithCycle,
  canRolloverCycle,
  canStartFirstCycle,
} from '../../../lib/plot-lifecycle';
import { PlotAccessPhoneModal } from '../../../components/farmlog/PlotAccessPhoneModal';
import { PlotInspectionPasswordModal } from '../../../components/farmlog/PlotInspectionPasswordModal';
import { useHasPermission } from '../../../hooks/useHasPermission';
import { useAuth } from '../../../hooks/useAuth';
import { canViewVariety } from '../../../lib/variety-visibility';
import {
  formattedPhoneSnapshot, hasPhoneAttribution, inspectorTypeLabel, phoneTypeLabel, submittedByLine,
} from '../../../lib/inspection-attribution';
import { toNumberOrNull } from '../../../lib/numeric';
import { formatThaiMobile } from '../../../lib/phone';
import {
  getScoreDisplayItems,
  resolveScoreLabels,
} from '../../../lib/inspection-protocol-snapshot';
import {
  computeCurrentExpectedYield,
  describeFinalEstimate,
  describeYieldPlanGap,
  formatYieldFormula,
  formatYieldQuantity,
} from '../../../lib/yield-planning';
import { describeCycleStatus, formatCycleTitle, getActiveCycle, getLatestCycle, recordCycleDisplayName } from '../../../lib/plot-cycle';

/** Round 8-14D — "ประวัติการตรวจ" (inspection records) page size is now
 * user-selectable, defaulting to 5 so the section stays compact on a plot
 * with a long inspection history. Unlike the CYCLE history table below
 * (which slices one already-fetched array client-side), this drives the
 * REAL backend pagination — listRecords' limit/offset — so changing it
 * issues a fresh request. */
const HISTORY_PAGE_SIZE_OPTIONS = [5, 10, 20, 50, 100] as const;
const HISTORY_PAGE_SIZE_DEFAULT = 5;

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-gray-400">{label}</dt>
      <dd className="text-sm font-medium text-gray-800">{value ?? <span className="text-gray-300">—</span>}</dd>
    </div>
  );
}

/** Round B — the plot's code with its source badge (อัตโนมัติ/กรอกเอง/
 * ข้อมูลเดิม), the same vocabulary LotValue uses below so a reader learns it
 * once. A plot created before the generator has no source and shows the code
 * alone — untagged rather than mislabelled as hand-entered. */
function PlotCodeValue({ plotCode, source }: { plotCode: string; source?: string | null }) {
  const badge = plotCodeSourceBadge(source);
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="font-mono">{displayOrDash(plotCode)}</span>
      {badge && (
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}>
          {badge.label}
        </span>
      )}
    </span>
  );
}

/** Round 8-5B — a cycle's Lot No with its source badge (อัตโนมัติ/กรอกเอง/
 * ข้อมูลเดิม). Renders "—" with no badge when the cycle has no lot. */
function LotValue({ lotNo, lotNoSource }: { lotNo: string | null; lotNoSource: PlotCycle['lotNoSource'] }) {
  const badge = lotSourceBadge(lotNoSource, !!lotNo);
  if (!lotNo) return <span className="text-gray-300">—</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span>{lotNo}</span>
      {badge && (
        <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}>
          {badge.label}
        </span>
      )}
    </span>
  );
}

/** "" and whitespace-only count as empty — PlotDetail's identity strings
 * (supplierCode/supplierName/plotCode/name) are typed as plain `string`
 * (never `| null`), with "" as the backend's own "missing" sentinel. */
function displayOrDash(value: string | null | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

/** Round 8-2.6: supplierName is the primary identity value, supplierCode a
 * secondary parenthetical — falls back to whichever one is actually present
 * (Field's own `—` fallback covers the both-empty case). */
function supplierIdentityDisplay(
  name: string | null | undefined,
  code: string | null | undefined,
): string | null {
  const n = displayOrDash(name);
  const c = displayOrDash(code);
  if (n && c) return `${n} (${c})`;
  return n ?? c;
}

function ScoreChip({ label, value }: { label: string; value: number | null }) {
  if (value == null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gray-50 px-2.5 py-1 text-xs text-gray-400">
        {label}: —
      </span>
    );
  }
  const tone = value <= 3 ? 'bg-orange-50 text-orange-700' : 'bg-green-50 text-green-700';
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
      {label}: {value}/10
    </span>
  );
}

/** Compact, label-less score chip for the collapsed history glance — a
 * RecordSummary carries no protocol snapshot, so per-criterion labels can't
 * be resolved here without misleading (a stage may remap what each slot
 * means). The expanded row below shows the properly-labelled breakdown. */
function ScoreDot({ value }: { value: number | null }) {
  if (value == null) return <span className="text-xs text-gray-300">—</span>;
  const tone = value <= 3 ? 'bg-orange-50 text-orange-700' : 'bg-green-50 text-green-700';
  return (
    <span className={`inline-flex min-w-8 justify-center rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
      {value}
    </span>
  );
}

/** Hero Yield Planning card (round 17, hero layout round 18; sourced from
 * the ACTIVE CYCLE round 8.0.4) — plantCount/expectedYieldFull/
 * expectedYieldUnit are PlotCycle-owned (set via Start/Edit cycle), never
 * read from the plot mirror here even though it's kept in sync; Current
 * Yield % stays plot-owned — it's the latest-inspection snapshot
 * (CurrentStatusSection's data source), which has no per-cycle equivalent.
 * Expected Current Yield is the single most prominent number on the page —
 * computed from the two, never stored — see lib/yield-planning.ts. */
function YieldPlanningSection({
  plot,
  activeCycle,
  canUpdate,
  // Round E — the plot's FULL cycle history size (any status). A closed cycle
  // counts: it is what tells "never started" apart from "already finished",
  // which is the only thing the start button may still be offered for.
  cycleCount,
  onStart,
  onEdit,
}: {
  plot: PlotDetailData;
  activeCycle: PlotCycle | null;
  canUpdate: boolean;
  cycleCount: number;
  onStart: () => void;
  onEdit: () => void;
}) {
  const pct = toNumberOrNull(plot.currentYieldPct);

  if (!activeCycle) {
    return (
      <section className="rounded-lg border border-green-200 bg-green-50/40 p-5 shadow-sm">
        <h2 className="mb-4 flex items-center gap-1.5 text-base font-semibold text-gray-800">
          <Sprout className="h-4 w-4 text-green-600" /> แผนผลผลิต (Yield Planning)
        </h2>
        <div className="rounded-md bg-white px-4 py-6 text-center shadow-sm">
          <p className="text-sm font-medium text-gray-600">
            {/* Round 8-6I Part F — an inactive plot must never invite
                StartCycleModal; reactivation (header buttons above) is the
                only path back for it. Distinct wording from the header's
                warning band (not an exact duplicate of "แปลงนี้ปิดใช้งานอยู่"). */}
            {plot.isActive ? 'รอเริ่มรอบปลูก' : 'แปลงปิดใช้งานอยู่ ยังตั้งแผนผลผลิตไม่ได้'}
          </p>
          <p className="mt-1 text-xs text-gray-400">
            {/* Round Q — a plot whose season is over has no plan left to make,
                but it DOES have real harvest figures, which now sit in the
                รอบปลูก card above. Say so: leaving this card blank while the
                one above it is full reads as if the data were missing. */}
            {canStartFirstCycle(cycleCount)
              ? 'ตั้งแผนผลผลิตได้หลังเริ่มรอบปลูก'
              : 'แปลงนี้ปิดรอบปลูกไปแล้ว — ดูผลผลิตจริงได้ที่การ์ด "รอบปลูก" ด้านบน'}
          </p>
          {/* Round E — offered only while the plot has never had a cycle: the
              "reserve the plot now, plan the season later" flow. A plot whose
              cycle is finished is finished; next season is a new plot. */}
          {plot.isActive && canUpdate && canStartFirstCycle(cycleCount) && (
            <button
              type="button"
              onClick={onStart}
              className="mt-3 inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-700"
            >
              <Sprout className="h-4 w-4" /> เริ่มรอบปลูกแรก
            </button>
          )}
        </div>
      </section>
    );
  }

  const gap = describeYieldPlanGap(activeCycle.plantCount, activeCycle.expectedYieldFull);
  const currentExpected = computeCurrentExpectedYield(activeCycle.expectedYieldFull, plot.currentYieldPct);
  const formula = formatYieldFormula(activeCycle.expectedYieldFull, plot.currentYieldPct, activeCycle.expectedYieldUnit);

  return (
    <section className="rounded-lg border border-green-200 bg-green-50/40 p-5 shadow-sm">
      <h2 className="mb-4 flex items-center gap-1.5 text-base font-semibold text-gray-800">
        <Sprout className="h-4 w-4 text-green-600" /> แผนผลผลิต (Yield Planning)
      </h2>

      {gap ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div>
            <p className="font-medium">ต้องตั้งค่าแผนผลผลิต</p>
            <p className="mt-0.5 text-xs text-amber-700">{gap}</p>
            {canUpdate && (
              <button
                type="button"
                onClick={onEdit}
                className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-amber-700"
              >
                <Pencil className="h-3.5 w-3.5" /> แก้รอบปลูก
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="mb-4 rounded-md bg-white px-4 py-5 text-center shadow-sm">
          {/* Round 8-8C — explicit "ประมาณการ" (estimated) so this computed
              hero number is never mistaken for an actual/harvested figure
              (those live in the cycle-history table's own "ผลผลิตตอนเก็บเกี่ยว"
              / "ผลผลิตจริงหลังทำความสะอาด" columns). */}
          <p className="text-xs font-medium uppercase tracking-wide text-gray-400">ผลผลิตประมาณการปัจจุบัน</p>
          {currentExpected != null ? (
            <>
              <p className="mt-1 text-4xl font-extrabold text-green-700">
                {formatYieldQuantity(currentExpected, activeCycle.expectedYieldUnit)}
              </p>
              {formula && <p className="mt-1.5 text-xs text-gray-500">{formula}</p>}
            </>
          ) : (
            <>
              <p className="mt-1 text-3xl font-bold text-gray-300">—</p>
              <p className="mt-1.5 text-xs text-gray-400">รอข้อมูลจากการตรวจแปลงครั้งแรก</p>
            </>
          )}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-t border-green-200 pt-3 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-gray-400">จำนวนต้น/จำนวนปลูก</dt>
          <dd className="text-sm font-medium text-gray-800">
            {activeCycle.plantCount != null && activeCycle.plantCount > 0
              ? activeCycle.plantCount.toLocaleString('th-TH')
              : <span className="text-gray-300">—</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-gray-400">เป้าผลิต</dt>
          <dd className="text-sm font-medium text-gray-800">
            {formatYieldQuantity(activeCycle.expectedYieldFull, activeCycle.expectedYieldUnit) ?? <span className="text-gray-300">—</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-gray-400">เปอร์เซ็นต์เทียบเป้าผลิต</dt>
          <dd className="text-sm font-medium text-gray-800">
            {pct != null ? `${pct}%` : <span className="text-gray-300">—</span>}
          </dd>
        </div>
      </dl>
    </section>
  );
}

/** Current planting cycle card (round 7.3) — the plot's ACTIVE cycle is the
 * source of truth for crop/variety/lot/planting-date/plant-count/expected
 * yield; currentYieldPct (and the estimated current yield derived from it)
 * still comes from the plot's inspection-derived snapshot, same source as
 * CurrentStatusSection/YieldPlanningSection above — a cycle doesn't carry
 * its own yield-% snapshot. */
/** The plot's season, in one readable card (round Q).
 *
 * It used to read `activeCycle` and therefore emptied itself the moment the
 * cycle closed — which round P made the normal end state, not an edge case.
 * Everything the season contained (crop, lot, PO, Oracle refs, and the harvest
 * figures) then survived only in the 18-column scrolling history table below,
 * so a finished plot's detail page showed nothing about what was grown.
 *
 * It now reads the LATEST cycle whatever its status, and gains the six
 * close/harvest fields that only exist once a cycle is closed. With that, the
 * card carries everything the history table did — which is why the table is
 * gone: under "one plot, one cycle" (round E) it was a one-row table with a
 * horizontal scrollbar and a "how many rounds to show" selector.
 *
 * The WRITE actions stay gated on the cycle actually being active: a closed
 * season is a record, not something to edit or close again. */
function CurrentCycleSection({
  plot,
  cycle,
  canUpdate,
  // Round E — see YieldPlanningSection above.
  cycleCount,
  cyclesLoading,
  onStart,
  onEdit,
  onCloseCycle,
  onCancelCycle,
  onRollover,
  canSeeVariety,
  canReadRecords,
  canCancelCycle,
}: {
  plot: PlotDetailData;
  cycle: PlotCycle | null;
  canUpdate: boolean;
  canReadRecords: boolean;
  cycleCount: number;
  cyclesLoading: boolean;
  onStart: () => void;
  onEdit: () => void;
  onCloseCycle: () => void;
  onCancelCycle: () => void;
  onRollover: () => void;
  canSeeVariety: boolean;
  canCancelCycle: boolean;
}) {
  if (!cycle) {
    return (
      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 flex items-center gap-1.5 text-base font-semibold text-gray-800">
          <Sprout className="h-4 w-4 text-gray-400" /> รอบปลูก
        </h2>
        <div className="rounded-md bg-gray-50 px-4 py-6 text-center">
          <p className="text-sm font-medium text-gray-600">รอเริ่มรอบปลูก</p>
          <p className="mt-1 text-xs text-gray-400">
            {/* Round 8-6I Part F — this subtitle previously always claimed
                the plot was active even when it wasn't; the warning band
                above the header already says so, but this line must not
                contradict it. */}
            {/* Must not be an EXACT duplicate of the header warning band's
                own "แปลงนี้ปิดใช้งานอยู่" — the two are read together, and an
                identical string reads as a stutter (and makes either one
                ambiguous to query). Same rule round 8-6I Part F set here. */}
            {!plot.isActive
              ? 'แปลงนี้ปิดใช้งานอยู่ — เปิดใช้งานแปลงก่อนจึงจะจัดการรอบปลูกได้'
              : canStartFirstCycle(cycleCount)
                ? 'แปลงนี้ยังใช้งานอยู่ แต่ยังไม่มีรอบปลูกที่เปิดอยู่'
                : 'รอบปลูกของแปลงนี้ปิดแล้ว — 1 แปลง = 1 รอบปลูก ฤดูถัดไปให้สร้างแปลงใหม่'}
          </p>
          {/* Round E — see the yield-planning card above for why this is gated
              on "has never had a cycle" rather than on plot.isActive alone. */}
          {plot.isActive && canUpdate && canStartFirstCycle(cycleCount) && (
            <button
              type="button"
              onClick={onStart}
              className="mt-3 inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-700"
            >
              <Sprout className="h-4 w-4" /> เริ่มรอบปลูกแรก
            </button>
          )}
        </div>
      </section>
    );
  }

  const growing = cycle.status === 'active';
  const pct = toNumberOrNull(plot.currentYieldPct);
  const currentExpected = computeCurrentExpectedYield(cycle.expectedYieldFull, plot.currentYieldPct);

  return (
    // A closed season is a record, not live work — the card drops the green
    // "in progress" tint for it so the two read differently at a glance.
    <section className={`rounded-lg border p-5 shadow-sm ${
      growing ? 'border-green-200 bg-green-50/40' : 'border-gray-200 bg-white'
    }`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-semibold text-gray-800">
          {/* "ปัจจุบัน" was dropped in round Q: this card now also shows a
              season that is over, and the badge beside it already says which. */}
          <Sprout className={`h-4 w-4 ${growing ? 'text-green-600' : 'text-gray-400'}`} /> รอบปลูก
        </h2>
        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${CYCLE_BADGE_TONE[cycle.status]}`}>
          {describeCycleStatus(cycle.status)}
        </span>
      </div>
      <p className="mb-3 text-sm font-semibold text-gray-800">{formatCycleTitle(cycle)}</p>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
        <Field label="ชนิดพืช" value={cycle.crop} />
        {canSeeVariety && <Field label="พันธุ์/สายพันธุ์" value={cycle.variety} />}
        <Field label="PO Number" value={cycle.poNumber} />
        <Field label="P.Code" value={cycle.pCode} />
        <Field label="Lot No ระบบ"
          value={<LotValue lotNo={cycle.lotNo} lotNoSource={cycle.lotNoSource} />} />
        {/* Round 8-12B — the SUPPLIER's own lot, shown as its own field with no
            source badge: it is not derived by the system, so "อัตโนมัติ/กรอกเอง"
            would be meaningless for it. */}
        <Field label="Supplier Lot No" value={cycle.supplierLotNo} />
        {/* Round 8-21B — three independent, OPTIONAL back-office reference
            fields, cycle-scoped (never a Plot-level/permanent field) — read
            straight off THIS active cycle, never any other. Field already
            renders "—" for null. */}
        <Field label="Oracle Supplier Code" value={cycle.oracleSupplierCode} />
        <Field label="Oracle Invoice" value={cycle.oracleInvoice} />
        <Field label="Ref Account" value={cycle.refAccount} />
        <Field label="วันที่ปลูก" value={cycle.plantingDate} />
        <Field
          label="จำนวนต้น/จำนวนปลูก"
          value={cycle.plantCount != null ? cycle.plantCount.toLocaleString('th-TH') : null}
        />
        <Field
          label="เป้าผลิต"
          value={formatYieldQuantity(cycle.expectedYieldFull, cycle.expectedYieldUnit)}
        />
        {/* Round Q — the live tracking pair only means something while the
            season is running: closing a cycle clears the plot's inspection
            snapshot (plot_cycle_repository.clear_plot_cycle_mirror_and_
            inspection_snapshot), so on a closed cycle both would read a
            permanent "—" beside the real harvest figures below. */}
        {growing && (
          <>
            <Field label="เปอร์เซ็นต์เทียบเป้าผลิต" value={pct != null ? `${pct}%` : null} />
            <Field
              label="ผลผลิตที่คาดว่าจะได้"
              value={currentExpected != null ? formatYieldQuantity(currentExpected, cycle.expectedYieldUnit) : null}
            />
          </>
        )}
      </dl>

      {/* Round Q — how the season ENDED. These six lived only in the history
          table until now, which is why a closed plot's page showed no harvest
          at all. Rendered only once the cycle is closed: on a live one every
          value is null by definition, and six dashes would be noise. */}
      {!growing && (
        <div className="mt-4 border-t border-gray-200 pt-4">
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-gray-500">
            ผลการปิดรอบ
          </p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <Field label="วันที่เริ่ม" value={thaiDate(cycle.startedAt)} />
            <Field label="วันที่ปิดรอบ" value={thaiDate(cycle.closedAt)} />
            <Field label="วันที่เก็บเกี่ยว" value={closedOnly(cycle, cycle.harvestDate)} />
            <Field label="ประมาณการสุดท้าย" value={<FinalEstimateCell cycle={cycle} />} />
            <Field
              label="ผลผลิตตอนเก็บเกี่ยว"
              value={closedOnly(cycle, formatYieldQuantity(cycle.harvestYield, cycle.finalYieldUnit))}
            />
            <Field
              label="ผลผลิตจริงหลังทำความสะอาด"
              value={closedOnly(cycle, formatYieldQuantity(cycle.finalYieldAfterClean, cycle.finalYieldUnit))}
            />
          </dl>
          {/* Free text, so it gets a full-width row of its own rather than a
              grid cell it would overflow. */}
          {cycle.closeReason?.trim() && (
            <p className="mt-3 text-xs text-gray-600">
              <span className="font-medium text-gray-500">เหตุผล/หมายเหตุ:</span>{' '}
              {cycle.closeReason}
            </p>
          )}
          {/* The history table's last column ("อ้างอิง"): the final_plot note
              and the inspection record the final estimate was taken from,
              permission-gated exactly as it was there. Kept, not dropped —
              it is the audit trail for the numbers just above it. */}
          <div className="mt-3 text-xs">
            <CycleReferenceCell cycle={cycle} canReadRecords={canReadRecords} />
          </div>
        </div>
      )}

      {/* Round Q — write actions are for a LIVE season only. A closed one is
          history: editing or re-closing it are not states the backend allows
          either (409 "Only active planting cycle can be closed").
          Round S — the row appears for EITHER privilege: a Supplier Owner has
          only ยกเลิก, an admin has all three. */}
      {(canUpdate || canCancelCycle) && growing && (
        <div className="mt-4 flex flex-wrap gap-2 border-t border-green-200 pt-3">
          {canUpdate && (
          <button
            type="button"
            onClick={onEdit}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary"
          >
            <Pencil className="h-3.5 w-3.5" /> แก้รอบปลูก
          </button>
          )}
          {canUpdate && (
          <button
            type="button"
            onClick={onCloseCycle}
            className="inline-flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 shadow-sm hover:bg-amber-100"
          >
            <Archive className="h-3.5 w-3.5" /> ปิดรอบปลูก (เก็บเกี่ยว)
          </button>
          )}
          {/* Round S — the one ending a Supplier may record. Styled as the
              destructive action it is, and separated from the harvest close so
              the two can never be picked by accident for one another. */}
          {canCancelCycle && (
          <button
            type="button"
            onClick={onCancelCycle}
            className="inline-flex items-center gap-2 rounded-md border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-medium text-red-800 shadow-sm hover:bg-red-100"
          >
            <PowerOff className="h-3.5 w-3.5" /> ยกเลิกรอบปลูก
          </button>
          )}
          {/* Rollover (round 7.9C) — one atomic close+start action, distinct
              from ปิดใช้งานแปลง (permanent plot closure) below it in the
              header actions. Only offered on a still-open plot, once cycles
              have finished loading (avoids a flash before the active cycle is
              known). */}
          {/* Round E — rollover exists only to start a SECOND cycle on the
              same plot, so the policy leaves it no valid state. Closing a
              cycle on its own is unaffected, and that is where the season's
              actual harvest is now recorded (round D). */}
          {canRolloverCycle() && plot.isActive && !cyclesLoading && (
            <button
              type="button"
              onClick={onRollover}
              className="inline-flex items-center gap-2 rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-800 shadow-sm hover:bg-blue-100"
            >
              <RefreshCw className="h-3.5 w-3.5" /> จบรอบ + เริ่มรอบใหม่
            </button>
          )}
        </div>
      )}
    </section>
  );
}

const CYCLE_BADGE_TONE: Record<PlotCycle['status'], string> = {
  active: 'bg-green-100 text-green-700',
  harvested: 'bg-blue-100 text-blue-700',
  cancelled: 'bg-gray-100 text-gray-500',
};

/** Round 8-10A — how many cycles the page fetches in one go. The selector below
 * can never show more than this, and the summary line says so rather than
 * claiming the plot has exactly this many. */
export const CYCLE_HISTORY_MAX = 100;

const dash = <span className="text-gray-300">—</span>;

function thaiDate(value: string | null): React.ReactNode {
  if (!value) return dash;
  return new Date(value).toLocaleDateString('th-TH');
}

/** Actual-harvest fields belong to a CLOSED cycle. An active row reports a dash
 * even if the column somehow carries a value — the same refusal the previous
 * ActualHarvestBlock made by not rendering at all for an active cycle. A
 * growing cycle has not been harvested, so there is nothing true to show. */
function closedOnly<T>(cycle: PlotCycle, value: T): T | null {
  return cycle.status === 'active' ? null : value;
}

/** The final ESTIMATE for one row, read verbatim through the shared helper —
 * never recomputed here (round 8-2.8B rule, unchanged). An active cycle has no
 * final snapshot at all, and a legacy closed cycle that never got one shows the
 * helper's own "none" message rather than a fabricated number. */
function FinalEstimateCell({ cycle }: { cycle: PlotCycle }) {
  const final = describeFinalEstimate({
    cycleStatus: cycle.status,
    finalEstimatedYield: cycle.finalEstimatedYield,
    finalYieldPct: cycle.finalYieldPct,
    expectedYieldUnit: cycle.expectedYieldUnit,
  });
  if (final.kind === 'active') return dash;
  if (final.kind === 'none') return <span className="text-gray-400">{final.message}</span>;
  return <span className="font-semibold text-green-700">{final.text}</span>;
}

/** closeReason / finalNote / the summarising record link for one row. All three
 * are optional and independent — a cycle may have any combination.
 *
 * finalNote and finalInspectionRecordId are final_plot artefacts of a CLOSED
 * cycle, so an active row shows neither even if the fields somehow carry
 * values — the same rule the previous ActualHarvestBlock enforced by refusing
 * to render for an active cycle at all. */
function CycleReferenceCell({
  cycle, canReadRecords,
}: { cycle: PlotCycle; canReadRecords: boolean }) {
  const closed = cycle.status !== 'active';
  const finalNote = closed ? cycle.finalNote : null;
  const recordId = closed ? cycle.finalInspectionRecordId : null;
  const hasAnything = cycle.closeReason || finalNote || recordId;
  if (!hasAnything) return dash;
  return (
    <div className="space-y-1">
      {cycle.closeReason && <p className="text-gray-600">เหตุผล: {cycle.closeReason}</p>}
      {finalNote && <p className="text-gray-600">หมายเหตุ: {finalNote}</p>}
      {recordId && (
        canReadRecords ? (
          <Link
            to={`/farmlog/records/${recordId}/preview`}
            className="inline-flex items-center gap-1 text-blue-600 hover:underline"
          >
            <FileText className="h-3 w-3" /> บันทึกที่ใช้สรุป
          </Link>
        ) : (
          // No permission → no link and NO id: a UUID is still a pointer at a
          // record this caller may not read.
          <p className="text-gray-400">บันทึกที่ใช้สรุป: มี</p>
        )
      )}
    </div>
  );
}


// Round Q — CycleHistorySection was deleted here.
//
// Under "one plot, one cycle" (round E) it was a one-row table with 18
// columns, a horizontal scrollbar and a "how many rounds to show" selector,
// and after round P (a close also retires the plot) it was the ONLY place
// the finished season's data appeared at all. Both problems are solved by
// the same move: everything it showed now lives in CurrentCycleSection
// above, as a plain label/value list that fits on screen.
//
// Removed rather than hidden behind `cycles.length > 1`: neither UAT (zero
// plot_cycles rows) nor production has a plot with a second cycle — the
// multi-cycle rows on the dev database are test data from before the
// policy. Git history holds the table if the policy is ever reversed.

function CurrentStatusSection({
  lastInspectionRecordId,
  fields,
  canSeeVariety,
}: {
  lastInspectionRecordId: string | null;
  canSeeVariety: boolean;
  fields: {
    crop: string | null;
    variety: string | null;
    plantingDate: string | null;
    stage: string | null;
    yieldPct: string | null;
    fieldPrepScore: number | null;
    weatherScore: number | null;
    careScore: number | null;
    varietyResistanceScore: number | null;
    gpsLat: string | null;
    gpsLng: string | null;
    lastInspectedAt: string | null;
    lastInspectedByCode: string | null;
  };
}) {
  const hasInspection = lastInspectionRecordId != null;

  const { data: latestRecord } = useQuery({
    queryKey: ['record', lastInspectionRecordId],
    queryFn: () => getRecord(lastInspectionRecordId!),
    enabled: hasInspection,
  });

  // Score VALUES stay verbatim from plot.current_* (this page never
  // re-derives them); only the LABELS come from the latest record's frozen
  // protocol snapshot (round 5.3). Falls back safely when the record hasn't
  // loaded, the user lacks records.read, or it's an old record with no
  // snapshot — resolveScoreLabels(undefined→{}) just returns the defaults.
  const scoreLabels = resolveScoreLabels(latestRecord ?? {});

  // Round 8-8C — the latest record's OWN kg snapshot, never recomputed from
  // the active cycle. Falls back to the plot's synced currentYieldPct
  // (fields.yieldPct) when latestRecord hasn't loaded yet (still fetching)
  // or the caller lacks records.read — the pct must still render either way.
  const quantityKg = toNumberOrNull(latestRecord?.yieldQuantityKg);
  const pctDisplay = latestRecord?.yieldPct != null
    ? parseFloat(latestRecord.yieldPct)
    : toNumberOrNull(fields.yieldPct);

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-base font-semibold text-gray-800">สถานะล่าสุด</h2>

      {!hasInspection ? (
        <p className="rounded-md bg-gray-50 px-3 py-6 text-center text-sm text-gray-400">
          ยังไม่มีการตรวจแปลงนี้
        </p>
      ) : (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
            <Field label="ชนิดพืช" value={fields.crop} />
            {canSeeVariety && <Field label="พันธุ์/สายพันธุ์" value={fields.variety} />}
            <Field label="วันที่ปลูก" value={fields.plantingDate} />
            <Field label="ระยะการเจริญเติบโต" value={fields.stage} />
            {quantityKg != null ? (
              <>
                <Field label="ผลผลิตประเมินล่าสุด" value={formatYieldQuantity(quantityKg, 'kg')} />
                <Field label="Yield % (เทียบเป้าผลิต)" value={pctDisplay != null ? `${pctDisplay}%` : null} />
              </>
            ) : (
              <Field
                label="Yield ล่าสุด"
                value={pctDisplay != null ? `${pctDisplay}%` : null}
              />
            )}
            <Field
              label="GPS ล่าสุด"
              value={
                fields.gpsLat != null && fields.gpsLng != null ? (
                  <span className="inline-flex items-center gap-1 font-mono text-xs">
                    <Navigation className="h-3 w-3 text-green-600" />
                    {parseFloat(fields.gpsLat).toFixed(6)}, {parseFloat(fields.gpsLng).toFixed(6)}
                  </span>
                ) : null
              }
            />
            <Field
              label="ตรวจล่าสุดเมื่อ"
              value={fields.lastInspectedAt ? new Date(fields.lastInspectedAt).toLocaleString('th-TH') : null}
            />
            <Field label="ผู้ตรวจ (รหัส)" value={fields.lastInspectedByCode} />
          </dl>

          <div className="flex flex-wrap gap-2">
            <ScoreChip label={scoreLabels.fieldPrepScore} value={fields.fieldPrepScore} />
            <ScoreChip label={scoreLabels.weatherScore} value={fields.weatherScore} />
            <ScoreChip label={scoreLabels.careScore} value={fields.careScore} />
            <ScoreChip label={scoreLabels.varietyResistanceScore} value={fields.varietyResistanceScore} />
          </div>

          {lastInspectionRecordId && (
            <Link
              to={`/farmlog/records/${lastInspectionRecordId}/preview`}
              className="inline-block text-xs font-medium text-green-700 hover:underline"
            >
              ดูบันทึกการตรวจล่าสุดแบบเต็ม →
            </Link>
          )}

          {latestRecord && latestRecord.photoUrls.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-gray-500">ภาพถ่ายล่าสุด ({latestRecord.photoUrls.length})</p>
              <div className="flex flex-wrap gap-2">
                {latestRecord.photoUrls.map((url, i) => (
                  <AuthenticatedPhoto key={`${latestRecord.id}-${i}`} recordId={latestRecord.id} photoUrl={url} alt={`ภาพล่าสุด ${i + 1}`} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** Round 8-8C — collapsed history glance: a kg-first record shows the kg
 * amount with its percent in parentheses (same cell/area, no layout growth);
 * a legacy record (percent only) falls back to the original percent-only
 * display; renders nothing when there's no yield data at all. */
function HistoryYieldGlance({ record }: { record: RecordSummary }) {
  const quantityKg = toNumberOrNull(record.yieldQuantityKg);
  const pct = record.yieldPct != null ? parseFloat(record.yieldPct) : null;
  if (quantityKg != null) {
    return (
      <span className="font-semibold text-green-700">
        {formatYieldQuantity(quantityKg, 'kg')}
        {pct != null && <span className="ml-1 font-normal text-gray-400">({pct}%)</span>}
      </span>
    );
  }
  if (pct != null) {
    return <span className="font-semibold text-green-700">{pct}%</span>;
  }
  return null;
}

function HistoryRow({ record }: { record: RecordSummary }) {
  const [expanded, setExpanded] = useState(false);

  const { data: full, isLoading: loadingFull } = useQuery({
    queryKey: ['record', record.id],
    queryFn: () => getRecord(record.id),
    enabled: expanded,
  });

  return (
    <li className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className="flex w-full flex-wrap items-center justify-between gap-2 text-left"
      >
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold text-gray-800">{record.recordDate}</p>
            {/* Which planting cycle this record belongs to (round 7.4) — bound
                at create time, so a multi-cycle plot's history no longer reads
                as if every record is the current cycle. Leads with the
                cycle's own cycleLabel when set (round 8.0.5), falling back to
                "รอบที่ N" then the generic "รอบปลูก". */}
            {(record.cycleLabel != null || record.cycleNo != null) && (
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                {recordCycleDisplayName(record)}
              </span>
            )}
          </div>
          {submittedByLine(record) && (
            <p className="text-xs text-gray-500">
              ผู้กรอก: {submittedByLine(record)}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-600">
          <span>{record.crop ?? '—'}{record.growthStage ? ` · ${record.growthStage}` : ''}</span>
          <HistoryYieldGlance record={record} />
          {expanded ? <ChevronUp className="h-4 w-4 text-gray-400" /> : <ChevronDown className="h-4 w-4 text-gray-400" />}
        </div>
      </button>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-gray-400">คะแนน 4 ด้าน:</span>
        <ScoreDot value={record.fieldPrepScore} />
        <ScoreDot value={record.weatherScore} />
        <ScoreDot value={record.careScore} />
        <ScoreDot value={record.varietyResistanceScore} />
      </div>

      {expanded && (
        <div className="mt-3 space-y-2 border-t border-gray-100 pt-3 text-sm">
          {loadingFull || !full ? (
            <p className="text-xs text-gray-400">กำลังโหลดรายละเอียด...</p>
          ) : (
            <>
              {/* Full labelled score breakdown from the record's frozen
                  protocol snapshot (round 5.3) — the collapsed row above only
                  shows the bare numbers. */}
              <div className="flex flex-wrap gap-1.5">
                {getScoreDisplayItems(full).map((item) => (
                  <ScoreChip key={item.slot} label={item.label} value={item.score} />
                ))}
              </div>
              {/* Round 8-8C — this record's OWN frozen kg snapshot (never the
                  plot's active cycle) — quantity/target/percent read verbatim
                  off `full`, same source RecordPreview uses. A legacy record
                  (yieldQuantityKg null) falls back to percent-only, same as
                  the collapsed glance above. */}
              {(toNumberOrNull(full.yieldQuantityKg) != null || full.yieldPct != null) && (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {toNumberOrNull(full.yieldQuantityKg) != null && (
                    <p><span className="text-xs text-gray-400">ผลผลิตที่คาดว่าจะได้: </span>{formatYieldQuantity(full.yieldQuantityKg, 'kg')}</p>
                  )}
                  {toNumberOrNull(full.yieldTargetKgSnapshot) != null && (
                    <p><span className="text-xs text-gray-400">เป้าผลิตที่ใช้คำนวณ: </span>{formatYieldQuantity(full.yieldTargetKgSnapshot, 'kg')}</p>
                  )}
                  {full.yieldPct != null && (
                    <p>
                      <span className="text-xs text-gray-400">เปอร์เซ็นต์เทียบเป้าผลิต: </span>
                      <span className="font-semibold text-green-700">{parseFloat(full.yieldPct)}%</span>
                    </p>
                  )}
                </div>
              )}
              <p>
                <span className="text-xs text-gray-400">ข้อมูลการเข้าตรวจ: </span>
                {hasPhoneAttribution(full) ? (
                  <>
                    {full.submittedPhoneSnapshot && (
                      <span>
                        {formattedPhoneSnapshot(full.submittedPhoneSnapshot)}
                        {phoneTypeLabel(full.submittedPhoneType) && ` (${phoneTypeLabel(full.submittedPhoneType)})`}
                      </span>
                    )}
                    {inspectorTypeLabel(full.inspectorType) && (
                      <span className="ml-1.5">· เข้าตรวจในฐานะ {inspectorTypeLabel(full.inspectorType)}</span>
                    )}
                  </>
                ) : (
                  <span className="italic text-gray-400">ผู้ใช้ในระบบ / ข้อมูลเดิม</span>
                )}
              </p>
              {full.recommendation && (
                <p><span className="text-xs text-gray-400">คำแนะนำ: </span>{full.recommendation}</p>
              )}
              {full.notes && (
                <p><span className="text-xs text-gray-400">หมายเหตุ: </span>{full.notes}</p>
              )}
              {full.photoUrls.length > 0 && (
                <div className="flex flex-wrap gap-2 pt-1">
                  {full.photoUrls.map((url, i) => (
                    <AuthenticatedPhoto
                      key={`${full.id}-${i}`}
                      recordId={full.id}
                      photoUrl={url}
                      alt={`ภาพ ${i + 1}`}
                      className="h-16 w-16 rounded-md object-cover shadow-sm"
                    />
                  ))}
                </div>
              )}
              <Link
                to={`/farmlog/records/${record.id}/preview`}
                className="inline-block pt-1 text-xs font-medium text-green-700 hover:underline"
              >
                One Page Preview →
              </Link>
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** "เบอร์โทรสำหรับเข้าตรวจแปลง" (round 8-3C) — a distinct section from Plot/
 * access identity, deliberately separate from PlotCycle/Yield (above) and from
 * assigned-users (not shown on this page at all, per round 8.0). Shows the
 * FULL formatted number for every active phone (never truncated here — that's
 * the Plots list's job); management lives behind PlotAccessPhoneModal, gated
 * by plots.update. A caller without plots.update still sees this data (the
 * whole page already requires plots.read) but gets no edit button. */
function AccessPhoneSection({
  plot,
  canUpdate,
  onManage,
}: {
  plot: PlotDetailData;
  canUpdate: boolean;
  onManage: () => void;
}) {
  const hasAny = plot.primaryPhone != null || plot.additionalPhones.length > 0;
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-semibold text-gray-800">
          <Phone className="h-4 w-4 text-gray-400" /> เบอร์โทรสำหรับเข้าตรวจแปลง
        </h2>
        {canUpdate && (
          <button
            type="button"
            onClick={onManage}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary"
          >
            <Pencil className="h-3.5 w-3.5" /> จัดการเบอร์เข้าตรวจ
          </button>
        )}
      </div>

      {!hasAny ? (
        <p className="rounded-md bg-gray-50 px-3 py-6 text-center text-sm text-gray-400">
          ยังไม่ได้ตั้งเบอร์สำหรับเข้าตรวจ
        </p>
      ) : (
        <ul className="space-y-2">
          {plot.primaryPhone != null && (
            <li className="flex items-center gap-2 text-sm">
              <span className="inline-flex shrink-0 items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                เบอร์หลัก
              </span>
              <span className="font-mono font-medium text-gray-800">{formatThaiMobile(plot.primaryPhone)}</span>
            </li>
          )}
          {plot.additionalPhones.map((phone) => (
            <li key={phone} className="flex items-center gap-2 text-sm">
              <span className="inline-flex shrink-0 items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                เบอร์เสริม
              </span>
              <span className="font-mono font-medium text-gray-800">{formatThaiMobile(phone)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** "รหัสยืนยันแปลง" (round 8-9B) — sits next to the access-phone section
 * because the two are halves of one credential: from round 8-9C the public
 * flow will ask for the phone number AND this password together.
 *
 * Owns its own query (the plot read model deliberately carries no credential
 * data — the backend never eager-loads it). Shows STATUS only; there is no way
 * to view an existing password, by design. A GET failure degrades to an inline
 * retry so the rest of Plot Detail keeps working. */
function InspectionCredentialSection({
  plotId,
  canUpdate,
  onManage,
}: {
  plotId: string;
  canUpdate: boolean;
  onManage: (configured: boolean) => void;
}) {
  const { data, isLoading, isError, refetch, isRefetching } = useQuery({
    // Keyed by plotId alone — a password must never appear in a cache key.
    queryKey: ['plot-inspection-credential', plotId],
    queryFn: () => getPlotInspectionAccessCredential(plotId),
    enabled: !!plotId,
  });

  const configured = data?.configured === true;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-base font-semibold text-gray-800">
          <KeyRound className="h-4 w-4 text-gray-400" /> รหัส Supplier ตรวจแปลง
        </h2>
        {/* The button waits for the query: its label and the modal's warning
            both depend on whether a password already exists. */}
        {canUpdate && !isLoading && !isError && (
          <button
            type="button"
            onClick={() => onManage(configured)}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary"
          >
            <Pencil className="h-3.5 w-3.5" />
            {configured ? 'เปลี่ยนรหัส Supplier ตรวจแปลง' : 'ตั้งรหัส Supplier ตรวจแปลง'}
          </button>
        )}
      </div>

      {isLoading ? (
        // Fixed-height placeholder — never flashes "ยังไม่ตั้งรหัส" first,
        // which would read as a real (and alarming) answer.
        <div className="flex h-[60px] items-center gap-2 rounded-md bg-gray-50 px-3 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" /> กำลังโหลดสถานะรหัส Supplier ตรวจแปลง…
        </div>
      ) : isError ? (
        <div className="flex h-[60px] flex-wrap items-center justify-between gap-2 rounded-md bg-red-50 px-3 text-sm">
          <span className="text-red-700">โหลดสถานะรหัส Supplier ตรวจแปลงไม่สำเร็จ</span>
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isRefetching}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-secondary disabled:opacity-60"
          >
            {isRefetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            ลองใหม่
          </button>
        </div>
      ) : configured ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex shrink-0 items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
            ตั้งรหัสแล้ว
          </span>
          {data?.updatedAt && (
            <span className="text-sm text-gray-500">
              แก้ไขล่าสุด {new Date(data.updatedAt).toLocaleString('th-TH')}
            </span>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex shrink-0 items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
            ยังไม่ตั้งรหัส
          </span>
          <span className="text-sm text-gray-500">
            ต้องตั้งรหัสก่อนเปิดใช้การค้นหาแปลงด้วยหมายเลขและรหัส
          </span>
        </div>
      )}
    </section>
  );
}

export function PlotDetail() {
  const { plotId } = useParams<{ plotId: string }>();
  const qc = useQueryClient();
  const canReadRecords = useHasPermission('records.read');
  const canCreateRecords = useHasPermission('records.create');
  const canUpdatePlot = useHasPermission('plots.update');
  // Round 8-6I Part F — activation privilege, same permission the backend's
  // deactivate/reactivate endpoints require (plots.delete).
  const canReactivate = useHasPermission('plots.delete');
  // Round S — ending a season as a FAILURE. A Supplier Owner holds this and
  // not plots.update: they know when a planting fails and should not wait on
  // Chiatai to record it, but closing as HARVESTED is a claim about a
  // delivered crop and stays Chiatai's.
  const canCancelCycle = useHasPermission('plots.cancel_cycle');
  // Round 8-25O — พันธุ์/สายพันธุ์ is Chiatai-internal-only.
  const { user } = useAuth();
  const canSeeVariety = canViewVariety(user?.roles);
  const [page, setPage] = useState(0);
  const [historyPageSize, setHistoryPageSize] = useState<number>(HISTORY_PAGE_SIZE_DEFAULT);
  const [printItems, setPrintItems] = useState<PlotQrLabelData[] | null>(null);
  const [startingCycle, setStartingCycle] = useState(false);
  const [editingCycle, setEditingCycle] = useState<PlotCycle | null>(null);
  const [closingCycle, setClosingCycle] = useState<PlotCycle | null>(null);
  const [cancellingCycle, setCancellingCycle] = useState<PlotCycle | null>(null);
  const [rollingOverCycle, setRollingOverCycle] = useState<PlotCycle | null>(null);
  const [managingPhones, setManagingPhones] = useState(false);
  // null = modal closed. The boolean is the credential's CONFIGURED state at
  // the moment the button was clicked — it drives the modal's title and its
  // "existing inspectors lose access" warning. No password is ever held here.
  const [managingCredential, setManagingCredential] = useState<boolean | null>(null);
  const [reactivatingOnly, setReactivatingOnly] = useState(false);
  const [reactivatingWithCycle, setReactivatingWithCycle] = useState(false);
  const [reactivateSuccessMessage, setReactivateSuccessMessage] = useState<string | null>(null);

  const { data: plot, isLoading, isError } = useQuery({
    queryKey: ['plot', plotId],
    queryFn: () => getPlot(plotId!),
    enabled: !!plotId,
  });

  const { data: cycles = [], isLoading: cyclesLoading } = useQuery({
    // Round 8-10A — fetch the newest 100 cycles ONCE. The history table's
    // 10/25/50/100 selector then slices this array client-side, so changing it
    // never refetches, never remounts the current-cycle section, and never
    // makes the active-cycle actions flicker. Key deliberately unchanged so
    // every existing lifecycle invalidation keeps working.
    queryKey: ['plot-cycles', plotId],
    queryFn: () => listPlotCycles(plotId!, { limit: CYCLE_HISTORY_MAX, offset: 0 }),
    enabled: !!plotId,
    staleTime: 60 * 1000,
  });
  const activeCycle = getActiveCycle(cycles);
  const latestCycle = getLatestCycle(cycles);

  const { data: history = [], isLoading: historyLoading, isError: historyIsError } = useQuery({
    // historyPageSize is part of the key (round 8-14D) — two different page
    // sizes are genuinely different result sets, so they must never share a
    // cache entry (a cached 5-row page would otherwise be served for a
    // 50-row request until it went stale).
    queryKey: ['records', 'byPlot', plotId, page, historyPageSize],
    queryFn: () => listRecords({ plotId, limit: historyPageSize, offset: page * historyPageSize }),
    enabled: !!plotId && canReadRecords,
  });

  /** Changing the page size invalidates the current page number: page 3 of
   * 5-row pages isn't page 3 of 50-row pages, and offset would point into
   * the middle of nowhere. Always go back to the first page. */
  function handleHistoryPageSizeChange(size: number) {
    setHistoryPageSize(size);
    setPage(0);
  }

  // Round 7.3 — refresh everything a cycle lifecycle change can affect: this
  // plot's detail (mirror fields), its cycle list/history, the Plots list
  // (cycle-status badge/yield wording), and the Plot Status report. Round
  // 8-2.8B: a close/rollover freezes a final-estimate snapshot, so the Cycle
  // Yield report must refresh too.
  function invalidateCycleQueries() {
    qc.invalidateQueries({ queryKey: ['plot', plotId] });
    qc.invalidateQueries({ queryKey: ['plot-cycles', plotId] });
    qc.invalidateQueries({ queryKey: ['plots'] });
    qc.invalidateQueries({ queryKey: ['report-plot-status'] });
    qc.invalidateQueries({ queryKey: ['report-cycle-yield'] });
  }

  // Round 8-6I Part G — reactivate/reactivate-with-cycle additionally flips
  // is_active, which the Plots list's plot-status filter and province
  // dropdown (round 8-6I Part B) both depend on — invalidate that too, on
  // top of everything invalidateCycleQueries already covers.
  function invalidateReactivateQueries() {
    invalidateCycleQueries();
    qc.invalidateQueries({ queryKey: ['plot-provinces'] });
  }

  if (isLoading) {
    return <div className="flex justify-center py-20 text-gray-400">กำลังโหลด...</div>;
  }
  if (isError || !plot) {
    return (
      <div className="container mx-auto px-4 py-8">
        <Link to="/farmlog/admin/plots" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeft className="h-4 w-4" /> กลับไปหน้ารายการแปลง
        </Link>
        <p className="mt-6 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          ไม่พบข้อมูลแปลงนี้ หรือคุณไม่มีสิทธิ์เข้าถึง
        </p>
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <Link to="/farmlog/admin/plots" className="mb-4 inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeft className="h-4 w-4" /> กลับไปหน้ารายการแปลง
      </Link>

      {/* Round 8-6I Part F — prominent warning band for a permanently
          deactivated plot, near the header (before the identity block). No
          equivalent band exists for an active plot — there is nothing to
          warn about. */}
      {!plot.isActive && (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div>
            <p className="font-semibold">แปลงนี้ปิดใช้งานอยู่</p>
            <p className="mt-0.5 text-xs text-amber-700">
              QR และหมายเลขเข้าตรวจเดิมจะกลับมาใช้ได้เมื่อเปิดแปลงพร้อมรอบปลูกใหม่
            </p>
          </div>
        </div>
      )}

      {reactivateSuccessMessage && (
        <div className="mb-4 flex items-start justify-between gap-2 rounded-md border border-green-300 bg-green-50 px-4 py-3 text-sm text-green-800">
          <span>{reactivateSuccessMessage}</span>
          <button
            type="button"
            onClick={() => setReactivateSuccessMessage(null)}
            aria-label="ปิดข้อความ"
            className="shrink-0 text-green-700 hover:text-green-900"
          >
            ✕
          </button>
        </div>
      )}

      <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="shrink-0 rounded-md bg-green-50 p-2"><MapPin className="h-5 w-5 text-green-600" /></div>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-gray-900">{plot.name}</h1>

            {/* Identity metadata (round 8-2.6) — Supplier/รหัสแปลง/ชื่อแปลง
                shown as distinct labelled fields instead of one ambiguous
                "code — name" string, so the three never get conflated. */}
            <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-2 lg:grid-cols-3">
              <Field label="ชื่อ Supplier" value={supplierIdentityDisplay(plot.supplierName, plot.supplierCode)} />
              <Field
                label="รหัสแปลง"
                value={<PlotCodeValue plotCode={plot.plotCode} source={plot.plotCodeSource} />}
              />
              <Field label="ชื่อแปลง" value={displayOrDash(plot.name)} />
            </dl>

            {/* ที่ตั้ง kept as its own labelled field, separate from identity
                above, so it never reads as part of the plot name. */}
            <dl className="mt-3">
              <div>
                <dt className="text-xs text-gray-400">ที่ตั้ง</dt>
                <dd className="text-sm font-medium text-gray-800">
                  {[plot.village, plot.district, plot.province].filter(Boolean).join(', ') || 'ไม่มีข้อมูลที่ตั้ง'}
                </dd>
              </div>
            </dl>
          </div>
        </div>

        {/* Operational actions (round 6.1), ordered: ตรวจแปลง → แก้ไขแปลง →
            พิมพ์ QR. QR prints from the plot's own denormalised
            supplier data — no suppliers fetch needed. Assignment is available
            via deep-link (?manage=assign) but not surfaced here (round 8.0).
            Round 8-6I Part F — reactivation buttons lead this group when the
            plot is inactive: "เปิดใช้งานและเริ่มรอบปลูกใหม่" (primary, the
            recommended choice) then "เปิดใช้งานแปลงเท่านั้น" (secondary). */}
        {(canCreateRecords || canUpdatePlot || plot.qrKey || (!plot.isActive && canReactivate)) && (
          <div className="flex flex-wrap gap-2 sm:justify-end">
            {/* Round E — reopening a plot AND starting another season is the
                same move as rollover, so it goes with it. Plain reactivation
                (next button) stays: an accidental deactivation must remain
                undoable, and reopening without a cycle breaks no rule. */}
            {canReactivateWithCycle() && !plot.isActive && canReactivate && canUpdatePlot && (
              <button
                type="button"
                onClick={() => setReactivatingWithCycle(true)}
                className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-700"
              >
                <Sprout className="h-4 w-4" />
                เปิดใช้งานและเริ่มรอบปลูกใหม่
              </button>
            )}
            {!plot.isActive && canReactivate && (
              <button
                type="button"
                onClick={() => setReactivatingOnly(true)}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm hover:bg-secondary"
              >
                <Unlock className="h-4 w-4" />
                เปิดใช้งานแปลงเท่านั้น
              </button>
            )}
            {canCreateRecords && plot.isActive && !cyclesLoading && (
              activeCycle ? (
                <Link
                  to={`/farmlog/records/new?supplierId=${encodeURIComponent(plot.supplierId)}&plotId=${encodeURIComponent(plot.id)}`}
                  className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-700"
                >
                  <ClipboardCheck className="h-4 w-4" />
                  ตรวจแปลง
                </Link>
              ) : (
                <button
                  type="button"
                  disabled
                  title="ต้องเริ่มรอบปลูกก่อนจึงจะบันทึกการตรวจแปลงได้"
                  className="inline-flex cursor-not-allowed items-center gap-2 rounded-md bg-gray-200 px-4 py-2 text-sm font-medium text-gray-400 shadow-sm"
                >
                  <ClipboardCheck className="h-4 w-4" />
                  ตรวจแปลง
                </button>
              )
            )}
            {canUpdatePlot && (
              <Link
                to={`/farmlog/admin/plots?manage=edit&plotId=${plot.id}`}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90"
              >
                <Pencil className="h-4 w-4" />
                แก้ไขแปลง
              </Link>
            )}
            {plot.qrKey && plot.supplierCode && (
              <button
                type="button"
                onClick={() => setPrintItems([plotToQrLabel(plot)])}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm hover:bg-secondary"
              >
                <Printer className="h-4 w-4" />
                พิมพ์ QR
              </button>
            )}
          </div>
        )}
      </header>

      <div className="space-y-6">
        <AccessPhoneSection
          plot={plot}
          canUpdate={canUpdatePlot}
          onManage={() => setManagingPhones(true)}
        />

        {plotId && (
          <InspectionCredentialSection
            plotId={plotId}
            canUpdate={canUpdatePlot}
            onManage={setManagingCredential}
          />
        )}

        <CurrentCycleSection
          plot={plot}
          // Round Q — the LATEST cycle whatever its status, so the card keeps
          // showing the season after it closes. The write handlers below stay
          // bound to activeCycle: the card only offers them while it is live.
          cycle={latestCycle}
          cycleCount={cycles.length}
          canUpdate={canUpdatePlot}
          cyclesLoading={cyclesLoading}
          onStart={() => setStartingCycle(true)}
          onEdit={() => activeCycle && setEditingCycle(activeCycle)}
          onCloseCycle={() => activeCycle && setClosingCycle(activeCycle)}
          onCancelCycle={() => activeCycle && setCancellingCycle(activeCycle)}
          canCancelCycle={canCancelCycle}
          onRollover={() => activeCycle && setRollingOverCycle(activeCycle)}
          canSeeVariety={canSeeVariety}
          canReadRecords={canReadRecords}
        />

        <YieldPlanningSection
          plot={plot}
          activeCycle={activeCycle}
          cycleCount={cycles.length}
          canUpdate={canUpdatePlot}
          onStart={() => setStartingCycle(true)}
          onEdit={() => activeCycle && setEditingCycle(activeCycle)}
        />

        <CurrentStatusSection
          lastInspectionRecordId={plot.lastInspectionRecordId}
          canSeeVariety={canSeeVariety}
          fields={{
            crop: plot.currentCrop,
            variety: plot.currentVariety,
            plantingDate: plot.currentPlantingDate,
            stage: plot.currentStage,
            yieldPct: plot.currentYieldPct,
            fieldPrepScore: plot.currentFieldPrepScore,
            weatherScore: plot.currentWeatherScore,
            careScore: plot.currentCareScore,
            varietyResistanceScore: plot.currentVarietyResistanceScore,
            gpsLat: plot.currentGpsLat,
            gpsLng: plot.currentGpsLng,
            lastInspectedAt: plot.lastInspectedAt,
            lastInspectedByCode: plot.lastInspectedByCode,
          }}
        />

        {canReadRecords && (
          <section>
            {/* flex-wrap + gap — on a narrow screen the heading and the
                selector drop onto separate lines instead of overlapping. */}
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-gray-800">ประวัติการตรวจ</h2>
              <div className="flex items-center gap-2">
                <label htmlFor="inspection-history-page-size" className="text-xs text-gray-500">
                  แสดงต่อหน้า
                </label>
                <select
                  id="inspection-history-page-size"
                  value={historyPageSize}
                  onChange={(e) => handleHistoryPageSizeChange(Number(e.target.value))}
                  className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
                >
                  {HISTORY_PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n} รายการ</option>
                  ))}
                </select>
              </div>
            </div>
            {historyLoading ? (
              <p className="py-8 text-center text-sm text-gray-400">กำลังโหลด...</p>
            ) : historyIsError ? (
              <p className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                โหลดประวัติการตรวจไม่สำเร็จ
              </p>
            ) : history.length === 0 ? (
              <p className="py-8 text-center text-sm text-gray-400">ยังไม่มีประวัติการตรวจ</p>
            ) : (
              <ul className="space-y-3">
                {history.map((r) => <HistoryRow key={r.id} record={r} />)}
              </ul>
            )}

            {history.length > 0 && (
              <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded border px-3 py-1.5 disabled:opacity-40 hover:bg-gray-50"
                >
                  ← ก่อนหน้า
                </button>
                <span>หน้า {page + 1}</span>
                <button
                  type="button"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={history.length < historyPageSize}
                  className="rounded border px-3 py-1.5 disabled:opacity-40 hover:bg-gray-50"
                >
                  ถัดไป →
                </button>
              </div>
            )}
          </section>
        )}
      </div>

      {printItems && (
        <PlotQrPrintSheet items={printItems} onClose={() => setPrintItems(null)} />
      )}

      {startingCycle && plotId && (
        <StartCycleModal
          plotId={plotId}
          supplierCode={plot.supplierCode}
          onClose={() => setStartingCycle(false)}
          onSaved={() => { setStartingCycle(false); invalidateCycleQueries(); }}
        />
      )}
      {editingCycle && plotId && (
        <EditCycleModal
          plotId={plotId}
          supplierCode={plot.supplierCode}
          cycle={editingCycle}
          onClose={() => setEditingCycle(null)}
          onSaved={() => { setEditingCycle(null); invalidateCycleQueries(); }}
        />
      )}
      {closingCycle && plotId && (
        <CloseCycleModal
          plotId={plotId}
          cycle={closingCycle}
          onClose={() => setClosingCycle(null)}
          onSaved={() => { setClosingCycle(null); invalidateCycleQueries(); }}
        />
      )}
      {cancellingCycle && plotId && (
        <CancelCycleModal
          plotId={plotId}
          cycle={cancellingCycle}
          onClose={() => setCancellingCycle(null)}
          onSaved={() => { setCancellingCycle(null); invalidateCycleQueries(); }}
        />
      )}
      {rollingOverCycle && plotId && (
        <RolloverCycleModal
          plotId={plotId}
          supplierCode={plot.supplierCode}
          cycle={rollingOverCycle}
          onClose={() => setRollingOverCycle(null)}
          onSaved={() => { setRollingOverCycle(null); invalidateCycleQueries(); }}
        />
      )}

      {managingPhones && plotId && (
        <PlotAccessPhoneModal
          plotId={plotId}
          plotLabel={plot.plotCode}
          onClose={() => setManagingPhones(false)}
          onSaved={() => setManagingPhones(false)}
        />
      )}

      {managingCredential !== null && plotId && (
        <PlotInspectionPasswordModal
          plotId={plotId}
          supplierCode={plot.supplierCode}
          supplierName={plot.supplierName}
          plotCode={plot.plotCode}
          plotName={plot.name}
          configured={managingCredential}
          // Unmounting is what clears both PIN fields — there is no other copy.
          onClose={() => setManagingCredential(null)}
          onSaved={() => setManagingCredential(null)}
        />
      )}

      {/* Round 8-6I Part F — reactivate (reopen only) / reactivate-with-cycle
          (atomic reopen + first cycle), each calling exactly ONE endpoint. */}
      {reactivatingOnly && plotId && (
        <ReactivatePlotModal
          plotId={plotId}
          plotCode={plot.plotCode}
          onClose={() => setReactivatingOnly(false)}
          onSaved={() => {
            setReactivatingOnly(false);
            invalidateReactivateQueries();
            setReactivateSuccessMessage('เปิดใช้งานแปลงแล้ว');
          }}
        />
      )}

      {reactivatingWithCycle && plotId && (
        <ReactivatePlotWithCycleModal
          plotId={plotId}
          supplierCode={plot.supplierCode}
          onClose={() => setReactivatingWithCycle(false)}
          onSaved={() => {
            setReactivatingWithCycle(false);
            invalidateReactivateQueries();
            setReactivateSuccessMessage('เปิดใช้งานแปลงและเริ่มรอบปลูกใหม่แล้ว');
          }}
        />
      )}
    </div>
  );
}

export default PlotDetail;
