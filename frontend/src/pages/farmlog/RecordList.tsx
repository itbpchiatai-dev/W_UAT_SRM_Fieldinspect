/**
 * RecordList — FarmLog field inspection records list with scope-aware filtering.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ClipboardList, Plus, PowerOff, FileText } from 'lucide-react';
import { listRecords, deactivateRecord, type RecordSummary } from '../../api/records';
import { listSuppliers } from '../../api/suppliers';
import { listPlots } from '../../api/plots';
import { useHasPermission } from '../../hooks/useHasPermission';
import { CompactScores } from '../../components/farmlog/CompactScores';
import { recordCycleDisplayName } from '../../lib/plot-cycle';
import { formatYieldQuantity } from '../../lib/yield-planning';
import { toNumberOrNull } from '../../lib/numeric';
import { fetchAllPages } from '../../lib/paginate';

// Round 8-25D — this page used to be fixed at 30 rows/page with no way to
// see more. Same [100, 200, 500, 'ทั้งหมด'] contract as the Plots admin page.
const PAGE_SIZE_OPTIONS = [100, 200, 500, 'all'] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
const DEFAULT_PAGE_SIZE: PageSize = 100;
const ALL_FETCH_CHUNK = 200;

/** Round 8-8C — a kg-first record (yieldQuantityKg present) shows the kg
 * amount as the primary value with the percent as secondary text below (same
 * cell, no new column, so the table never grows wider than necessary); a
 * legacy record (percent only) falls back to the original percent-only
 * display. quantityKg=0 is a real value (shows "0 kg"), never the em dash —
 * only truly missing data (both null) falls back to that. */
function YieldCell({ record }: { record: RecordSummary }) {
  const quantityKg = toNumberOrNull(record.yieldQuantityKg);
  const pct = record.yieldPct != null ? parseFloat(record.yieldPct) : null;

  if (quantityKg != null) {
    return (
      <div>
        <div className="font-semibold text-green-700">{formatYieldQuantity(quantityKg, 'kg')}</div>
        {pct != null && <div className="text-xs text-gray-400">{pct}%</div>}
      </div>
    );
  }
  if (pct != null) {
    return <span className="font-semibold text-green-700">{pct}%</span>;
  }
  return <span className="text-gray-400 text-xs">—</span>;
}

