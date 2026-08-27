/**
 * CycleYieldReport — FarmLog Report #2 "ผลผลิตตามรอบปลูก".
 *
 * One row per PlotCycle with its frozen final ESTIMATED-yield snapshot (round
 * 8-2.8A), read verbatim from the backend — this page NEVER recomputes the
 * estimate. finalEstimatedYield is an ESTIMATE, not actual harvested yield.
 * Default filter shows closed cycles (harvested + cancelled); includes the
 * history of deactivated plots so a closed plot never loses its record.
 *
 * Gated by plots.read (route + backend); RLS scopes a supplier-only user to
 * their own plots. The "record used" link only renders with records.read.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { Sprout, Download, Loader2, FileText } from 'lucide-react';
import {
  listCycleYieldReport,
  downloadCycleYieldReport,
  type CycleYieldRow,
  type CycleYieldParams,
} from '../../../api/reports';
import { listSuppliers } from '../../../api/suppliers';
import { listMasterData, masterDataQueryKey } from '../../../api/masterdata';
import { describeFinalEstimate, formatYieldQuantity } from '../../../lib/yield-planning';
import { useHasPermission } from '../../../hooks/useHasPermission';
import { fetchAllPages } from '../../../lib/paginate';

// Round 8-25D — this report used to have NO ceiling at all (every matching
// cycle came back in one response). Same [100, 200, 500, 'ทั้งหมด'] contract
// as the Plots admin page and the sibling PlotStatusReport.
const PAGE_SIZE_OPTIONS = [100, 200, 500, 'all'] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
const DEFAULT_PAGE_SIZE: PageSize = 100;
const ALL_FETCH_CHUNK = 200;

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'closed', label: 'รอบที่ปิดแล้ว' },
  { value: 'harvested', label: 'เก็บเกี่ยวแล้ว' },
  { value: 'cancelled', label: 'ยกเลิก' },
  { value: 'active', label: 'กำลังปลูก' },
  { value: 'all', label: 'ทั้งหมด' },
];

const STATUS_BADGE: Record<string, string> = {
  active: 'bg-green-50 text-green-700',
  harvested: 'bg-blue-50 text-blue-700',
  cancelled: 'bg-gray-100 text-gray-500',
};

const STATUS_LABEL: Record<string, string> = {
  active: 'กำลังปลูก',
  harvested: 'เก็บเกี่ยวแล้ว',
  cancelled: 'ยกเลิก',
};

function cycleTitle(row: CycleYieldRow): string {
  return row.cycleLabel || `รอบที่ ${row.cycleNo}`;
}

/** Round 8-5B — short Thai label for a lot's source, or null (no tag). */
function lotSourceLabel(source: CycleYieldRow['lotNoSource']): string | null {
  if (source === 'auto') return 'อัตโนมัติ';
  if (source === 'manual') return 'กรอกเอง';
  if (source === 'legacy') return 'ข้อมูลเดิม';
  return null;
}

