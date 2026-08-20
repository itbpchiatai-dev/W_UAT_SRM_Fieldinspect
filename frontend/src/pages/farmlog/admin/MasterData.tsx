/**
 * Master Data admin (Step 12.5) — manage dropdown options per type.
 * Options edited here appear immediately in the record form's selects.
 *
 * Round 8-15B adds the crop/variety Excel import entry points — a
 * standalone "ดาวน์โหลด Template" button (masterdata.read; works even for a
 * caller who can't import) and a "นำเข้า Excel" button that opens
 * MasterDataCropVarietyImportModal (requires masterdata.create AND
 * masterdata.update — the modal's own commit-report call needs both). The
 * table below is unchanged — a successful import just invalidates the
 * shared ['masterdata'] query key, same as every existing mutation here, so
 * the crop/variety tabs pick up the new rows on their normal refetch.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Download, Loader2, Pencil, Plus, Trash2, Upload } from 'lucide-react';
import {
  createMasterData,
  deleteMasterData,
  downloadCropVarietyImportTemplate,
  listMasterData,
  masterDataQueryKey,
  updateMasterData,
  type MasterDataItem,
} from '../../../api/masterdata';
import { ActionMenu } from '../../../components/ActionMenu';
import { MasterDataCropVarietyImportModal } from '../../../components/farmlog/MasterDataCropVarietyImportModal';
import { MasterDataSelect } from '../../../components/farmlog/MasterDataSelect';
import { useHasPermission } from '../../../hooks/useHasPermission';
import { downloadBlob } from '../../../lib/downloadBlob';

// Rounds 8-14E / 8-14E.1 — 'level', 'severity', 'irrigation', and
// 'fertilizer' removed from this admin UI: no production consumer reads
// them (Record Form only uses growth_stage/weather via MasterDataButtons;
// confirmed via rg audit before each change, including fertilizer against
// Record Form/Public Inspect/Plot Cycle/Inspection Protocol/Report in
// round 8-14E.1). Frontend-only — the master_data rows for these types are
// untouched in the database and the authenticated list/CRUD API still
// serves them if queried directly.
const MD_TYPES: { type: string; label: string; parentType?: string }[] = [
  { type: 'crop', label: 'ชนิดพืช' },
  { type: 'variety', label: 'พันธุ์/สายพันธุ์', parentType: 'crop' },
  { type: 'growth_stage', label: 'ระยะการเจริญเติบโต' },
  { type: 'weather', label: 'สภาพอากาศ' },
  { type: 'province', label: 'จังหวัด' },
];

export function MasterData() {
  const qc = useQueryClient();
  const [activeType, setActiveType] = useState('crop');
  const [editing, setEditing] = useState<MasterDataItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const canRead = useHasPermission('masterdata.read');
  const canCreate = useHasPermission('masterdata.create');
  const canUpdate = useHasPermission('masterdata.update');
  const canDelete = useHasPermission('masterdata.delete');
  // Round 8-15B — the import modal's commit-report call needs BOTH, same
  // gate the backend itself enforces (masterdata.py's crop-variety-import
  // preview/commit routes).
  const canImportCropVariety = canCreate && canUpdate;

  const templateM = useMutation({
    mutationFn: () => downloadCropVarietyImportTemplate(),
    onSuccess: ({ blob, filename }) => downloadBlob(blob, filename),
  });

  const meta = MD_TYPES.find((t) => t.type === activeType)!;

  // Round 8-22A — this page shows BOTH active and inactive rows (so an
  // inactive one can be toggled back on), unlike every other masterdata
  // consumer below (activeOnly=true) — the query key must say so, or this
  // query and theirs would collide (see api/masterdata.ts's
  // masterDataQueryKey docstring).
  const { data: items = [], isLoading } = useQuery({
    queryKey: masterDataQueryKey(activeType),
    queryFn: () => listMasterData({ type: activeType }),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteMasterData(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['masterdata'] }),
  });
  const toggleM = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateMasterData(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['masterdata'] }),
  });

  const showForm = creating || editing !== null;

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">Master Data</h1>
          <p className="mt-1 text-sm text-muted-foreground">จัดการตัวเลือก dropdown ของฟอร์มบันทึก</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canRead && (
            <button type="button" onClick={() => templateM.mutate()} disabled={templateM.isPending}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium shadow-sm hover:bg-secondary disabled:opacity-60">
              {templateM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              ดาวน์โหลด Template (ชนิดพืชและพันธุ์)
            </button>
          )}
          {canImportCropVariety && (
            <button type="button" onClick={() => setImportOpen(true)}
              className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium shadow-sm hover:bg-secondary">
              <Upload className="h-4 w-4" />
              นำเข้า Excel (ชนิดพืชและพันธุ์)
            </button>
          )}
          {canCreate && (
            <button type="button" onClick={() => { setEditing(null); setCreating(true); }}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90">
              <Plus className="h-4 w-4" /> เพิ่มตัวเลือก
            </button>
          )}
        </div>
      </header>

      {/* Type tabs */}
      <div className="mt-4 flex flex-wrap gap-2">
        {MD_TYPES.map((t) => (
          <button key={t.type} type="button" onClick={() => setActiveType(t.type)}
            className={`rounded-full px-3 py-1.5 text-sm ${activeType === t.type ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/70'}`}>
            {t.label}
          </button>
        ))}
      </div>

      <section className="mt-4 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="px-4 py-2">ลำดับ</th>
                <th className="px-4 py-2">ค่า</th>
                {meta.parentType && <th className="px-4 py-2">อยู่ภายใต้</th>}
                <th className="px-4 py-2">สถานะ</th>
                <th className="px-4 py-2 text-right">จัดการ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.length === 0 && (
                <tr><td colSpan={meta.parentType ? 5 : 4} className="px-4 py-8 text-center text-muted-foreground">ไม่พบข้อมูล</td></tr>
              )}
              {items.map((m) => (
                <tr key={m.id} className="hover:bg-secondary/30">
                  <td className="px-4 py-2 text-muted-foreground">{m.orderIndex}</td>
                  <td className="px-4 py-2 font-medium">{m.value}</td>
                  {meta.parentType && <td className="px-4 py-2 text-muted-foreground">{m.parent ?? '—'}</td>}
                  <td className="px-4 py-2">
                    {canUpdate ? (
                      <button type="button" onClick={() => toggleM.mutate({ id: m.id, active: !m.active })}
                        disabled={toggleM.isPending}
                        className={`rounded-full px-2 py-0.5 text-xs ${m.active ? 'bg-success/15 text-success-readable' : 'bg-destructive/15 text-destructive'}`}>
                        {m.active ? 'ใช้งาน' : 'ปิด'}
                      </button>
                    ) : (
                      <span className={`rounded-full px-2 py-0.5 text-xs ${m.active ? 'bg-success/15 text-success-readable' : 'bg-destructive/15 text-destructive'}`}>
                        {m.active ? 'ใช้งาน' : 'ปิด'}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end">
                      {/* Round 8-25F — one ActionMenu trigger, matching the
                          Plots, Suppliers and RecordList tables. */}
                      <ActionMenu
                        ariaLabel={`ตัวเลือกเพิ่มเติมสำหรับ ${m.value}`}
                        items={[
                          ...(canUpdate ? [{
                            key: 'edit', label: 'แก้ไข', icon: Pencil,
                            onClick: () => { setCreating(false); setEditing(m); },
                          }] : []),
                          ...(canDelete ? [{
                            key: 'delete', label: 'ลบ', icon: Trash2, danger: true,
                            disabled: deleteM.isPending,
                            onClick: () => { if (confirm(`ลบ "${m.value}" ?`)) deleteM.mutate(m.id); },
                          }] : []),
                        ]}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {showForm && (
        <MasterDataModal
          item={editing}
          type={activeType}
          parentType={meta.parentType}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); qc.invalidateQueries({ queryKey: ['masterdata'] }); }}
        />
      )}

      {importOpen && (
        <MasterDataCropVarietyImportModal
          onClose={() => setImportOpen(false)}
          onImported={() => qc.invalidateQueries({ queryKey: ['masterdata'] })}
        />
      )}
    </div>
  );
}

