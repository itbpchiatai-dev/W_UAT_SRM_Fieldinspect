/**
 * Plot cycle lifecycle modals (round 7.3) — start a new รอบปลูก, edit the
 * active cycle's plan, or close it (harvested/cancelled). Mirrors the form
 * style/validation of Plots.tsx's PlotModal (sticky footer, same
 * expectedYieldFull↔expectedYieldUnit pairing rule) since these edit the
 * same plan fields, just cycle-scoped instead of plot-scoped.
 */
import axios from 'axios';
import { useEffect, useRef, useState } from 'react';
import { useForm } from 'react-hook-form';
import type { UseFormRegister, UseFormWatch, UseFormSetValue, FieldErrors, Path } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Archive, Loader2, Lock, Pencil, PowerOff, RefreshCw, Sprout, Unlock } from 'lucide-react';
import {
  createPlotCycle,
  updatePlotCycle,
  closePlotCycle,
  getPlotCycleClosePreview,
  deactivatePlot,
  rolloverPlotCycle,
  reactivatePlot,
  reactivatePlotWithCycle,
  type PlotCycle,
  type PlotCycleCreatePayload,
  type PlotCycleUpdatePayload,
  type PlotCycleClosePayload,
  type PlotCycleRolloverPayload,
} from '../../api/plots';
import { MasterDataSelect } from './MasterDataSelect';
import { listMasterData, masterDataQueryKey } from '../../api/masterdata';
import { YIELD_UNIT_OPTIONS, formatYieldQuantity } from '../../lib/yield-planning';

const optionalNumberInput = z.preprocess(
  (value) => (value === '' || value === undefined ? undefined : value),
  z.coerce.number().min(0).optional(),
);

// Plan fields shared by every cycle form (Start/Edit/Rollover/Create-plot).
// Round 8-5B split the schema by MODE because pCode requiredness differs;
// round 8-13B made poNumber optional in BOTH modes (it was required on create
// through round 8-12C); round A removed the lot-mode selector from both:
//   - CREATE (cyclePlanFields): poNumber OPTIONAL (blank = no PO); pCode
//     REQUIRED nonblank. Used by Start, Rollover.newCycle, and Plots.tsx's
//     Create Plot "รอบปลูกแรก".
//   - EDIT (cycleEditPlanFields): poNumber + pCode OPTIONAL. pCode blank =
//     preserve (unchanged); poNumber blank = CLEAR the stored PO — see
//     toEditPayload, which always sends poNumber (never omits it) for
//     exactly this reason.
// Only the field DEFS + the refine rule are shared; each modal's zod object
// still adds its own top-level fields (status, closeStatus, etc).
const planCoreFields = {
  crop: z.string().max(100).optional().or(z.literal('')),
  // Round 8-26C — still optional HERE, and made required for CREATE only by
  // cyclePlanFields below. Edit keeps it optional so a legacy cycle that was
  // saved without a variety stays editable (its P.Code is preserved by the
  // blank-means-keep rule, so nothing forces the user to fill this in).
  variety: z.string().max(100).optional().or(z.literal('')),
  cycleLabel: z.string().max(100).optional().or(z.literal('')),
  // Round 8-12A — the SUPPLIER's own lot number. Always optional, in every
  // mode: it never feeds the Auto Lot formula, so requiring it would block a
  // cycle for data the system does not need. Round A — it is also the ONLY
  // lot field on this form: the system Lot No is generated server-side and
  // has no input at all.
  supplierLotNo: z.string().max(100).optional().or(z.literal('')),
  // Round 8-21A/8-21B — three independent, OPTIONAL back-office reference
  // fields, same "always optional in every mode" rule as supplierLotNo:
  // none of them feeds the Auto Lot formula or any other business logic.
  oracleSupplierCode: z.string().max(255).optional().or(z.literal('')),
  oracleInvoice: z.string().max(255).optional().or(z.literal('')),
  refAccount: z.string().max(255).optional().or(z.literal('')),
  plantingDate: z.string().optional().or(z.literal('')),
  plantCount: optionalNumberInput,
  expectedYieldFull: optionalNumberInput,
  expectedYieldUnit: z.string().max(20).optional().or(z.literal('')),
};

export const cyclePlanFields = {
  // Round 8-13A/B — optional: blank/whitespace is a valid "no PO" value, not
  // an error. pCode stays required nonblank.
  poNumber: z.string().max(100).optional().or(z.literal('')),
  pCode: z.string().trim().min(1, 'กรุณากรอก P.Code').max(100),
  ...planCoreFields,
  // Round 8-26C — variety is REQUIRED when CREATING a cycle, overriding
  // planCoreFields above. P.Code is required on create and is now derived
  // from the variety (a variety owns exactly one active P.Code), so without
  // a variety there is no P.Code to derive and the cycle cannot be created
  // at all. Confirmed with the user as an accepted consequence.
  variety: z.string().trim().min(1, 'กรุณาเลือกพันธุ์').max(100),
};

export const cycleEditPlanFields = {
  poNumber: z.string().max(100).optional().or(z.literal('')),
  pCode: z.string().max(100).optional().or(z.literal('')),
  ...planCoreFields,
};

export function requireUnitWithYield(
  values: { expectedYieldFull?: number; expectedYieldUnit?: string },
  ctx: z.RefinementCtx,
) {
  const hasFull = values.expectedYieldFull !== undefined;
  const unitBlank = !values.expectedYieldUnit || values.expectedYieldUnit.trim() === '';
  if (hasFull && unitBlank) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['expectedYieldUnit'],
      message: 'กรุณาระบุหน่วย (เช่น kg) เมื่อกรอกเป้าผลิต',
    });
  }
}

/**
 * Round 8-17A.1 — cycleLabel is required on EVERY cycle form submit,
 * independent of Auto vs Manual lot (mirrors the backend's
 * PlotCycleCreate._require_cycle_label / update_plot_cycle's clear-block —
 * see app/schemas/plot.py and app/api/v1/plots.py). Shared by both
 * refineCyclePlan (create/start/rollover) and refineEditCyclePlan (edit,
 * where it also covers "legacy null must be filled in before saving" since
 * a blank field fails this the same way a never-set one does) so the rule
 * lives in exactly one place.
 */
