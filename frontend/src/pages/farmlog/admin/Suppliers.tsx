/**
 * Suppliers — paginated list + create / edit / deactivate.
 *
 * Round 8-20B adds the Excel import entry points (backend round 8-20A):
 * "ดาวน์โหลด Template" and "นำเข้า Excel", both gated on suppliers.read. The
 * BACKEND is the authority on per-row create/update permission — this page
 * never pre-judges which rows a caller may execute (see
 * SupplierImportModal).
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FileSpreadsheet, Loader2, Pencil, Phone, Plus, PowerOff, Search, User, X } from 'lucide-react';
import {
  createSupplier,
  deactivateSupplier,
  downloadSupplierImportTemplate,
  getSupplier,
  searchSuppliers,
  SupplierImportReportError,
  updateSupplier,
  type SupplierCreatePayload,
  type SupplierStatusFilter,
  type SupplierSummary,
} from '../../../api/suppliers';
import { SupplierImportModal } from '../../../components/farmlog/SupplierImportModal';
import { downloadBlob } from '../../../lib/downloadBlob';
import { useHasPermission } from '../../../hooks/useHasPermission';
import { fetchAllPages } from '../../../lib/paginate';

// Round 8-25D — this page used to be fixed at 20 rows/page with no way to
// see more. Same [100, 200, 500, 'ทั้งหมด'] contract as the Plots admin page.
const PAGE_SIZE_OPTIONS = [100, 200, 500, 'all'] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];
const DEFAULT_PAGE_SIZE: PageSize = 100;
const ALL_FETCH_CHUNK = 200;

// Round 8-20D — contact-number fragment bounds. The lower bound keeps a 1-3
// digit fragment (which would match a large share of every number in scope)
// from being a usable enumeration probe; the upper bound is a full Thai
// mobile. The BACKEND re-checks both by hand — this is the immediate-feedback
// copy, never the security boundary.
const MIN_CONTACT_PHONE_DIGITS = 4;
const MAX_CONTACT_PHONE_DIGITS = 10;
const CONTACT_PHONE_FRAGMENT_RE = new RegExp(
  `^[0-9]{${MIN_CONTACT_PHONE_DIGITS},${MAX_CONTACT_PHONE_DIGITS}}$`,
);

// "ใช้งาน" is the baseline the page opens on and the one ล้างค่า restores —
// one named constant so the default, the reset, and the
// "is this narrowed away from baseline" check can never drift apart.
const DEFAULT_SUPPLIER_STATUS: SupplierStatusFilter = 'active';

const supplierSchema = z.object({
  code: z.string().min(1, 'กรุณาระบุรหัส').max(50),
  name: z.string().min(1, 'กรุณาระบุชื่อ').max(255),
  taxId: z.string().max(20).optional().or(z.literal('')),
  contactName: z.string().max(255).optional().or(z.literal('')),
  contactEmail: z.string().email('อีเมลไม่ถูกต้อง').optional().or(z.literal('')),
  contactPhone: z.string().max(50).optional().or(z.literal('')),
  address: z.string().optional().or(z.literal('')),
});
type SupplierFormValues = z.infer<typeof supplierSchema>;

export function Suppliers() {
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  // Round 8-20D — DRAFT (what is typed) vs APPLIED (what the query uses).
  // Nothing is queried until applyFilters runs, so a half-typed value never
  // fires a request. The pre-8-20D page applied `q` on every keystroke; the
  // draft/applied split is the same pattern the Plots page uses.
  const [qText, setQText] = useState('');
  const [contactNameText, setContactNameText] = useState('');
  const [phoneText, setPhoneText] = useState('');
  const [statusDraft, setStatusDraft] = useState<SupplierStatusFilter>(DEFAULT_SUPPLIER_STATUS);

  const [q, setQ] = useState('');
  const [appliedContactName, setAppliedContactName] = useState('');
  // The applied contact-number fragment. Held in state so the request can
  // carry it, but NEVER placed in the React Query key (see the query below).
  const [appliedPhoneDigits, setAppliedPhoneDigits] = useState('');
  const [appliedStatus, setAppliedStatus] = useState<SupplierStatusFilter>(DEFAULT_SUPPLIER_STATUS);
  // A plain counter bumped on every applied search — the query-key
  // discriminator for the phone filter. It replaces any phone-derived value
  // (raw or hashed) so the key can carry NEITHER the number NOR anything
  // reversible back to it. Same nonce pattern the Plots page uses.
  const [searchNonce, setSearchNonce] = useState(0);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [templateError, setTemplateError] = useState<string | null>(null);

  const canCreate = useHasPermission('suppliers.create');
  const canUpdate = useHasPermission('suppliers.update');
  const canDelete = useHasPermission('suppliers.delete');
  // Round 8-20B — downloading the template and OPENING the import UI need
  // only suppliers.read. Whether any individual row may actually be created
  // or updated is decided by the backend, per row, at Preview/Commit time —
  // never guessed here.
  const canImport = useHasPermission('suppliers.read');

  const { data: suppliers = [], isLoading } = useQuery({
    // The applied contact-number fragment is DELIBERATELY absent from this
    // key — `searchNonce` stands in for it. A query key is visible to React
    // Query devtools and any cache inspection, so it must never carry the
    // number or a hash of it. The nonce also guarantees that re-applying the
    // SAME filters refetches instead of serving a stale cached page.
    queryKey: ['suppliers', page, pageSize, q, appliedContactName, appliedStatus, searchNonce],
    queryFn: () => {
      const filters = {
        q: q || undefined,
        contactName: appliedContactName || undefined,
        contactPhoneDigits: appliedPhoneDigits || undefined,
        status: appliedStatus,
      };
      if (pageSize === 'all') {
        return fetchAllPages(
          (offset, limit) => searchSuppliers({ ...filters, limit, offset }),
          ALL_FETCH_CHUNK,
        );
      }
      return searchSuppliers({ ...filters, limit: pageSize, offset: page * pageSize });
    },
  });

  const hasActiveFilters =
    qText.trim() !== '' || contactNameText.trim() !== '' || phoneText.trim() !== ''
    || statusDraft !== DEFAULT_SUPPLIER_STATUS
    || q !== '' || appliedContactName !== '' || appliedPhoneDigits !== ''
    || appliedStatus !== DEFAULT_SUPPLIER_STATUS;

  /** Round 8-20D — promote the draft inputs to applied filters. Any change to
   * a filter returns to page 0; paging afterwards keeps the applied set. */
  function applyFilters() {
    const phone = phoneText.trim();
    if (phone && !CONTACT_PHONE_FRAGMENT_RE.test(phone)) {
      // Generic message, and NOTHING is sent — not even the other filters,
      // which would otherwise show a wider result set than was asked for.
      setFilterError(
        `กรุณากรอกหมายเลขติดต่อเป็นตัวเลข ${MIN_CONTACT_PHONE_DIGITS}-${MAX_CONTACT_PHONE_DIGITS} หลัก`,
      );
      return;
    }
    setFilterError(null);
    setPage(0);
    setQ(qText.trim());
    setAppliedContactName(contactNameText.trim());
    setAppliedPhoneDigits(phone);
    setAppliedStatus(statusDraft);
    setSearchNonce((n) => n + 1);
  }

  function clearFilters() {
    setFilterError(null);
    setPage(0);
    setQText('');
    setContactNameText('');
    setPhoneText('');
    setStatusDraft(DEFAULT_SUPPLIER_STATUS);
    setQ('');
    setAppliedContactName('');
    setAppliedPhoneDigits('');
    setAppliedStatus(DEFAULT_SUPPLIER_STATUS);
    setSearchNonce((n) => n + 1);
  }

  const deactivateM = useMutation({
    mutationFn: (id: string) => deactivateSupplier(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suppliers'] }),
  });

  // Round 8-20B — read-only download; never mutates.
  const templateM = useMutation({
    mutationFn: () => downloadSupplierImportTemplate(),
    onSuccess: ({ blob, filename }) => {
      downloadBlob(blob, filename);
      setTemplateError(null);
    },
    onError: (error) => {
      setTemplateError(
        error instanceof SupplierImportReportError
          ? error.message
          : 'ดาวน์โหลด Template ไม่สำเร็จ',
      );
    },
  });

  /** Round 8-20B — a commit can create/update/activate/deactivate any number
   * of Suppliers, so refresh the list AND every cached supplier detail
   * (['supplier', id]) rather than trying to name the affected ids. Same
   * invalidate-the-family pattern the rest of this app uses. */
  function handleImported() {
    qc.invalidateQueries({ queryKey: ['suppliers'] });
    qc.invalidateQueries({ queryKey: ['supplier'] });
  }

  const showForm = creating || editingId !== null;

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">Suppliers</h1>
          <p className="mt-1 text-sm text-muted-foreground">จัดการข้อมูล Supplier ทั้งหมด</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canImport && (
            <>
              <button
                type="button"
                onClick={() => templateM.mutate()}
                disabled={templateM.isPending}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary disabled:opacity-60"
              >
                {templateM.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                ดาวน์โหลด Template
              </button>
              <button
                type="button"
                onClick={() => { setTemplateError(null); setImporting(true); }}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-colors hover:bg-secondary"
              >
                <FileSpreadsheet className="h-4 w-4" />
                นำเข้า Excel
              </button>
            </>
          )}
          {canCreate && (
            <button
              type="button"
              onClick={() => { setEditingId(null); setCreating(true); }}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              เพิ่ม Supplier
            </button>
          )}
        </div>
      </header>

      {templateError && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span className="min-w-0 break-words">{templateError}</span>
        </div>
      )}

      {/* Round 8-20D — one responsive filter row. Inputs are DRAFT state;
          nothing is applied until "ค้นหา" (or Enter) fires applyFilters, so a
          half-typed value never triggers a request. */}
      <div className="mt-4 rounded-lg border border-border bg-card p-3 shadow-sm">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">ชื่อหรือรหัส Supplier</span>
            <span className="relative flex items-center">
              <Search className="absolute left-2 h-4 w-4 text-muted-foreground" />
              <input
                type="search"
                value={qText}
                onChange={(e) => { setQText(e.target.value); setFilterError(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') applyFilters(); }}
                placeholder="ค้นหาชื่อหรือรหัส..."
                className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </span>
          </label>

          <label className="flex flex-1 flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">ชื่อผู้ติดต่อ</span>
            <span className="relative flex items-center">
              <User className="absolute left-2 h-4 w-4 text-muted-foreground" />
              <input
                type="search"
                value={contactNameText}
                onChange={(e) => { setContactNameText(e.target.value); setFilterError(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') applyFilters(); }}
                placeholder="ค้นหาบางส่วนได้ เช่น สมชาย"
                className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </span>
          </label>

          {/* Digits only, stripped as typed, so the box can never hold a value
              the backend would reject (and no '%'/'_' can reach a LIKE
              pattern). autoComplete off: this is a lookup key, never the
              user's own number. */}
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">หมายเลขติดต่อ</span>
            <span className="relative flex items-center">
              <Phone className="absolute left-2 h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                inputMode="numeric"
                autoComplete="off"
                maxLength={MAX_CONTACT_PHONE_DIGITS}
                value={phoneText}
                onChange={(e) => {
                  setPhoneText(e.target.value.replace(/\D/g, '').slice(0, MAX_CONTACT_PHONE_DIGITS));
                  setFilterError(null);
                }}
                onKeyDown={(e) => { if (e.key === 'Enter') applyFilters(); }}
                placeholder={`ค้นหาบางส่วนได้ ${MIN_CONTACT_PHONE_DIGITS}-${MAX_CONTACT_PHONE_DIGITS} หลัก`}
                aria-invalid={filterError ? true : undefined}
                className="w-full rounded-md border border-input bg-background py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted-foreground">สถานะ</span>
            <select
              value={statusDraft}
              onChange={(e) => { setStatusDraft(e.target.value as SupplierStatusFilter); setFilterError(null); }}
              aria-label="สถานะ"
              className="min-w-[140px] rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="active">ใช้งาน</option>
              <option value="inactive">ปิดใช้งาน</option>
              <option value="all">ทั้งหมด</option>
            </select>
          </label>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={applyFilters}
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

        {/* Generic message — never echoes the digits the user typed. */}
        {filterError && (
          <p role="alert" className="mt-2 text-sm text-destructive">{filterError}</p>
        )}
        {appliedPhoneDigits && !filterError && (
          // Deliberately does NOT echo the fragment back on screen.
          <p className="mt-2 text-sm text-muted-foreground">
            กำลังกรองตามหมายเลขติดต่อที่ระบุ
          </p>
        )}
      </div>

      <section className="mt-4 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-secondary/50 text-left text-sm font-semibold text-muted-foreground">
              <tr>
                <th className="px-4 py-2">รหัส</th>
                <th className="px-4 py-2">ชื่อ Supplier</th>
                <th className="px-4 py-2">ผู้ติดต่อ</th>
                <th className="px-4 py-2">อีเมล</th>
                <th className="px-4 py-2">สถานะ</th>
                <th className="px-4 py-2 text-right">จัดการ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {suppliers.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                    ไม่พบข้อมูล
                  </td>
                </tr>
              )}
              {suppliers.map((s: SupplierSummary) => (
                <tr key={s.id} className="hover:bg-secondary/30">
                  <td className="px-4 py-2 font-mono">{s.code}</td>
                  <td className="px-4 py-2 font-medium">{s.name}</td>
                  <td className="px-4 py-2 text-muted-foreground">{s.contactName ?? '—'}</td>
                  <td className="px-4 py-2 text-muted-foreground">{s.contactEmail ?? '—'}</td>
                  <td className="px-4 py-2">
                    {s.isActive ? (
                      <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs text-success-readable">ใช้งาน</span>
                    ) : (
                      <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-xs text-destructive">ปิดแล้ว</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      {canUpdate && (
                        <button
                          type="button"
                          onClick={() => { setCreating(false); setEditingId(s.id); }}
                          className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
                          title="แก้ไข"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                      )}
                      {canDelete && s.isActive && (
                        <button
                          type="button"
                          onClick={() => {
                            if (confirm(`ปิดการใช้งาน "${s.name}" ?`)) deactivateM.mutate(s.id);
                          }}
                          disabled={deactivateM.isPending}
                          className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                          title="ปิดการใช้งาน"
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
      </section>

      <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <label htmlFor="suppliers-page-size">แสดง</label>
          <select
            id="suppliers-page-size"
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
          <span>{suppliers.length} suppliers</span>
        ) : (
          <div className="flex items-center gap-4">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="disabled:opacity-40"
            >
              ← ก่อนหน้า
            </button>
            <span>หน้า {page + 1}</span>
            <button
              type="button"
              disabled={suppliers.length < pageSize}
              onClick={() => setPage((p) => p + 1)}
              className="disabled:opacity-40"
            >
              ถัดไป →
            </button>
          </div>
        )}
      </div>

      {/* Round 8-20B — a sibling of the edit modal, never nested inside it
          or inside a card. `key` on the mount is what guarantees a closed-
          then-reopened modal starts completely fresh: React unmounts the
          old instance, so no file/preview/previewState can survive. */}
      {importing && (
        <SupplierImportModal
          onClose={() => setImporting(false)}
          onImported={handleImported}
        />
      )}

      {showForm && (
        <SupplierModal
          supplierId={editingId}
          onClose={() => { setCreating(false); setEditingId(null); }}
          onSaved={() => {
            setCreating(false);
            setEditingId(null);
            qc.invalidateQueries({ queryKey: ['suppliers'] });
          }}
        />
      )}
    </div>
  );
}


function SupplierModal({
  supplierId,
  onClose,
  onSaved,
}: {
  supplierId: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = supplierId !== null;

  const { data: existing, isLoading: loadingExisting } = useQuery({
    queryKey: ['supplier', supplierId],
    queryFn: () => getSupplier(supplierId!),
    enabled: isEdit,
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<SupplierFormValues>({
    resolver: zodResolver(supplierSchema),
    values: existing
      ? {
          code: existing.code,
          name: existing.name,
          taxId: existing.taxId ?? '',
          contactName: existing.contactName ?? '',
          contactEmail: existing.contactEmail ?? '',
          contactPhone: existing.contactPhone ?? '',
          address: existing.address ?? '',
        }
      : undefined,
  });

  const createM = useMutation({ mutationFn: (p: SupplierCreatePayload) => createSupplier(p) });
  const updateM = useMutation({ mutationFn: ({ id, p }: { id: string; p: SupplierCreatePayload }) => updateSupplier(id, p) });

  async function onSubmit(values: SupplierFormValues) {
    const payload = {
      code: values.code,
      name: values.name,
      taxId: values.taxId || null,
      contactName: values.contactName || null,
      contactEmail: values.contactEmail || null,
      contactPhone: values.contactPhone || null,
      address: values.address || null,
    };
    if (isEdit) {
      await updateM.mutateAsync({ id: supplierId!, p: payload });
    } else {
      await createM.mutateAsync(payload);
    }
    reset();
    onSaved();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-lg rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold">
            {isEdit ? 'แก้ไข Supplier' : 'เพิ่ม Supplier ใหม่'}
          </h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        {loadingExisting ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 px-6 py-5">
            <div className="grid grid-cols-2 gap-4">
              <Field label="รหัส Supplier *" error={errors.code?.message}>
                <input
                  {...register('code')}
                  disabled={isEdit}
                  className="field-input uppercase"
                  placeholder="SUP001"
                />
              </Field>
              <Field label="ชื่อ Supplier *" error={errors.name?.message} className="col-span-1">
                <input {...register('name')} className="field-input" placeholder="บริษัท ..." />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Field label="เลขที่ผู้เสียภาษี" error={errors.taxId?.message}>
                <input {...register('taxId')} className="field-input" placeholder="0123456789012" />
              </Field>
              <Field label="ชื่อผู้ติดต่อ" error={errors.contactName?.message}>
                <input {...register('contactName')} className="field-input" />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Field label="อีเมลผู้ติดต่อ" error={errors.contactEmail?.message}>
                <input {...register('contactEmail')} type="email" className="field-input" />
              </Field>
              <Field label="เบอร์โทร" error={errors.contactPhone?.message}>
                <input {...register('contactPhone')} className="field-input" />
              </Field>
            </div>

            <Field label="ที่อยู่" error={errors.address?.message}>
              <textarea {...register('address')} rows={2} className="field-input resize-none" />
            </Field>

            {(createM.error || updateM.error) && (
              <p className="text-sm text-destructive">
                {(createM.error as Error)?.message || (updateM.error as Error)?.message}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
                ยกเลิก
              </button>
              <button
                type="submit"
                disabled={isSubmitting}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                {isEdit ? 'บันทึก' : 'สร้าง'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  error,
  children,
  className,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-1 ${className ?? ''}`}>
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
