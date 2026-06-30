/**
 * Master Data admin (Step 12.5) — manage dropdown options per type.
 * Options edited here appear immediately in the record form's selects.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import {
  createMasterData,
  deleteMasterData,
  listMasterData,
  updateMasterData,
  type MasterDataItem,
} from '../../../api/masterdata';
import { MasterDataSelect } from '../../../components/farmlog/MasterDataSelect';
import { useHasPermission } from '../../../hooks/useHasPermission';

const MD_TYPES: { type: string; label: string; parentType?: string }[] = [
  { type: 'crop', label: 'ชนิดพืช' },
  { type: 'variety', label: 'พันธุ์/สายพันธุ์', parentType: 'crop' },
  { type: 'growth_stage', label: 'ระยะการเจริญเติบโต' },
  { type: 'weather', label: 'สภาพอากาศ' },
  { type: 'level', label: 'ระดับ (เตรียม/ดูแล)' },
  { type: 'severity', label: 'ระดับความรุนแรง' },
  { type: 'irrigation', label: 'การให้น้ำ' },
  { type: 'fertilizer', label: 'ปุ๋ย' },
];

export function MasterData() {
  const qc = useQueryClient();
  const [activeType, setActiveType] = useState('crop');
  const [editing, setEditing] = useState<MasterDataItem | null>(null);
  const [creating, setCreating] = useState(false);

  const canCreate = useHasPermission('masterdata.create');
  const canUpdate = useHasPermission('masterdata.update');
  const canDelete = useHasPermission('masterdata.delete');

  const meta = MD_TYPES.find((t) => t.type === activeType)!;

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['masterdata', activeType, null],
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
        {canCreate && (
          <button type="button" onClick={() => { setEditing(null); setCreating(true); }}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90">
            <Plus className="h-4 w-4" /> เพิ่มตัวเลือก
          </button>
        )}
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
                    <div className="flex justify-end gap-1">
                      {canUpdate && (
                        <button type="button" onClick={() => { setCreating(false); setEditing(m); }}
                          className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground" title="แก้ไข">
                          <Pencil className="h-4 w-4" />
                        </button>
                      )}
                      {canDelete && (
                        <button type="button" onClick={() => { if (confirm(`ลบ "${m.value}" ?`)) deleteM.mutate(m.id); }}
                          disabled={deleteM.isPending}
                          className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50" title="ลบ">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
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
    </div>
  );
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
      setError((err as Error)?.message || 'บันทึกไม่สำเร็จ');
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