export function requireCycleLabel(
  values: { cycleLabel?: string },
  ctx: z.RefinementCtx,
) {
  const label = values.cycleLabel?.trim() || '';
  if (!label) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['cycleLabel'],
      message: 'กรุณาระบุชื่อรอบปลูก เนื่องจากใช้ระบุรอบและสร้าง Lot No อัตโนมัติ',
    });
  }
}

/** Round 8-12A.1 — the Auto Lot components the BACKEND requires to generate a
 * lot ({cycleLabel}-{supplierCode}-{pCode}-{running}). The supplier code is
 * resolved server-side and is never a form field, so the form can only check
 * the one the user actually types here. Blocking means the user sees which
 * field is missing instead of a 422 after submit.
 *
 * cycleLabel is deliberately NOT checked here (round 8-17A.1):
 * requireCycleLabel above already requires it unconditionally on every
 * submit, so a second check on the same field would only duplicate the issue.
 *
 * Round A — this no longer depends on a lot mode. A new cycle ALWAYS gets a
 * generated lot, so its components are always required; and an edit never
 * regenerates one, so `effective` exists only to let EDIT fall back to the
 * cycle's stored pCode when the user never retyped it. */
export function requireAutoLotComponents(
  values: { pCode?: string },
  ctx: z.RefinementCtx,
  effective?: { pCode?: string | null },
) {
  const code = values.pCode?.trim() || effective?.pCode?.trim() || '';
  if (!code) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ['pCode'],
      message: 'กรุณากรอก P.Code ก่อนสร้าง Lot No ระบบอัตโนมัติ',
    });
  }
}

// Full plan refine — the yield-unit rule PLUS the lot-component rules.
// Round 8-12B — the generated lot requires cycleLabel + P.Code, mirroring the
// backend's own rule. Round A dropped the Manual-lot rule with the mode
// selector itself: there is no hand-typed Lot No to validate any more.
export function refineCyclePlan(
  values: {
    expectedYieldFull?: number; expectedYieldUnit?: string;
    cycleLabel?: string; pCode?: string;
  },
  ctx: z.RefinementCtx,
) {
  requireUnitWithYield(values, ctx);
  requireCycleLabel(values, ctx);
  requireAutoLotComponents(values, ctx);
}

/** EDIT-only refine. Round 8-5B.1 required a PO to regenerate an Auto Lot;
 * round 8-12A dropped the PO from the formula entirely, and round A dropped
 * regeneration altogether — an edit never touches the lot.
 *
 * That is why this is no longer just refineCyclePlan: with no lot to mint, the
 * Auto components stop being an edit-time requirement. cycleLabel is still
 * required (it identifies the cycle — round 8-17A.1), but a blank P.Code goes
 * back to meaning "preserve whatever is stored", which is what keeps a legacy
 * cycle that never had one editable at all. */
export function refineEditCyclePlan(
  values: {
    expectedYieldFull?: number; expectedYieldUnit?: string; poNumber?: string;
    cycleLabel?: string; pCode?: string;
  },
  ctx: z.RefinementCtx,
) {
  requireUnitWithYield(values, ctx);
  requireCycleLabel(values, ctx);
  // Deliberately NOT requireAutoLotComponents: an edit generates no lot, so a
  // blank P.Code here means "preserve the stored one" (a legacy cycle may not
  // even have one), never "you are about to mint a lot without it".
}

export const cycleFormSchema = z.object(cyclePlanFields).superRefine(refineCyclePlan);
export type CycleFormValues = z.infer<typeof cycleFormSchema>;

export const cycleEditFormSchema = z.object(cycleEditPlanFields).superRefine(refineEditCyclePlan);
export type CycleEditFormValues = z.infer<typeof cycleEditFormSchema>;

// The subset of fields CyclePlanFields actually reads/writes — any form
// values type sharing this shape can reuse it without duplicating the field
// markup/validation-display wiring.
export interface CyclePlanShape {
  poNumber?: string;
  pCode?: string;
  crop?: string;
  variety?: string;
  cycleLabel?: string;
  supplierLotNo?: string;
  oracleSupplierCode?: string;
  oracleInvoice?: string;
  refAccount?: string;
  plantingDate?: string;
  plantCount?: number;
  expectedYieldFull?: number;
  expectedYieldUnit?: string;
}

function numberOrNull(value: number | undefined): number | null {
  return value === undefined ? null : value;
}

// CREATE payload — poNumber trimmed-or-null (round 8-13A/B: optional, never
// uppercased here — the backend normalizes/upper-cases it); pCode always sent
// (still required). Round A — no lot is sent at all: the backend always
// generates {cycleLabel}-{supplierCode}-{pCode}-{running} itself.
// supplierLotNo is sent trimmed, or null when blank — it is independent of the
// system lot. NEVER sends lotNo/lotNoSource/lotRunningNo or any auto-lot
// series key (all server-derived).
export function toPayload(values: CycleFormValues): PlotCycleCreatePayload {
  return {
    poNumber: values.poNumber?.trim() || null,
    pCode: values.pCode.trim(),
    crop: values.crop || null,
    variety: values.variety || null,
    // requireCycleLabel (superRefine) already blocked submit on a blank
    // value, so `.trim()` here is always nonblank — never sent as null,
    // matching PlotCycleCreatePayload.cycleLabel's now-required `string`.
    cycleLabel: values.cycleLabel!.trim(),
    supplierLotNo: values.supplierLotNo?.trim() || null,
    // Round 8-21B — trim-or-null, same convention as supplierLotNo above.
    oracleSupplierCode: values.oracleSupplierCode?.trim() || null,
    oracleInvoice: values.oracleInvoice?.trim() || null,
    refAccount: values.refAccount?.trim() || null,
    plantingDate: values.plantingDate || null,
    plantCount: numberOrNull(values.plantCount),
    expectedYieldFull: numberOrNull(values.expectedYieldFull),
    expectedYieldUnit: values.expectedYieldUnit?.trim() || null,
  };
}