// Round 8-22A — before this, a failed create/update showed `(err as
// Error)?.message`, which for an axios error is a generic string like
// "Request failed with status code 409" — never the backend's actual Thai
// detail message (e.g. the new duplicate-value 409). Same extraction shape
// as Plots.tsx's own plotMutationErrorMessage (independent local copy, same
// convention this codebase already uses for its other create/edit modals).
function masterDataMutationErrorMessage(error: unknown): string {
  if (!error) return '';
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: unknown } | string | undefined;
    if (typeof data === 'string') return data;
    if (typeof data?.detail === 'string') return data.detail;
    return error.message;
  }
  return error instanceof Error ? error.message : 'บันทึกไม่สำเร็จ';
}

function MasterDataModal({
  item, type, parentType, onClose, onSaved,
}: {
  item: MasterDataItem | null;
  type: string;
  parentType?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = item !== null;
  const [value, setValue] = useState(item?.value ?? '');
  const [parent, setParent] = useState<string | null>(item?.parent ?? null);
  const [orderIndex, setOrderIndex] = useState(item?.orderIndex ?? 0);
  const [error, setError] = useState('');

  const createM = useMutation({ mutationFn: createMasterData });
  const updateM = useMutation({
    mutationFn: ({ id, p }: { id: string; p: Parameters<typeof updateMasterData>[1] }) => updateMasterData(id, p),
  });

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (!value.trim()) { setError('กรุณาระบุค่า'); return; }
    try {
      if (isEdit) {
        await updateM.mutateAsync({ id: item!.id, p: { value: value.trim(), parent, orderIndex } });
      } else {
        await createM.mutateAsync({ type, value: value.trim(), parent, orderIndex });
      }
      onSaved();
    } catch (err) {
      setError(masterDataMutationErrorMessage(err) || 'บันทึกไม่สำเร็จ');
    }
  }

  const inputCls = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold">{isEdit ? 'แก้ไขตัวเลือก' : 'เพิ่มตัวเลือก'}</h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        <form onSubmit={onSubmit} className="space-y-4 px-6 py-5">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">ค่า *</label>
            <input value={value} onChange={(e) => setValue(e.target.value)} className={inputCls} autoFocus />
          </div>
          {parentType && (
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">อยู่ภายใต้ ({parentType})</label>
              <MasterDataSelect type={parentType} value={parent} onChange={setParent} />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">ลำดับ</label>
            <input type="number" value={orderIndex} onChange={(e) => setOrderIndex(Number(e.target.value))} className={inputCls} />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={createM.isPending || updateM.isPending}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">
              {(createM.isPending || updateM.isPending) && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEdit ? 'บันทึก' : 'สร้าง'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