export function RecordList() {
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  const [filterSupplier, setFilterSupplier] = useState('');
  const [filterPlot, setFilterPlot] = useState('');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');

  const canCreate = useHasPermission('records.create');
  const canDelete = useHasPermission('records.delete');

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
    staleTime: 5 * 60 * 1000,
  });

  const { data: plots = [] } = useQuery({
    queryKey: ['plots', filterSupplier],
    queryFn: () => listPlots({
      supplierId: filterSupplier || undefined,
      activeOnly: true,
      limit: 200,
    }),
    staleTime: 60 * 1000,
  });

  const { data: records = [], isLoading } = useQuery({
    queryKey: ['records', page, pageSize, filterSupplier, filterPlot, filterDateFrom, filterDateTo],
    queryFn: () => {
      const filters = {
        supplierId: filterSupplier || undefined,
        plotId: filterPlot || undefined,
        dateFrom: filterDateFrom || undefined,
        dateTo: filterDateTo || undefined,
        activeOnly: true,
      };
      if (pageSize === 'all') {
        return fetchAllPages(
          (offset, limit) => listRecords({ ...filters, limit, offset }),
          ALL_FETCH_CHUNK,
        );
      }
      return listRecords({ ...filters, limit: pageSize, offset: page * pageSize });
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: string) => deactivateRecord(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['records'] }),
  });

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-6">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-6 w-6 text-green-600" />
          <h1 className="text-2xl font-bold text-gray-900">บันทึกการตรวจแปลง</h1>
        </div>
        {canCreate && (
          <Link
            to="/farmlog/records/new"
            className="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700"
          >
            <Plus className="h-4 w-4" />
            บันทึกใหม่
          </Link>
        )}
      </header>

      {/* Filters */}
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <select
          value={filterSupplier}
          onChange={e => { setFilterSupplier(e.target.value); setFilterPlot(''); setPage(0); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
        >
          <option value="">ทุก Supplier</option>
          {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>

        <select
          value={filterPlot}
          onChange={e => { setFilterPlot(e.target.value); setPage(0); }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
        >
          <option value="">ทุกแปลง</option>
          {plots.map(p => <option key={p.id} value={p.id}>{p.plotCode} — {p.name}</option>)}
        </select>

        <input
          type="date"
          value={filterDateFrom}
          onChange={e => { setFilterDateFrom(e.target.value); setPage(0); }}
          placeholder="วันเริ่มต้น"
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
        />

        <input
          type="date"
          value={filterDateTo}
          onChange={e => { setFilterDateTo(e.target.value); setPage(0); }}
          placeholder="วันสิ้นสุด"
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="flex justify-center py-16 text-gray-400">กำลังโหลด...</div>
        ) : records.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16 text-gray-400">
            <ClipboardList className="h-10 w-10" />
            <p>ยังไม่มีบันทึก</p>
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {/* Score column is a single neutral "คะแนนตรวจ" (round 5.4):
                    a record's 4 criteria are remapped by its growth stage,
                    so fixed per-slot column labels would mislead across a
                    mixed-stage list. The labelled breakdown is in the
                    preview/detail (snapshot-driven). */}
                {['วันที่', 'Supplier / แปลง', 'พืช/ระยะ', 'Yield', 'คะแนนตรวจ (4 หัวข้อ)', 'สถานะ', ''].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {records.map((r: RecordSummary) => (
                <tr key={r.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">
                    {r.recordDate}
                    {/* Which planting cycle this record belongs to (round
                        8.0.5) — the record's OWN cycle, bound at create
                        time; leads with cycleLabel, falls back to รอบที่ N. */}
                    {(r.cycleLabel != null || r.cycleNo != null) && (
                      <div className="mt-0.5 inline-block rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                        {recordCycleDisplayName(r)}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {r.supplierName && <div className="text-xs text-gray-400">{r.supplierName}</div>}
                    <div className="font-medium">{r.plotCode || r.plotId}</div>
                    {r.plotName && r.plotName !== r.plotCode && (
                      <div className="text-xs text-gray-400">{r.plotName}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    <div>{r.crop ?? '—'}</div>
                    {r.growthStage && <div className="text-xs text-gray-400">{r.growthStage}</div>}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <YieldCell record={r} />
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <CompactScores scores={[r.fieldPrepScore, r.weatherScore, r.careScore, r.varietyResistanceScore]} />
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${r.isActive ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      {r.isActive ? 'ใช้งาน' : 'ปิด'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                    <div className="flex items-center justify-end gap-2">
                      <Link
                        to={`/farmlog/records/${r.id}/preview`}
                        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                        title="One Page Preview"
                      >
                        <FileText className="h-4 w-4" />
                      </Link>
                      {canDelete && r.isActive && (
                        <button
                          onClick={() => { if (confirm('ปิดบันทึกนี้?')) deactivateM.mutate(r.id); }}
                          className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600"
                          title="ปิด"
                        >
                          <PowerOff className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
        <div className="flex items-center gap-2">
          <label htmlFor="records-page-size">แสดง</label>
          <select
            id="records-page-size"
            value={String(pageSize)}
            onChange={(e) => {
              setPage(0);
              const v = e.target.value;
              setPageSize(v === 'all' ? 'all' : (Number(v) as PageSize));
            }}
            className="rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm focus:border-green-500 focus:outline-none focus:ring-1 focus:ring-green-500"
          >
            {PAGE_SIZE_OPTIONS.map((opt) => (
              <option key={opt} value={String(opt)}>
                {opt === 'all' ? 'ทั้งหมด' : `${opt} แถว`}
              </option>
            ))}
          </select>
        </div>
        {pageSize === 'all' ? (
          <span>{records.length} บันทึก</span>
        ) : (
          <div className="flex items-center gap-4">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded border px-3 py-1.5 disabled:opacity-40 hover:bg-gray-50"
            >
              ← ก่อนหน้า
            </button>
            <span>หน้า {page + 1}</span>
            <button
              onClick={() => setPage(p => p + 1)}
              disabled={records.length < pageSize}
              className="rounded border px-3 py-1.5 disabled:opacity-40 hover:bg-gray-50"
            >
              ถัดไป →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
