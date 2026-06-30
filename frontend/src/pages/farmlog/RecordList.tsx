/**
 * RecordList — FarmLog field inspection records list with scope-aware filtering.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ClipboardList, Plus, PowerOff, Eye, FileText } from 'lucide-react';
import { listRecords, deactivateRecord, type RecordSummary } from '../../api/records';
import { listSuppliers } from '../../api/suppliers';
import { listPlots } from '../../api/plots';
import { useHasPermission } from '../../hooks/useHasPermission';

const PAGE_SIZE = 30;

/** A list-coded status counts as an alert unless empty or the "ไม่พบ" option. */
function StatusCell({ status }: { status: string | null }) {
  if (!status) return <span className="text-gray-400 text-xs">—</span>;
  if (status === 'ไม่พบ') return <span className="text-gray-400 text-xs">ไม่พบ</span>;
  return (
    <span className="inline-flex items-center rounded-full bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-700">
      {status}
    </span>
  );
}

export function RecordList() {
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [filterSupplier, setFilterSupplier] = useState('');
  const [filterPlot, setFilterPlot] = useState('');
  const [filterDateFrom, setFilterDateFrom] = useState('');
  const [filterDateTo, setFilterDateTo] = useState('');

  const canCreate = useHasPermission('records.create');
  const canDelete = useHasPermission('records.delete');

  const { data: suppliers = [] } = useQuery({
    queryKey: ['suppliers', 'all'],
    queryFn: () => listSuppliers({ activeOnly: true, limit: 200 }),
  });

  const { data: plots = [] } = useQuery({
    queryKey: ['plots', filterSupplier],
    queryFn: () => listPlots({
      supplierId: filterSupplier || undefined,
      activeOnly: true,
      limit: 200,
    }),
  });

  const { data: records = [], isLoading } = useQuery({
    queryKey: ['records', page, filterSupplier, filterPlot, filterDateFrom, filterDateTo],
    queryFn: () => listRecords({
      supplierId: filterSupplier || undefined,
      plotId: filterPlot || undefined,
      dateFrom: filterDateFrom || undefined,
      dateTo: filterDateTo || undefined,
      activeOnly: true,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
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
      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
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
                {['วันที่', 'Supplier / แปลง', 'พืช/ระยะ', 'Yield', 'แมลง', 'โรค', 'สถานะ', ''].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {records.map((r: RecordSummary) => (
                <tr key={r.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">{r.recordDate}</td>
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
                    {r.yieldPct != null ? (
                      <span className="font-semibold text-green-700">{parseFloat(r.yieldPct)}%</span>
                    ) : (
                      <span className="text-gray-400 text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm"><StatusCell status={r.pestStatus} /></td>
                  <td className="px-4 py-3 text-sm"><StatusCell status={r.diseaseStatus} /></td>
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
                      <Link
                        to={`/farmlog/records/${r.id}`}
                        className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                        title="แก้ไข"
                      >
                        <Eye className="h-4 w-4" />
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
      {records.length > 0 && (
        <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
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
            disabled={records.length < PAGE_SIZE}
            className="rounded border px-3 py-1.5 disabled:opacity-40 hover:bg-gray-50"
          >
            ถัดไป →
          </button>
        </div>
      )}
    </div>
  );
}
