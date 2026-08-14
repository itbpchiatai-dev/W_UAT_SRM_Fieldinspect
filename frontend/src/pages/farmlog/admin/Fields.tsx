/**
 * Field Master (Step 12) — manage the schema-driven form field catalog.
 *
 * Core fields (is_core) are seed-managed: label / order / active are editable
 * but key / type are locked and they cannot be deleted. Their `required` flag
 * is NOT editable here — the record form hard-codes core-field requiredness in
 * code, so a toggle here would be decorative/misleading; it's hidden for core
 * (shown & enforced only for custom fields, which RecordForm validates via
 * validateField). Custom fields are admin-created and stored in
 * records.custom_fields.
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import {
  createFieldDefinition,
  deleteFieldDefinition,
  listFieldDefinitions,
  updateFieldDefinition,
  type FieldDefinition,
  type FieldType,
} from '../../../api/fielddefs';
import { useHasPermission } from '../../../hooks/useHasPermission';

// Types an admin may create as a custom field (Spec §7.2).
const CUSTOM_TYPES: FieldType[] = ['text', 'multiline', 'number', 'date', 'list', 'boolean'];

const TYPE_LABEL: Record<FieldType, string> = {
  text: 'ข้อความ', multiline: 'ข้อความหลายบรรทัด', number: 'ตัวเลข', date: 'วันที่',
  list: 'ตัวเลือก (dropdown)', boolean: 'ใช่/ไม่ใช่', score: 'คะแนน 1-10', percent: 'เปอร์เซ็นต์',
  photo: 'ภาพถ่าย', geo: 'พิกัด GPS', plot_picker: 'เลือกแปลง',
};

export function Fields() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<FieldDefinition | null>(null);
  const [creating, setCreating] = useState(false);

  const canCreate = useHasPermission('fielddefs.create');
  const canUpdate = useHasPermission('fielddefs.update');
  const canDelete = useHasPermission('fielddefs.delete');

  const { data: fields = [], isLoading } = useQuery({
    queryKey: ['fielddefs', 'all'],
    queryFn: () => listFieldDefinitions(false),
  });

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteFieldDefinition(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fielddefs'] }),
  });

  const toggleM = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      updateFieldDefinition(id, { active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['fielddefs'] }),
  });

  const showForm = creating || editing !== null;

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">Field Master</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            จัดการฟิลด์ของฟอร์มบันทึก — ฟิลด์หลัก (core) แก้ได้บางส่วน · เพิ่ม custom field ได้
          </p>
        </div>
        {canCreate && (
          <button
            type="button"
            onClick={() => { setEditing(null); setCreating(true); }}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
          >
            <Plus className="h-4 w-4" />
            เพิ่ม custom field
          </button>
        )}
      </header>

      <section className="mt-4 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="px-4 py-2">ลำดับ</th>
                <th className="px-4 py-2">Key</th>
                <th className="px-4 py-2">ป้ายชื่อ</th>
                <th className="px-4 py-2">ชนิด</th>
                <th className="px-4 py-2">ประเภท</th>
                <th className="px-4 py-2">บังคับ</th>
                <th className="px-4 py-2">สถานะ</th>
                <th className="px-4 py-2 text-right">จัดการ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {fields.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-muted-foreground">ไม่พบข้อมูล</td></tr>
              )}
              {fields.map((f) => (
                <tr key={f.id} className="hover:bg-secondary/30">
                  <td className="px-4 py-2 text-muted-foreground">{f.orderIndex}</td>
                  <td className="px-4 py-2 font-mono text-xs">{f.key}</td>
                  <td className="px-4 py-2 font-medium">{f.label}</td>
                  <td className="px-4 py-2 text-muted-foreground">{TYPE_LABEL[f.fieldType] ?? f.fieldType}</td>
                  <td className="px-4 py-2">
                    {f.isCore
                      ? <span className="rounded-full bg-secondary px-2 py-0.5 text-xs">core</span>
                      : <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">custom</span>}
                  </td>
                  <td className="px-4 py-2">
                    {f.isCore ? (
                      // Core requiredness is enforced in RecordForm's code, not
                      // from this row — show it read-only so no one thinks
                      // toggling it here does anything.
                      <span
                        className="text-muted-foreground"
                        title="ฟิลด์หลักถูกกำหนดโดยระบบ — แก้ที่นี่ไม่ได้"
                      >
                        {f.required ? '✓ (ระบบ)' : '— (ระบบ)'}
                      </span>
                    ) : (
                      f.required ? '✓' : '—'
                    )}
                  </td>
                  <td className="px-4 py-2">
                    {canUpdate ? (
                      <button
                        type="button"
                        onClick={() => toggleM.mutate({ id: f.id, active: !f.active })}
                        disabled={toggleM.isPending}
                        className={`rounded-full px-2 py-0.5 text-xs ${f.active ? 'bg-success/15 text-success-readable' : 'bg-destructive/15 text-destructive'}`}
                      >
                        {f.active ? 'ใช้งาน' : 'ปิด'}
                      </button>
                    ) : (
                      <span className={`rounded-full px-2 py-0.5 text-xs ${f.active ? 'bg-success/15 text-success-readable' : 'bg-destructive/15 text-destructive'}`}>
                        {f.active ? 'ใช้งาน' : 'ปิด'}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      {canUpdate && (
                        <button
                          type="button"
                          onClick={() => { setCreating(false); setEditing(f); }}
                          className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
                          title="แก้ไข"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                      )}
                      {canDelete && !f.isCore && (
                        <button
                          type="button"
                          onClick={() => { if (confirm(`ลบ custom field "${f.label}" ?`)) deleteM.mutate(f.id); }}
                          disabled={deleteM.isPending}
                          className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                          title="ลบ"
                        >
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
        <FieldModal
          field={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            qc.invalidateQueries({ queryKey: ['fielddefs'] });
          }}
        />
      )}
    </div>
  );
}

const fieldSchema = z.object({
  key: z.string().regex(/^[a-z][a-z0-9_]*$/, 'key ต้องเป็น snake_case ASCII').max(64),
  label: z.string().min(1, 'กรุณาระบุป้ายชื่อ').max(255),
  fieldType: z.enum(['text', 'multiline', 'number', 'date', 'list', 'boolean']),
  required: z.boolean().default(false),
  optionsText: z.string().optional(),
  orderIndex: z.number().int().min(0).default(0),
});
type FieldFormValues = z.infer<typeof fieldSchema>;

function FieldModal({
  field,
  onClose,
  onSaved,
}: {
  field: FieldDefinition | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = field !== null;
  const isCoreEdit = isEdit && field!.isCore;

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FieldFormValues>({
    resolver: zodResolver(fieldSchema),
    defaultValues: field
      ? {
          key: field.key,
          label: field.label,
          fieldType: (CUSTOM_TYPES.includes(field.fieldType) ? field.fieldType : 'text') as FieldFormValues['fieldType'],
          required: field.required,
          optionsText: field.options.join(', '),
          orderIndex: field.orderIndex,
        }
      : { key: '', label: '', fieldType: 'text', required: false, optionsText: '', orderIndex: 100 },
  });

  const selectedType = watch('fieldType');

  const createM = useMutation({ mutationFn: createFieldDefinition });
  const updateM = useMutation({
    mutationFn: ({ id, p }: { id: string; p: Parameters<typeof updateFieldDefinition>[1] }) =>
      updateFieldDefinition(id, p),
  });

  async function onSubmit(values: FieldFormValues) {
    const options = values.fieldType === 'list'
      ? (values.optionsText ?? '').split(',').map((s) => s.trim()).filter(Boolean)
      : [];
    if (isEdit) {
      // key / type are immutable — only mutable attrs are sent. For core
      // fields `required` is system-managed (the record form hard-codes it),
      // so we deliberately omit it from the update to avoid a decorative edit.
      await updateM.mutateAsync({
        id: field!.id,
        p: isCoreEdit
          ? { label: values.label, options, orderIndex: values.orderIndex }
          : { label: values.label, required: values.required, options, orderIndex: values.orderIndex },
      });
    } else {
      await createM.mutateAsync({
        key: values.key,
        label: values.label,
        fieldType: values.fieldType,
        required: values.required,
        options,
        orderIndex: values.orderIndex,
      });
    }
    onSaved();
  }

  const inputCls = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
  const err = (createM.error || updateM.error) as Error | null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold">{isEdit ? `แก้ไขฟิลด์: ${field!.label}` : 'เพิ่ม custom field'}</h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 px-6 py-5">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">Key (slug) *</label>
              <input {...register('key')} disabled={isEdit} className={`${inputCls} font-mono`} placeholder="soil_ph" />
              {errors.key && <p className="text-xs text-destructive">{errors.key.message}</p>}
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">ชนิดฟิลด์ *</label>
              <select {...register('fieldType')} disabled={isEdit} className={inputCls}>
                {CUSTOM_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-muted-foreground">ป้ายชื่อ (label) *</label>
            <input {...register('label')} className={inputCls} placeholder="ค่า pH ดิน" />
            {errors.label && <p className="text-xs text-destructive">{errors.label.message}</p>}
          </div>

          {selectedType === 'list' && (
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">ตัวเลือก (คั่นด้วย ,)</label>
              <input {...register('optionsText')} className={inputCls} placeholder="ตัวเลือก1, ตัวเลือก2" />
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">ลำดับ</label>
              <input type="number" {...register('orderIndex', { valueAsNumber: true })} className={inputCls} />
            </div>
            {/* "บังคับกรอก" applies only to custom fields — core requiredness is
                enforced in the record form's code, so editing it here would be
                decorative. Hide it for core edits. */}
            {isCoreEdit ? (
              <div className="flex flex-col justify-end pb-2">
                <p className="text-xs text-muted-foreground">
                  การบังคับกรอกของฟิลด์หลักถูกกำหนดโดยระบบ
                </p>
              </div>
            ) : (
              <label className="flex items-center gap-2 pt-6 cursor-pointer">
                <input type="checkbox" {...register('required')} className="h-4 w-4 rounded border-input text-primary" />
                <span className="text-sm">บังคับกรอก</span>
              </label>
            )}
          </div>

          {err && <p className="text-sm text-destructive">{err.message}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEdit ? 'บันทึก' : 'สร้าง'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