// EDIT payload — exclude_unset preserve semantics for MOST fields (blank =
// preserve → key omitted), but poNumber is the exception (round 8-13B): it is
// ALWAYS sent, same pattern as supplierLotNo below, because an optional field
// where blank could mean either "leave it" or "clear it" is ambiguous — this
// form resolves that by making blank always mean "clear". pCode keeps the
// OLD omit-when-blank/preserve behavior (round 8-13A did not touch it: P.Code
// stays required on create, and editing it blank is still just "don't touch
// it", never "clear a required field"). Round A — the system lot is never
// sent in any form: it is immutable once the cycle exists, so an edit simply
// has nothing to say about it. Never sends lotNoSource/lotRunningNo either.
export function toEditPayload(values: CycleEditFormValues): PlotCycleUpdatePayload {
  const payload: PlotCycleUpdatePayload = {
    crop: values.crop || null,
    variety: values.variety || null,
    // Round 8-17A.1 — requireCycleLabel (via refineEditCyclePlan) already
    // blocked submit on blank, including for a legacy null cycle (the user
    // must type a value before saving ANY edit) — so this is always sent
    // nonblank, never a clearing null. Matches the backend's own refusal to
    // clear an existing label (update_plot_cycle's effective_cycle_label
    // check) — the two can never disagree about what "cleared" means.
    cycleLabel: values.cycleLabel!.trim(),
    // Round 8-13B — always sent: blank means "clear the PO Number", never
    // "leave it as is" (re-sending the SAME trimmed value when the user never
    // touched the field is harmless — the backend normalizes idempotently).
    poNumber: values.poNumber?.trim() || null,
    // Round 8-12A — always sent: an empty box means "clear the supplier's lot
    // number", which is a real edit, not a preserve. It never regenerates the
    // system lot.
    supplierLotNo: values.supplierLotNo?.trim() || null,
    // Round 8-21B — always sent, same convention as supplierLotNo: an empty
    // box clears the stored value; an untouched (prefilled) box round-trips
    // its current value back unchanged.
    oracleSupplierCode: values.oracleSupplierCode?.trim() || null,
    oracleInvoice: values.oracleInvoice?.trim() || null,
    refAccount: values.refAccount?.trim() || null,
    plantingDate: values.plantingDate || null,
    plantCount: numberOrNull(values.plantCount),
    expectedYieldFull: numberOrNull(values.expectedYieldFull),
    expectedYieldUnit: values.expectedYieldUnit?.trim() || null,
  };
  const pc = values.pCode?.trim();
  if (pc) payload.pCode = pc;
  return payload;
}

/** Preview string for the Auto Lot the backend WILL generate (round 8-12A V2):
 *
 *     {cycleLabel}-{supplierCode}-{pCode}-###
 *
 * The running number is allocated ONLY at save time, server-side, per
 * (supplier, cycleLabel, pCode) series across plots — the client can never
 * know it, so the segment is always the literal "###". Every component is used
 * IN FULL and verbatim: the cycle label is not parsed as a date or case-folded
 * ("26-may" stays "26-may"), and the P.Code is never clipped ("WM-141" stays
 * "WM-141"). Neither the PO nor the plot code appears — V1 used both, V2 uses
 * neither.
 *
 * A component that hasn't been filled in yet renders as a readable Thai
 * placeholder rather than a fabricated value. Display-only; never sent. */
export function autoLotPreview(
  cycleLabel: string | undefined,
  supplierCode: string | undefined,
  pCode: string | undefined,
): string {
  const label = cycleLabel?.trim() || '<ชื่อรอบปลูก>';
  const supplier = supplierCode?.trim() || '<รหัส Supplier>';
  const code = pCode?.trim() || '<P.Code>';
  return `${label}-${supplier}-${code}-###`;
}

/** Human label + tone for a cycle's lotNoSource badge (round 8-5B). */
export function lotSourceBadge(
  source: 'auto' | 'manual' | 'legacy' | null,
  hasLot: boolean,
): { label: string; className: string } | null {
  if (source === 'auto') return { label: 'อัตโนมัติ', className: 'bg-green-100 text-green-700' };
  if (source === 'manual') return { label: 'กรอกเอง', className: 'bg-blue-100 text-blue-700' };
  if (hasLot) return { label: 'ข้อมูลเดิม', className: 'bg-gray-100 text-gray-600' };
  return null;
}

function cycleMutationErrorMessage(error: unknown): string {
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

/**
 * Round 8-6I Part G — error mapping specific to reactivate/reactivate-with-
 * cycle, layered on top of the generic detail-extracting
 * cycleMutationErrorMessage: 404 and the two distinct 409 cases (already
 * active vs the defensive inconsistent-state guard) get their own copy;
 * everything else (422 validation, network) falls back to the same
 * formatter every other cycle modal uses.
 */
export function reactivateErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) {
      // No response at all — network error/timeout, outcome unconfirmed.
      return 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';
    }
    const status = error.response.status;
    if (status === 404) return 'ไม่พบแปลง หรือคุณไม่มีสิทธิ์เข้าถึง';
    if (status === 409) {
      const data = error.response.data as { detail?: unknown } | undefined;
      const detail = typeof data?.detail === 'string' ? data.detail : '';
      if (detail.includes('เปิดใช้งานอยู่แล้ว')) {
        return 'แปลงนี้เปิดใช้งานอยู่แล้ว กรุณารีเฟรชข้อมูล';
      }
      // Inconsistent-state guard (or any other 409) — the backend's own
      // message is already a safe, non-leaking Thai string; show it
      // verbatim rather than inventing a second copy of it here.
      return detail || cycleMutationErrorMessage(error);
    }
  }
  // 422 (validation) and everything else — the same detail-extracting
  // formatter every other cycle modal in this file uses.
  return cycleMutationErrorMessage(error);
}

export function deactivateErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response) {
      return 'เชื่อมต่อระบบไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';
    }
    if (error.response.status === 404) {
      return 'ไม่พบแปลง หรือคุณไม่มีสิทธิ์เข้าถึง';
    }
    if (error.response.status === 409) {
      const data = error.response.data as { detail?: unknown } | undefined;
      const detail = typeof data?.detail === 'string' ? data.detail : '';
      return detail || 'ยังปิดใช้งานแปลงไม่ได้ กรุณาตรวจสอบรอบปลูกปัจจุบัน';
    }
  }
  return cycleMutationErrorMessage(error);
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

