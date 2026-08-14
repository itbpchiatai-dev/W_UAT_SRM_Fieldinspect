/**
 * PlotStatusReport — FarmLog Report #1 "สถานะแปลง".
 *
 * A scope-aware table of every active plot with its latest inspection-derived
 * status + yield (read from the plots table's denormalized current-* columns),
 * filterable by supplier / province / crop / inspected-state / last-inspection
 * date range, with an Excel export of the exact same filtered rows.
 *
 * Gated by plots.read (route + backend); RLS scopes a supplier-only user to
 * their own plots automatically.
 */
import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { BarChart3, Download, Loader2 } from 'lucide-react';
import {
  listPlotStatus,
  downloadPlotStatusReport,
  type PlotStatusRow,
  type PlotStatusParams,
} from '../../../api/reports';
import { listSuppliers } from '../../../api/suppliers';
import { listPlotProvinces } from '../../../api/plots';
import { listMasterData } from '../../../api/masterdata';
import { computeCurrentExpectedYield, formatYieldQuantity } from '../../../lib/yield-planning';
import { toNumberOrNull } from '../../../lib/numeric';
import { CompactScores } from '../../../components/farmlog/CompactScores';

export function PlotStatusReport() {
  const [filterSupplier, setFilterSupplier] = useState('');
  const [filterProvince, setFilterProvince] = useState('');
  const [filterCrop, setFilterCrop] = useState('');
  const [filterInspected, setFilterInspected] = useState('');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');

  const params: PlotStatusParams = {
    supplierId: filterSupplier || undefined,
    province: filterProvince || undefined,
    crop: filterCrop || undefined,
    inspected: filterInspected || undefined,
    dateFrom: filterDateFrom || undefined,
    dateTo: filterDateTo || undefined,
  };

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: provinces = [] } = useQuery({
    queryKey: ['plot-provinces', filterSupplier],
    queryFn: () => listPlotProvinces({ supplierId: filterSupplier || undefined, activeOnly: true }),
    staleTime: 60 * 1000,
  });

  const { data: crops = [] } = useQuery({
    queryKey: ['masterdata', 'crop'],
    queryFn: () => listMasterData({ type: 'crop', activeOnly: true }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['report-plot-status', params],
    queryFn: () => listPlotStatus(params),
  });

  const exportM = useMutation({
    mutationFn: () => downloadPlotStatusReport(params),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'plot-status-report.xlsx';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });

  const selectClass =
    'rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500';

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-6 w-6 text-green-600" />
          <h1 className="text-2xl font-bold text-gray-900">รายงานสถานะแปลง</h1>
        </div>
        <button
          onClick={() => exportM.mutate()}
          disabled={exportM.isPending || rows.length === 0}
          className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-40"
        >
          {exportM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          ดาวน์โหลด Excel
        </button>
      </header>

      {exportM.isError && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          ดาวน์โหลดไม่สำเร็จ ลองใหม่อีกครั้ง
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <select
          value={filterSupplier}
          onChange={(e) => { setFilterSupplier(e.target.value); setFilterProvince(''); }}
          className={selectClass}
        >
          <option value="">ทุก Supplier</option>
          {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>

        <select value={filterProvince} onChange={(e) => setFilterProvince(e.target.value)} className={selectClass}>
          <option value="">ทุกจังหวัด</option>
          {provinces.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>

        <select value={filterCrop} onChange={(e) => setFilterCrop(e.target.value)} className={selectClass}>
          <option value="">ทุกชนิดพืช</option>
          {crops.map((c) => <option key={c.id} value={c.value}>{c.value}</option>)}
        </select>

        <select value={filterInspected} onChange={(e) => setFilterInspected(e.target.value)} className={selectClass}>
          <option value="">ทั้งหมด (ตรวจ/ยังไม่ตรวจ)</option>
          <option value="inspected">เฉพาะที่ตรวจแล้ว</option>
          <option value="not_inspected">เฉพาะที่ยังไม่ตรวจ</option>
        </select>

        <input
          type="date"
          value={filterDateFrom}
          onChange={(e) => setFilterDateFrom(e.target.value)}
          title="ตรวจล่าสุด ตั้งแต่วันที่"
          className={selectClass}
        />
        <input
          type="date"
          value={filterDateTo}
          onChange={(e) => setFilterDateTo(e.target.value)}
          title="ตรวจล่าสุด ถึงวันที่"
          className={selectClass}
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="flex justify-center py-16 text-gray-400">กำลังโหลด...</div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-gray-400">
            <BarChart3 className="h-10 w-10" />
            <p>ไม่พบข้อมูลแปลงตามเงื่อนไข</p>
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {/* Neutral single score column (round 5.4): each plot's 4
                    current scores are labelled by its latest inspection's
                    growth-stage protocol, which varies row to row — fixed
                    per-slot headers would mislabel. NOTE: the Excel export
                    still carries the old per-slot headers (server-generated,
                    downloadPlotStatusReport) — a backend change tracked
                    separately. */}
                {['Supplier / แปลง', 'จังหวัด', 'พืช/พันธุ์/ระยะ', 'Yield ปัจจุบัน', 'คะแนนตรวจ (4 หัวข้อ)', 'ตรวจล่าสุด', 'สถานะ'].map((h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r: PlotStatusRow) => {
                // Round 7.4 — a plot with no active cycle shows "รอเริ่มรอบปลูก"
                // and no yield plan (the backend already sends null identity/
                // plan for it); only compute yield when a cycle is active.
                const hasActiveCycle = r.activeCycleId != null;
                const currentYield = hasActiveCycle
                  ? computeCurrentExpectedYield(r.expectedYieldFull, r.currentYieldPct)
                  : null;
                const pct = hasActiveCycle ? toNumberOrNull(r.currentYieldPct) : null;
                return (
                  <tr key={r.plotId} className="hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-700">
                      <div className="text-xs text-gray-400">{r.supplierName}</div>
                      <div className="font-medium">{r.plotCode}</div>
                      {r.plotName && r.plotName !== r.plotCode && (
                        <div className="text-xs text-gray-400">{r.plotName}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-700">{r.province ?? '—'}</td>
                    <td className="px-4 py-3 text-sm text-gray-700">
                      {hasActiveCycle ? (
                        <>
                          <div>{r.currentCrop ?? '—'}</div>
                          {r.currentVariety && <div className="text-xs text-gray-400">{r.currentVariety}</div>}
                          {r.currentStage && <div className="text-xs text-gray-400">{r.currentStage}</div>}
                        </>
                      ) : (
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
                          รอเริ่มรอบปลูก
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {currentYield != null ? (
                        <div>
                          <span className="font-semibold text-green-700">
                            {formatYieldQuantity(currentYield, r.expectedYieldUnit)}
                          </span>
                          {pct != null && <span className="ml-1 text-xs text-gray-400">({pct}%)</span>}
                        </div>
                      ) : pct != null ? (
                        <span className="font-semibold text-green-700">{pct}%</span>
                      ) : (
                        <span className="text-gray-400 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <CompactScores scores={[r.currentFieldPrepScore, r.currentWeatherScore, r.currentCareScore, r.currentVarietyResistanceScore]} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">
                      {r.lastInspectedAt ? (
                        <div>
                          <div>{r.lastInspectedAt.slice(0, 10)}</div>
                          {r.lastInspectedByCode && (
                            <div className="text-xs text-gray-400">โดย {r.lastInspectedByCode}</div>
                          )}
                        </div>
                      ) : (
                        <span className="text-gray-400 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${r.isInspected ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                        {r.isInspected ? 'ตรวจแล้ว' : 'ยังไม่ตรวจ'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {rows.length > 0 && (
        <div className="mt-3 text-sm text-gray-500">รวม {rows.length} แปลง</div>
      )}
    </div>
  );
}