export function CycleYieldReport() {
  const [filterSupplier, setFilterSupplier] = useState('');
  const [filterCrop, setFilterCrop] = useState('');
  const [filterStatus, setFilterStatus] = useState('closed');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);

  const canReadRecords = useHasPermission('records.read');

  // Filters only — no limit/offset. Shared as-is with the export mutation
  // below, which must always carry every filtered row.
  const filterParams: CycleYieldParams = {
    supplierId: filterSupplier || undefined,
    crop: filterCrop || undefined,
    status: filterStatus || undefined,
    dateFrom: filterDateFrom || undefined,
    dateTo: filterDateTo || undefined,
  };

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: crops = [] } = useQuery({
    queryKey: masterDataQueryKey('crop', null, true),
    queryFn: () => listMasterData({ type: 'crop', activeOnly: true }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: rows = [], isLoading, isError } = useQuery({
    queryKey: ['report-cycle-yield', page, pageSize, filterParams],
    queryFn: () => {
      if (pageSize === 'all') {
        return fetchAllPages(
          (offset, limit) => listCycleYieldReport({ ...filterParams, limit, offset }),
          ALL_FETCH_CHUNK,
        );
      }
      return listCycleYieldReport({ ...filterParams, limit: pageSize, offset: page * pageSize });
    },
  });

  const exportM = useMutation({
    mutationFn: () => downloadCycleYieldReport(filterParams),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'cycle-yield-report.xlsx';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });

  const selectClass =
    'rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500';

  return (
    <div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
        <p className="text-sm text-gray-500">
          ค่าประมาณการผลผลิตที่ freeze ตอนปิดรอบปลูก — ไม่ใช่ผลผลิตที่เก็บเกี่ยวได้จริง
        </p>
        <button
          onClick={() => exportM.mutate()}
          disabled={exportM.isPending || rows.length === 0}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-40"
        >
          {exportM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          ดาวน์โหลด Excel
        </button>
      </div>

      {exportM.isError && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ดาวน์โหลดไม่สำเร็จ ลองใหม่อีกครั้ง
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <select
          value={filterSupplier}
          onChange={(e) => { setPage(0); setFilterSupplier(e.target.value); }}
          aria-label="Supplier"
          className={selectClass}
        >
          <option value="">ทุก Supplier</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>

        <select value={filterCrop} onChange={(e) => { setPage(0); setFilterCrop(e.target.value); }} aria-label="ชนิดพืช" className={selectClass}>
          <option value="">ทุกชนิดพืช</option>
          {crops.map((c) => <option key={c.id} value={c.value}>{c.value}</option>)}
        </select>

        <select value={filterStatus} onChange={(e) => { setPage(0); setFilterStatus(e.target.value); }} aria-label="สถานะรอบ" className={selectClass}>
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>

        <input
          type="date"
          value={filterDateFrom}
          onChange={(e) => { setPage(0); setFilterDateFrom(e.target.value); }}
          title="วันที่ปิดรอบ ตั้งแต่วันที่"
          aria-label="วันที่ปิดรอบ ตั้งแต่"
          className={selectClass}
        />
        <input
          type="date"
          value={filterDateTo}
          onChange={(e) => { setPage(0); setFilterDateTo(e.target.value); }}
          title="วันที่ปิดรอบ ถึงวันที่"
          aria-label="วันที่ปิดรอบ ถึง"
          className={selectClass}
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="flex justify-center py-16 text-gray-400">กำลังโหลด...</div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-red-500">
            <p>โหลดข้อมูลไม่สำเร็จ ลองใหม่อีกครั้ง</p>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-gray-400">
            <Sprout className="h-10 w-10" />
            <p>ไม่พบรอบปลูกตามเงื่อนไข</p>
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {['Supplier / แปลง', 'รอบปลูก', 'พืช/พันธุ์/Lot', 'เป้าผลิต', 'สถานะ / ปิดรอบ', 'สรุปผลผลิต'].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r: CycleYieldRow) => {
                const final = describeFinalEstimate({
                  cycleStatus: r.cycleStatus,
                  finalEstimatedYield: r.finalEstimatedYield,
                  finalYieldPct: r.finalYieldPct,
                  expectedYieldUnit: r.expectedYieldUnit,
                });
                return (
                  <tr key={r.cycleId} className="hover:bg-gray-50 align-top">
                    <td className="px-4 py-3 text-sm text-gray-700">
                      <div className="text-xs text-gray-400">{r.supplierName} ({r.supplierCode})</div>
                      <div className="font-medium">{r.plotCode}</div>
                      {r.plotName && r.plotName !== r.plotCode && (
                        <div className="text-xs text-gray-400">{r.plotName}</div>
                      )}
                      {!r.plotIsActive && (
                        <span className="mt-1 inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">
                          แปลงปิดใช้งาน
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-700">
                      <div className="font-medium">{cycleTitle(r)}</div>
                      {r.province && <div className="text-xs text-gray-400">{r.province}</div>}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-700">
                      <div>{r.crop ?? '—'}</div>
                      {r.variety && <div className="text-xs text-gray-400">{r.variety}</div>}
                      {/* Round 8-5B — PO / P.Code + Lot (with source), from THIS
                          row's own cycle (historical report). */}
                      <div className="text-xs text-gray-400">
                        PO {r.poNumber || '—'} · P.Code {r.pCode || '—'}
                      </div>
                      {r.lotNo && (
                        <div className="text-xs text-gray-400">
                          Lot ระบบ {r.lotNo}
                          {lotSourceLabel(r.lotNoSource) && <span className="ml-1">({lotSourceLabel(r.lotNoSource)})</span>}
                        </div>
                      )}
                      {/* Round 8-12C — Supplier Lot No: a SEPARATE identity
                          from the system-generated Lot above, never merged
                          into it. Always rendered (dash when absent), same
                          as PO/P.Code above. */}
                      <div className="text-xs text-gray-400">
                        Supplier Lot {r.supplierLotNo || '—'}
                      </div>
                      {r.plantingDate && <div className="text-xs text-gray-400">ปลูก {r.plantingDate}</div>}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-700">
                      {r.expectedYieldFull != null
                        ? `${r.expectedYieldFull}${r.expectedYieldUnit ? ` ${r.expectedYieldUnit}` : ''}`
                        : '—'}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[r.cycleStatus] ?? 'bg-gray-100 text-gray-500'}`}>
                        {STATUS_LABEL[r.cycleStatus] ?? r.cycleStatus}
                      </span>
                      {r.closedAt && (
                        <div className="mt-1 text-xs text-gray-400">{r.closedAt.slice(0, 10)}</div>
                      )}
                      {r.closeReason && (
                        <div className="mt-0.5 text-xs text-gray-400">{r.closeReason}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {final.kind === 'active' ? (
                        <span className="text-xs text-gray-400">{final.hint}</span>
                      ) : final.kind === 'none' ? (
                        <div>
                          <div className="text-xs text-gray-400">{final.label}</div>
                          <div className="text-xs text-gray-400">{final.message}</div>
                        </div>
                      ) : (
                        <div>
                          <div className="text-xs text-gray-400">{final.label}</div>
                          <div className="font-semibold text-green-700">{final.text}</div>
                        </div>
                      )}
                      {/* Round 8-7C.2 — the record-source link is CYCLE-level
                          info (which inspection record was used to snapshot
                          this cycle's close), never gated on the estimate
                          having a value: a closed cycle can have a resolved
                          finalInspectionRecordId even when finalEstimatedYield/
                          finalYieldPct are both null (e.g. the source record's
                          own yieldPct was null). Rendered exactly once, outside
                          the final.kind branches above, so it can never be
                          duplicated or silently dropped when kind !== 'value'.
                          Never shown for an active cycle (it hasn't closed
                          yet); never a raw UUID for a user without
                          records.read — no link, no ID text, nothing. */}
                      {r.cycleStatus !== 'active' && r.finalInspectionRecordId && canReadRecords && (
                        <Link
                          to={`/farmlog/records/${r.finalInspectionRecordId}/preview`}
                          className="mt-1 inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
                        >
                          <FileText className="h-3 w-3" /> บันทึกที่ใช้สรุป
                        </Link>
                      )}
                      {/* Round 8-7C.1 — ACTUAL harvest figures (round 8-7A's
                          final_plot), distinct from the ESTIMATE above.
                          Never shown for an active cycle (it hasn't closed
                          yet); renders only the fields actually present — no
                          fabricated values for a legacy cycle or one closed
                          by any path other than final_plot. Read verbatim,
                          never recomputed; actual uses its OWN unit
                          (finalYieldUnit), never expectedYieldUnit. */}
                      {r.cycleStatus !== 'active' && (
                        r.harvestYield != null || r.finalYieldAfterClean != null
                        || r.harvestDate != null || r.finalNote
                      ) && (
                        <div className="mt-2 space-y-0.5 border-t border-gray-100 pt-2">
                          {r.harvestYield != null && (
                            <div>
                              <span className="text-xs text-gray-400">ผลผลิตตอนเก็บเกี่ยว: </span>
                              <span className="text-xs text-gray-700">
                                {formatYieldQuantity(r.harvestYield, r.finalYieldUnit) ?? '—'}
                              </span>
                            </div>
                          )}
                          {r.finalYieldAfterClean != null && (
                            <div>
                              <span className="text-xs text-gray-400">ผลผลิตจริงหลังทำความสะอาด: </span>
                              <span className="text-xs font-semibold text-emerald-700">
                                {formatYieldQuantity(r.finalYieldAfterClean, r.finalYieldUnit) ?? '—'}
                              </span>
                            </div>
                          )}
                          {r.harvestDate != null && (
                            <div>
                              <span className="text-xs text-gray-400">วันที่เก็บเกี่ยว: </span>
                              <span className="text-xs text-gray-700">{r.harvestDate}</span>
                            </div>
                          )}
                          {r.finalNote && (
                            <div className="text-xs text-gray-400">หมายเหตุ: {r.finalNote}</div>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-gray-500">
        <div className="flex items-center gap-2">
          <label htmlFor="cycle-yield-page-size">แสดง</label>
          <select
            id="cycle-yield-page-size"
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
          <span>{rows.length} รอบปลูก</span>
        ) : (
          <div className="flex items-center gap-4">
            <button type="button" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))} className="disabled:opacity-40">← ก่อนหน้า</button>
            <span>หน้า {page + 1}</span>
            <button type="button" disabled={rows.length < pageSize} onClick={() => setPage((p) => p + 1)} className="disabled:opacity-40">ถัดไป →</button>
          </div>
        )}
      </div>
    </div>
  );
}