export function CyclePlanFields<T extends CyclePlanShape>({
  register, errors, watch, setValue, supplierCode, mode = 'create', existingLot,
}: {
  register: UseFormRegister<T>;
  errors: FieldErrors<T>;
  watch: UseFormWatch<T>;
  setValue: UseFormSetValue<T>;
  // Round 8-12B — the SUPPLIER code for the Auto Lot preview
  // ({cycleLabel}-{supplierCode}-{pCode}-###). V1 previewed with the plot code;
  // V2's formula has no plot code at all. For Start/Edit/Rollover this is the
  // plot's own supplierCode from the read model; for Create Plot it's the code
  // of the Supplier the user picked. Empty string while nothing is selected —
  // the preview then shows a placeholder rather than inventing a value. The
  // user never types it here: it is server truth, echoed for display only.
  supplierCode: string;
  mode?: 'create' | 'edit';
  // Edit only — the cycle's current lot, shown read-only (round A: an edit can
  // never change it, so there is nothing to offer but the value itself).
  existingLot?: { lotNo: string | null; lotNoSource: 'auto' | 'manual' | 'legacy' | null };
}) {
  const hasExistingLot = mode === 'edit' && !!existingLot?.lotNo;
  const pCodeRequired = mode === 'create';

  // --- Round 8-26C: P.Code is DERIVED from the variety, not typed ---------
  // A variety owns exactly one active P.Code (services/p_code_master.py), so
  // the only honest control here is a read-only echo of what the variety
  // resolves to. `varietyTouched` is why this is not a plain effect keyed on
  // `variety`: in EDIT mode an untouched form must leave the cycle's stored
  // P.Code exactly as it is — a legacy cycle carries a free-text value that
  // is not in Master Data, and silently rewriting it to the variety's
  // current P.Code (or to blank) would change data the user never touched,
  // and would change the Lot No a regenerate produces. So the derivation
  // only ever runs after the user actually picks a crop or a variety.
  const variety = (watch('variety' as Path<T>) as string | undefined) || '';
  const [varietyTouched, setVarietyTouched] = useState(false);
  const deriveActive = mode === 'create' || varietyTouched;

  const pCodeQuery = useQuery({
    queryKey: masterDataQueryKey('p_code', variety || null, true),
    queryFn: () => listMasterData({ type: 'p_code', parent: variety, activeOnly: true }),
    enabled: deriveActive && !!variety,
  });
  const derivedPCode = pCodeQuery.data?.[0]?.value ?? '';
  // Only the RESOLVED value is written back, and only once it is known —
  // writing '' while the query is still in flight would blank the field on
  // every re-render and fight the user's own selection.
  const settledPCode = !variety ? '' : pCodeQuery.isSuccess ? derivedPCode : null;
  const lastWritten = useRef<string | null>(null);
  useEffect(() => {
    if (!deriveActive || settledPCode === null) return;
    if (lastWritten.current === settledPCode) return;
    lastWritten.current = settledPCode;
    setValue('pCode' as Path<T>, settledPCode as never, {
      shouldDirty: true, shouldValidate: true,
    });
  }, [deriveActive, settledPCode, setValue]);

  const pCodeValue = (watch('pCode' as Path<T>) as string | undefined) || '';
  const varietyHasNoPCode = deriveActive && !!variety && pCodeQuery.isSuccess && !derivedPCode;

  return (
    <>
      <div className="grid grid-cols-2 gap-4">
        <Field
          label="PO Number (ไม่บังคับ)"
          error={errors.poNumber?.message as string | undefined}
        >
          <input
            {...register('poNumber' as Path<T>)}
            className="field-input"
            placeholder="เช่น PO25001"
          />
          {mode === 'edit' && (
            <p className="text-xs text-muted-foreground">
              ไม่บังคับ — เว้นว่างเพื่อลบ PO Number ของรอบนี้
            </p>
          )}
        </Field>
        {/* Round 8-26C — read-only: the value comes from the chosen พันธุ์,
            never from typing. `register` still binds it so the form owns the
            value (submit, validation, and the Auto Lot preview all read it),
            and `readOnly` rather than `disabled` keeps it in the payload. */}
        <Field
          label={`P.Code${pCodeRequired ? ' *' : ''}`}
          error={errors.pCode?.message as string | undefined}
        >
          <input
            {...register('pCode' as Path<T>)}
            readOnly
            aria-readonly="true"
            // Field's <label> has no htmlFor, so without this the input has
            // no accessible name at all — and a read-only field the user
            // cannot click into needs one more than most.
            aria-label="P.Code"
            className="field-input bg-muted text-muted-foreground"
            placeholder={variety ? '—' : 'เลือกพันธุ์ก่อน'}
          />
          {varietyHasNoPCode ? (
            <p className="text-xs text-destructive">
              พันธุ์นี้ยังไม่ได้กำหนด P.Code — กรุณาเพิ่มที่เมนู Master Data ก่อน
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              {pCodeValue ? 'มาจากพันธุ์ที่เลือก' : 'ระบบจะเติมให้อัตโนมัติเมื่อเลือกพันธุ์'}
            </p>
          )}
        </Field>
      </div>

      {/* Round 8-17A.1 — required in every mode (create AND edit), not just
          when generating an Auto Lot: see requireCycleLabel. */}
      <Field label="ชื่อรอบปลูก *" error={errors.cycleLabel?.message as string | undefined}>
        <input
          {...register('cycleLabel' as Path<T>)}
          className="field-input"
          placeholder="เช่น jun2026 หรือ may2026"
        />
        <p className="text-xs text-muted-foreground">ใช้ระบุรอบปลูกและสร้าง Lot No อัตโนมัติ เช่น jun2026, may2026</p>
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field label="ชนิดพืช" error={errors.crop?.message as string | undefined}>
          <MasterDataSelect
            type="crop"
            placeholder="— เลือกชนิดพืช —"
            value={(watch('crop' as Path<T>) as string | undefined) || null}
            onChange={(v) => {
              // Changing the crop clears the variety, which clears the
              // derived P.Code — so this counts as touching the variety.
              setVarietyTouched(true);
              setValue('crop' as Path<T>, (v ?? '') as never, { shouldDirty: true });
              setValue('variety' as Path<T>, '' as never, { shouldDirty: true, shouldValidate: true });
            }}
          />
        </Field>
        <Field
          label={`พันธุ์/สายพันธุ์${mode === 'create' ? ' *' : ''}`}
          error={errors.variety?.message as string | undefined}
        >
          <MasterDataSelect
            type="variety"
            placeholder="— เลือกพันธุ์ —"
            parent={(watch('crop' as Path<T>) as string | undefined) || null}
            value={(watch('variety' as Path<T>) as string | undefined) || null}
            onChange={(v) => {
              setVarietyTouched(true);
              setValue('variety' as Path<T>, (v ?? '') as never, {
                shouldDirty: true, shouldValidate: true,
              });
            }}
          />
        </Field>
      </div>

      {/* Lot No ระบบ — READ-ONLY (round A). There is no mode to choose any
          more: the server generates the lot when the cycle is created and it
          never changes afterwards, so this is a display-only panel showing
          either what WILL be generated (create) or what already exists (edit).
          Labelled "Lot No ระบบ" because a cycle carries TWO lot numbers; the
          editable one is Supplier Lot No, its own Field below. */}
      <Field label="Lot No ระบบ">
        {hasExistingLot ? (
          <div className="rounded-md border border-border bg-muted px-3 py-2">
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <Lock className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="break-all font-mono">{existingLot?.lotNo}</span>
              {(() => {
                const badge = lotSourceBadge(existingLot?.lotNoSource ?? null, true);
                return badge ? (
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${badge.className}`}>
                    {badge.label}
                  </span>
                ) : null;
              })()}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              ระบบสร้างให้ตอนเริ่มรอบปลูก — แก้ไขไม่ได้
            </p>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800">
            <p className="break-all">
              ระบบจะสร้างให้:{' '}
              <span className="font-mono">
                {autoLotPreview(
                  watch('cycleLabel' as Path<T>) as string | undefined,
                  supplierCode,
                  watch('pCode' as Path<T>) as string | undefined,
                )}
              </span>
            </p>
            <p className="mt-0.5 text-xs text-green-700">
              รูปแบบ: ชื่อรอบปลูก-รหัส Supplier-P.Code-เลขรัน · เลขรันจริงระบบจะกำหนดให้ตอนบันทึก
            </p>
          </div>
        )}
      </Field>

      {/* Round 8-12B — the SUPPLIER's own lot number. Deliberately its own
          Field OUTSIDE the segmented control above, so it reads as separate
          data rather than another mode of the system lot. */}
      <Field label="Supplier Lot No" error={errors.supplierLotNo?.message as string | undefined}>
        <input
          {...register('supplierLotNo' as Path<T>)}
          className="field-input"
          placeholder="เช่น SUP-LOT-A123"
        />
        <p className="text-xs text-muted-foreground">
          เลข Lot ที่ Supplier กำหนดเอง (ไม่บังคับ) — ไม่เกี่ยวกับเลข Lot ที่ระบบสร้าง
        </p>
      </Field>

      <div className="grid grid-cols-2 gap-4">
        <Field label="วันที่ปลูก" error={errors.plantingDate?.message as string | undefined}>
          <input {...register('plantingDate' as Path<T>)} type="date" className="field-input" />
        </Field>
        <div />
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Field label="จำนวนต้น/จำนวนปลูก" error={errors.plantCount?.message as string | undefined}>
          <input {...register('plantCount' as Path<T>)} type="number" step="1" className="field-input" placeholder="0" />
        </Field>
        <Field label="เป้าผลิต" error={errors.expectedYieldFull?.message as string | undefined}>
          <input {...register('expectedYieldFull' as Path<T>)} type="number" step="0.01" className="field-input" placeholder="0.00" />
        </Field>
        <Field label="หน่วย" error={errors.expectedYieldUnit?.message as string | undefined}>
          <select {...register('expectedYieldUnit' as Path<T>)} className="field-input">
            <option value="">— เลือกหน่วย —</option>
            {(() => {
              const current = watch('expectedYieldUnit' as Path<T>) as string | undefined;
              return current && !YIELD_UNIT_OPTIONS.includes(current)
                ? <option value={current}>{current}</option>
                : null;
            })()}
            {YIELD_UNIT_OPTIONS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </Field>
      </div>

      {/* Round 8-21B — three independent, OPTIONAL back-office reference
          fields, grouped under their own heading BELOW every other
          cycle-plan field above (not a nested card — a plain labelled
          section at the same visual level as the rest of this form). None
          of the three has a `*`: all optional in every mode. maxLength=255
          mirrors the backend's VARCHAR(255) cap; the zod schema
          (planCoreFields above) enforces the same limit. The grid collapses
          to one column on narrow screens (`sm:grid-cols-3`), and helper
          text wraps instead of overflowing. */}
      <div className="space-y-3 border-t border-border pt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          ข้อมูลอ้างอิง Oracle
        </h4>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Field label="Oracle Supplier Code" error={errors.oracleSupplierCode?.message as string | undefined}>
            <input
              {...register('oracleSupplierCode' as Path<T>)}
              maxLength={255}
              className="field-input"
              placeholder="เช่น ORC-SUP-001"
            />
          </Field>
          <Field label="Oracle Invoice" error={errors.oracleInvoice?.message as string | undefined}>
            <input
              {...register('oracleInvoice' as Path<T>)}
              maxLength={255}
              className="field-input"
              placeholder="เช่น INV-2026-0001"
            />
          </Field>
          <Field label="Ref Account" error={errors.refAccount?.message as string | undefined}>
            <input
              {...register('refAccount' as Path<T>)}
              maxLength={255}
              className="field-input"
              placeholder="เช่น ACC-0001"
            />
          </Field>
        </div>
        {mode === 'edit' && (
          <p className="break-words text-xs text-muted-foreground">
            ไม่บังคับ — เว้นว่างเพื่อลบค่าของรอบนี้
          </p>
        )}
      </div>
    </>
  );
}

function ModalShell({
  title, icon, onClose, children,
}: {
  title: string; icon: React.ReactNode; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            {icon}
            {title}
          </h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function StartCycleModal({
  plotId, supplierCode, onClose, onSaved,
}: {
  plotId: string; supplierCode: string; onClose: () => void; onSaved: () => void;
}) {
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<CycleFormValues>({
    resolver: zodResolver(cycleFormSchema),
    defaultValues: { poNumber: '', pCode: '' },
  });

  const createM = useMutation({ mutationFn: (p: PlotCycleCreatePayload) => createPlotCycle(plotId, p) });

  async function onSubmit(values: CycleFormValues) {
    await createM.mutateAsync(toPayload(values));
    onSaved();
  }

  return (
    <ModalShell title="เริ่มรอบปลูกใหม่" icon={<Sprout className="h-4 w-4 text-green-600" />} onClose={onClose}>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
        <CyclePlanFields register={register} errors={errors} watch={watch} setValue={setValue}
          supplierCode={supplierCode} mode="create" />

        <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
          {createM.error && (
            <p className="mb-3 text-sm text-destructive">{cycleMutationErrorMessage(createM.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              เริ่มรอบปลูก
            </button>
          </div>
        </div>
      </form>
    </ModalShell>
  );
}

export function EditCycleModal({
  plotId, supplierCode, cycle, onClose, onSaved,
}: {
  plotId: string; supplierCode: string; cycle: PlotCycle; onClose: () => void; onSaved: () => void;
}) {
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<CycleEditFormValues>({
    resolver: zodResolver(cycleEditFormSchema),
    values: {
      poNumber: cycle.poNumber ?? '',
      pCode: cycle.pCode ?? '',
      crop: cycle.crop ?? '',
      variety: cycle.variety ?? '',
      cycleLabel: cycle.cycleLabel ?? '',
      // Round 8-12B — always prefilled: the supplier's lot number is
      // independent data, so an edit must show what is stored and let the user
      // clear it by emptying the box. (The SYSTEM lot has no input at all —
      // CyclePlanFields renders it read-only from `existingLot`.)
      supplierLotNo: cycle.supplierLotNo ?? '',
      // Round 8-21B — same "always prefilled, clear by emptying" convention.
      oracleSupplierCode: cycle.oracleSupplierCode ?? '',
      oracleInvoice: cycle.oracleInvoice ?? '',
      refAccount: cycle.refAccount ?? '',
      plantingDate: cycle.plantingDate ?? '',
      plantCount: cycle.plantCount ?? undefined,
      expectedYieldFull: cycle.expectedYieldFull != null ? Number(cycle.expectedYieldFull) : undefined,
      expectedYieldUnit: cycle.expectedYieldUnit ?? '',
    },
  });

  const updateM = useMutation({ mutationFn: (p: PlotCycleUpdatePayload) => updatePlotCycle(plotId, cycle.id, p) });

  async function onSubmit(values: CycleEditFormValues) {
    await updateM.mutateAsync(toEditPayload(values));
    onSaved();
  }

  return (
    <ModalShell title={`แก้รอบปลูก — รอบที่ ${cycle.cycleNo}`} icon={<Pencil className="h-4 w-4" />} onClose={onClose}>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
        <CyclePlanFields register={register} errors={errors} watch={watch} setValue={setValue}
          supplierCode={supplierCode} mode="edit"
          existingLot={{ lotNo: cycle.lotNo, lotNoSource: cycle.lotNoSource }} />

        <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
          {updateM.error && (
            <p className="mb-3 text-sm text-destructive">{cycleMutationErrorMessage(updateM.error)}</p>
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
    </ModalShell>
  );
}

const closeSchema = z.object({
  status: z.enum(['harvested', 'cancelled']),
  closeReason: z.string().optional().or(z.literal('')),
  // Round D — the ACTUAL harvest, pre-filled from the field team's own report
  // and editable. Left blank they are simply not sent, and the server carries
  // the reported figures forward itself; the form does not re-implement that
  // fallback, so the two can never disagree.
  harvestYield: optionalNumberInput,
  finalYieldAfterClean: optionalNumberInput,
  harvestDate: z.string().optional().or(z.literal('')),
});
type CloseFormValues = z.infer<typeof closeSchema>;

export function CloseCycleModal({
  plotId, cycle, onClose, onSaved,
}: {
  plotId: string; cycle: PlotCycle; onClose: () => void; onSaved: () => void;
}) {
  const { register, handleSubmit, watch, reset, formState: { errors, isSubmitting } } = useForm<CloseFormValues>({
    resolver: zodResolver(closeSchema),
    defaultValues: { status: 'harvested', closeReason: '' },
  });

  // Round D — what the server WILL record if the admin just confirms. Read-only
  // and side-effect-free; the close itself is still the POST below.
  const previewQ = useQuery({
    queryKey: ['plot-cycle-close-preview', plotId, cycle.id],
    queryFn: () => getPlotCycleClosePreview(plotId, cycle.id),
  });

  // Pre-fill ONCE, when the figures arrive — never from a refetching effect
  // that could overwrite something the admin has already corrected. `reset`
  // (not per-field setValue) so the boxes count as pristine defaults rather
  // than edits the user made.
  const prefilledRef = useRef(false);
  useEffect(() => {
    const p = previewQ.data;
    if (!p || !p.resolved || prefilledRef.current) return;
    prefilledRef.current = true;
    reset({
      status: 'harvested',
      closeReason: '',
      harvestYield: p.harvestYield != null ? Number(p.harvestYield) : undefined,
      finalYieldAfterClean:
        p.finalYieldAfterClean != null ? Number(p.finalYieldAfterClean) : undefined,
      harvestDate: p.harvestDate ?? '',
    });
  }, [previewQ.data, reset]);

  const status = watch('status');
  const recordingHarvest = status === 'harvested';

  const closeM = useMutation({
    mutationFn: (p: PlotCycleClosePayload) => closePlotCycle(plotId, cycle.id, p),
  });

  async function onSubmit(values: CloseFormValues) {
    const payload: PlotCycleClosePayload = {
      status: values.status,
      closeReason: values.closeReason?.trim() || null,
    };
    // A cancelled cycle was never harvested — the backend rejects figures sent
    // with one, so the form never sends them either.
    if (values.status === 'harvested') {
      payload.harvestYield = values.harvestYield ?? null;
      payload.finalYieldAfterClean = values.finalYieldAfterClean ?? null;
      payload.harvestDate = values.harvestDate?.trim() || null;
    }
    await closeM.mutateAsync(payload);
    onSaved();
  }

  return (
    <ModalShell title={`ปิดรอบปลูก — รอบที่ ${cycle.cycleNo}`} icon={<Archive className="h-4 w-4 text-amber-600" />} onClose={onClose}>
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          หลังปิดรอบปลูก จะบันทึกการตรวจใหม่ไม่ได้จนกว่าจะเริ่มรอบปลูกใหม่
        </p>
        <Field label="สถานะ" error={errors.status?.message}>
          <select {...register('status')} className="field-input">
            <option value="harvested">เก็บเกี่ยวแล้ว</option>
            <option value="cancelled">ยกเลิก</option>
          </select>
        </Field>

        {/* Round D — the actual harvest, pre-filled from the field team's own
            report. The admin confirms or corrects; nobody retypes. Hidden for a
            cancelled close, which records no harvest at all. */}
        {recordingHarvest && (
          <section className="space-y-3 rounded-md border border-border bg-secondary/30 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium text-foreground">ผลผลิตจริง</h3>
              {previewQ.isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
            </div>
            {previewQ.data?.resolved ? (
              <p className="text-xs text-muted-foreground">
                ดึงจากบันทึกการตรวจ
                {previewQ.data.sourceGrowthStage ? ` ระยะ "${previewQ.data.sourceGrowthStage}"` : ''}
                {previewQ.data.sourceRecordDate ? ` วันที่ ${previewQ.data.sourceRecordDate}` : ''}
                {' '}— ตรวจสอบและแก้ไขได้
              </p>
            ) : (
              !previewQ.isLoading && (
                <p className="text-xs text-muted-foreground">
                  รอบนี้ยังไม่มีบันทึกผลผลิตจากหน้าตรวจแปลง — เว้นว่างไว้ได้ หรือกรอกเองให้ครบทั้ง 3 ช่อง
                </p>
              )
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="ผลผลิตที่เก็บได้ (kg)" error={errors.harvestYield?.message}>
                <input {...register('harvestYield')} type="number" step="0.01" min={0}
                  className="field-input" placeholder="0.00" />
              </Field>
              <Field label="หลังทำความสะอาด (kg)" error={errors.finalYieldAfterClean?.message}>
                <input {...register('finalYieldAfterClean')} type="number" step="0.01" min={0}
                  className="field-input" placeholder="0.00" />
              </Field>
              <Field label="วันที่เก็บเกี่ยว" error={errors.harvestDate?.message}>
                <input {...register('harvestDate')} type="date" className="field-input" />
              </Field>
            </div>
          </section>
        )}

        <Field label="เหตุผล (ไม่บังคับ)" error={errors.closeReason?.message}>
          <textarea {...register('closeReason')} rows={3} className="field-input" />
        </Field>

        <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
          {closeM.error && (
            <p className="mb-3 text-sm text-destructive">{cycleMutationErrorMessage(closeM.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              ยืนยันปิดรอบปลูก
            </button>
          </div>
        </div>
      </form>
    </ModalShell>
  );
}

// --- Rollover — atomically close the active cycle + open a fresh one -------
// (round 7.9C, backed by the round-7.9B single-transaction endpoint). This is
// the ONLY client entry point for that transition: a caller must never call
// closePlotCycle then createPlotCycle as two separate requests, since a start
// failure after a successful close would strand the plot with no active
// cycle — see rolloverPlotCycle's docstring in api/plots.ts.
const rolloverSchema = z.object({
  closeStatus: z.enum(['harvested', 'cancelled']),
  closeReason: z.string().optional().or(z.literal('')),
  ...cyclePlanFields,
}).superRefine(refineCyclePlan);
type RolloverFormValues = z.infer<typeof rolloverSchema>;

function toRolloverPayload(values: RolloverFormValues): PlotCycleRolloverPayload {
  // The new cycle reuses the shared CREATE payload builder (PO optional,
  // pCode required, no lot — the server generates it) — RolloverFormValues is
  // a superset of CycleFormValues.
  return {
    closeStatus: values.closeStatus,
    closeReason: values.closeReason?.trim() || null,
    newCycle: toPayload(values),
  };
}

/** Maps a rollover failure to Thai copy a user can act on — 409 means someone
 * else changed the cycle underneath them (refresh and retry), 404 means the
 * plot/cycle disappeared (out of scope, or deleted), and everything else
 * (422 validation, network) falls back to the same detail-extracting message
 * the other cycle modals use. */
function rolloverErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status === 409) {
      return 'ไม่สามารถจบรอบได้ อาจมีการเปลี่ยนแปลงรอบปลูกจากผู้ใช้อื่น กรุณารีเฟรชแล้วลองใหม่';
    }
    if (status === 404) {
      return 'ไม่พบแปลงหรือรอบปลูกนี้';
    }
  }
  return cycleMutationErrorMessage(error);
}

export function RolloverCycleModal({
  plotId, supplierCode, cycle, onClose, onSaved,
}: {
  plotId: string; supplierCode: string; cycle: PlotCycle; onClose: () => void; onSaved: () => void;
}) {
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<RolloverFormValues>({
    resolver: zodResolver(rolloverSchema),
    defaultValues: { closeStatus: 'harvested', closeReason: '', poNumber: '', pCode: '' },
  });

  const rolloverM = useMutation({
    mutationFn: (p: PlotCycleRolloverPayload) => rolloverPlotCycle(plotId, cycle.id, p),
  });

  async function onSubmit(values: RolloverFormValues) {
    try {
      await rolloverM.mutateAsync(toRolloverPayload(values));
    } catch {
      // Already reflected in rolloverM.error (rendered below) — nothing else
      // to do. Caught here (rather than left to reject) so a failed rollover
      // doesn't surface as an unhandled promise rejection from the form's
      // synchronous submit handler.
      return;
    }
    onSaved();
  }

  return (
    <ModalShell
      title={`จบรอบเดิม + เริ่มรอบใหม่ — รอบที่ ${cycle.cycleNo}`}
      icon={<RefreshCw className="h-4 w-4 text-green-600" />}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-5 overflow-y-auto px-6 py-5">
        <div className="space-y-1 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
          <p>ระบบจะปิดรอบเดิมและเปิดรอบใหม่ในครั้งเดียว</p>
          <p>QR เดิมของแปลงยังใช้ต่อได้</p>
          <p>ประวัติรอบเดิมและบันทึกการตรวจเดิมจะไม่หาย</p>
        </div>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold text-foreground">จบรอบเดิม — รอบที่ {cycle.cycleNo}</h3>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
            <div><dt className="text-xs text-muted-foreground">ชนิดพืช</dt><dd>{cycle.crop ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">พันธุ์/สายพันธุ์</dt><dd>{cycle.variety ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Lot No ระบบ</dt><dd>{cycle.lotNo ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Supplier Lot No</dt><dd>{cycle.supplierLotNo ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Oracle Supplier Code</dt><dd className="break-words">{cycle.oracleSupplierCode ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Oracle Invoice</dt><dd className="break-words">{cycle.oracleInvoice ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Ref Account</dt><dd className="break-words">{cycle.refAccount ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">วันที่ปลูก</dt><dd>{cycle.plantingDate ?? '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">จำนวนต้น</dt><dd>{cycle.plantCount != null ? cycle.plantCount.toLocaleString('th-TH') : '—'}</dd></div>
            <div><dt className="text-xs text-muted-foreground">เป้าผลิต</dt><dd>{formatYieldQuantity(cycle.expectedYieldFull, cycle.expectedYieldUnit) ?? '—'}</dd></div>
          </dl>
          <Field label="สถานะปิดรอบ" error={errors.closeStatus?.message}>
            <select {...register('closeStatus')} className="field-input">
              <option value="harvested">เก็บเกี่ยวแล้ว</option>
              <option value="cancelled">ยกเลิก</option>
            </select>
          </Field>
          <Field label="เหตุผล (ไม่บังคับ)" error={errors.closeReason?.message}>
            <textarea {...register('closeReason')} rows={2} className="field-input" />
          </Field>
        </section>

        <section className="space-y-3 border-t border-border pt-4">
          <h3 className="text-sm font-semibold text-foreground">เริ่มรอบใหม่</h3>
          <CyclePlanFields register={register} errors={errors} watch={watch} setValue={setValue}
            supplierCode={supplierCode} mode="create" />
        </section>

        <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
          {rolloverM.error && (
            <p className="mb-3 text-sm text-destructive">{rolloverErrorMessage(rolloverM.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              ยืนยันจบรอบ + เริ่มรอบใหม่
            </button>
          </div>
        </div>
      </form>
    </ModalShell>
  );
}

// --- Reactivation (round 8-6H backend / 8-6I frontend) ----------------------
// Two DISTINCT actions, never combined into one request: reactivatePlot
// (reopen only, no cycle) and reactivatePlotWithCycle (atomic reopen +
// first-cycle create). Neither ever calls the other's endpoint plus a
// second request — that atomicity guarantee lives entirely in the backend's
// single transaction (see api/plots.ts's reactivatePlotWithCycle docstring).

export function DeactivatePlotModal({
  plotId, plotCode, onClose, onSaved,
}: {
  plotId: string; plotCode: string; onClose: () => void; onSaved: () => void;
}) {
  const deactivateM = useMutation({ mutationFn: () => deactivatePlot(plotId) });

  async function handleConfirm() {
    try {
      await deactivateM.mutateAsync();
    } catch {
      return;
    }
    onSaved();
  }

  return (
    <ModalShell
      title={`ปิดใช้งานแปลง — ${plotCode}`}
      icon={<PowerOff className="h-4 w-4 text-destructive" />}
      onClose={onClose}
    >
      <div className="space-y-4 px-6 py-5">
        <div className="space-y-1 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <p>แปลงนี้จะถูกปิดใช้งานและไม่สามารถบันทึกการตรวจแปลงได้</p>
          <p>หากมีรอบปลูกที่เปิดอยู่ ต้องปิดรอบปลูกปัจจุบันก่อน</p>
          <p>ประวัติรอบปลูก บันทึกการตรวจ QR และหมายเลขเข้าตรวจจะไม่ถูกลบ</p>
        </div>
        {deactivateM.error && (
          <p className="text-sm text-destructive">{deactivateErrorMessage(deactivateM.error)}</p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
            ยกเลิก
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={deactivateM.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground disabled:opacity-60"
          >
            {deactivateM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            ยืนยันปิดใช้งานแปลง
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

export function ReactivatePlotModal({
  plotId, plotCode, onClose, onSaved,
}: {
  plotId: string; plotCode: string; onClose: () => void; onSaved: () => void;
}) {
  const reactivateM = useMutation({ mutationFn: () => reactivatePlot(plotId) });

  async function handleConfirm() {
    try {
      await reactivateM.mutateAsync();
    } catch {
      // Already reflected in reactivateM.error (rendered below) — caught
      // here so a failed reactivate doesn't surface as an unhandled promise
      // rejection; the modal stays open, same pattern as the other cycle
      // modals in this file.
      return;
    }
    onSaved();
  }

  return (
    <ModalShell title={`เปิดใช้งานแปลง — ${plotCode}`} icon={<Unlock className="h-4 w-4 text-green-600" />} onClose={onClose}>
      <div className="space-y-4 px-6 py-5">
        <div className="space-y-1 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
          <p>แปลงนี้จะกลับมาใช้งานได้อีกครั้ง</p>
          <p>แต่ยังบันทึกการตรวจแปลงไม่ได้จนกว่าจะเริ่มรอบปลูกใหม่</p>
        </div>
        {reactivateM.error && (
          <p className="text-sm text-destructive">{reactivateErrorMessage(reactivateM.error)}</p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={reactivateM.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {reactivateM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            ยืนยันเปิดใช้งานแปลง
          </button>
        </div>
      </div>
    </ModalShell>
  );
}

export function ReactivatePlotWithCycleModal({
  plotId, supplierCode, onClose, onSaved,
}: {
  plotId: string; supplierCode: string; onClose: () => void; onSaved: () => void;
}) {
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<CycleFormValues>({
    resolver: zodResolver(cycleFormSchema),
    defaultValues: { poNumber: '', pCode: '' },
  });

  const reactivateM = useMutation({
    mutationFn: (p: PlotCycleCreatePayload) => reactivatePlotWithCycle(plotId, p),
  });

  async function onSubmit(values: CycleFormValues) {
    try {
      await reactivateM.mutateAsync(toPayload(values));
    } catch {
      // Same unhandled-rejection guard as ReactivatePlotModal above.
      return;
    }
    onSaved();
  }

  return (
    <ModalShell
      title="เปิดใช้งานและเริ่มรอบปลูกใหม่"
      icon={<Sprout className="h-4 w-4 text-green-600" />}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 overflow-y-auto px-6 py-5">
        <div className="space-y-1 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
          <p>แปลงนี้จะเปิดใช้งานและเริ่มรอบปลูกใหม่ในครั้งเดียว</p>
          <p>QR และหมายเลขเข้าตรวจเดิมจะกลับมาใช้ได้ทันทีที่บันทึกสำเร็จ</p>
        </div>

        <CyclePlanFields register={register} errors={errors} watch={watch} setValue={setValue}
          supplierCode={supplierCode} mode="create" />

        <div className="sticky bottom-0 -mx-6 -mb-5 mt-2 border-t border-border bg-card px-6 py-4">
          {reactivateM.error && (
            <p className="mb-3 text-sm text-destructive">{reactivateErrorMessage(reactivateM.error)}</p>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">ยกเลิก</button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-60">
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              เปิดใช้งานและเริ่มรอบปลูก
            </button>
          </div>
        </div>
      </form>
    </ModalShell>
  );
}
