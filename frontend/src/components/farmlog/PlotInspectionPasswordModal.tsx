/**
 * PlotInspectionPasswordModal — set or replace a plot's inspection password
 * ("รหัสยืนยันแปลง", round 8-9B).
 *
 * The password is a SHARED access credential of the PLOT: from round 8-9C the
 * public flow will ask for the plot's inspection phone number PLUS this
 * password. It is never a person's identity.
 *
 * Secret handling (the whole point of this component):
 *   - The plaintext lives in React state and nowhere else. No localStorage, no
 *     sessionStorage, no IndexedDB, no react-query cache, no console, no URL.
 *   - Both fields are cleared the moment the modal closes (unmount drops the
 *     state) and on every successful save.
 *   - There is NO way to read an existing password back — the backend has no
 *     such endpoint. This modal only ever writes.
 *   - Client validation mirrors the backend policy for fast feedback; the
 *     BACKEND remains the source of truth (its 422 wins, and its message is
 *     mapped to one generic Thai string here, never echoed raw).
 *
 * Gated by plots.update — PlotDetail only renders the trigger for callers that
 * have it, and this component defends itself the same way PlotAccessPhoneModal
 * does.
 */
import { useId, useState } from 'react';
import axios from 'axios';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Eye, EyeOff, Loader2 } from 'lucide-react';
import { setPlotInspectionAccessCredential } from '../../api/plots';
import { useHasPermission } from '../../hooks/useHasPermission';

/** Locked by the backend policy (app/auth/plot_access_password.py — keep these
 * two in sync with PLOT_ACCESS_PASSWORD_MIN/MAX_LENGTH). */
export const PLOT_INSPECTION_PIN_MIN_LENGTH = 4;
export const PLOT_INSPECTION_PIN_MAX_LENGTH = 20;

const ASCII_DIGITS = /^[0-9]*$/;

/**
 * Mirrors the backend policy (round 8-9B.0): ASCII digits only, 4 to 20 of
 * them. There is deliberately NO guessability rule — 0000, 1111, 1234 and
 * 987654 are all valid, because field users share this code by voice and a
 * rule that rejects what they actually pick just gets it written down
 * somewhere worse. Returns a Thai message or null.
 *
 * Deliberately a pure function so the rules are testable without rendering,
 * and deliberately NOT the authority — a code this accepts can still be
 * rejected by the backend, which is what actually protects the plot.
 */
export function validatePlotInspectionPin(pin: string): string | null {
  if (pin.length === 0) return 'กรุณากรอกรหัส Supplier ตรวจแปลง';
  if (
    !ASCII_DIGITS.test(pin)
    || pin.length < PLOT_INSPECTION_PIN_MIN_LENGTH
    || pin.length > PLOT_INSPECTION_PIN_MAX_LENGTH
  ) {
    return 'รหัส Supplier ตรวจแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก';
  }
  return null;
}

/** Maps a set-credential failure to the exact Thai message the round 8-9B
 * brief specifies per status code. Never surfaces a raw backend detail — the
 * 422 body could quote policy internals, and nothing from the server is safe
 * to render verbatim here. */
export function describeInspectionCredentialError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status === 422) {
      return 'รหัส Supplier ตรวจแปลงต้องเป็นตัวเลข 4 ถึง 20 หลัก';
    }
    if (status === 503) {
      return 'ระบบยังไม่พร้อมตั้งรหัส Supplier ตรวจแปลง กรุณาติดต่อผู้ดูแลระบบ';
    }
    if (status === 409) {
      return 'มีผู้ใช้อื่นเปลี่ยนข้อมูลแปลงนี้ กรุณาลองใหม่อีกครั้ง';
    }
    if (status === 404) {
      return 'ไม่พบแปลงนี้ หรือคุณไม่มีสิทธิ์จัดการแปลง';
    }
  }
  return 'ตั้งรหัส Supplier ตรวจแปลงไม่สำเร็จ กรุณาลองใหม่อีกครั้ง';
}

/** Keeps only ASCII digits and caps the length at the policy maximum — so a
 * paste, an IME, or a numeric keypad can never smuggle in a non-digit or a
 * Unicode digit form, and a leading 0 survives (which is exactly why these are
 * type="password", never type="number"). */
function sanitizePin(raw: string): string {
  return raw.replace(/[^0-9]/g, '').slice(0, PLOT_INSPECTION_PIN_MAX_LENGTH);
}

function PinField({
  id,
  label,
  value,
  onChange,
  helperText,
  disabled,
  autoFocus,
  reveal,
  onToggleReveal,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (next: string) => void;
  /** Shown under the field — the only place the length rule is stated. */
  helperText?: string;
  disabled: boolean;
  autoFocus?: boolean;
  reveal: boolean;
  onToggleReveal: () => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-gray-700">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          // type="password" (never "number"): a number input strips a leading
          // zero and offers spinners, and "password" is what keeps the value
          // out of autofill history.
          type={reveal ? 'text' : 'password'}
          inputMode="numeric"
          autoComplete="new-password"
          maxLength={PLOT_INSPECTION_PIN_MAX_LENGTH}
          value={value}
          disabled={disabled}
          autoFocus={autoFocus}
          onChange={(e) => onChange(sanitizePin(e.target.value))}
          className="w-full rounded-md border border-border bg-background px-3 py-2 pr-10 font-mono text-base tracking-[0.3em] shadow-sm disabled:opacity-60"
        />
        <button
          type="button"
          onClick={onToggleReveal}
          disabled={disabled}
          title={reveal ? 'ซ่อนรหัส' : 'แสดงรหัส'}
          aria-label={reveal ? 'ซ่อนรหัส' : 'แสดงรหัส'}
          className="absolute inset-y-0 right-0 flex items-center px-3 text-muted-foreground hover:text-foreground disabled:opacity-60"
        >
          {reveal ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {helperText && <p className="mt-1 text-xs text-muted-foreground">{helperText}</p>}
    </div>
  );
}

export function PlotInspectionPasswordModal({
  plotId,
  supplierCode,
  supplierName,
  plotCode,
  plotName,
  configured,
  onClose,
  onSaved,
}: {
  plotId: string;
  supplierCode: string;
  supplierName: string;
  plotCode: string;
  plotName: string;
  /** Drives the title + the "existing inspectors lose access" warning. */
  configured: boolean;
  onClose: () => void;
  /** Called after a successful save + cache invalidation — the parent's job is
   * just to close the modal (this component owns invalidation). */
  onSaved: () => void;
}) {
  const qc = useQueryClient();
  const canUpdate = useHasPermission('plots.update');
  const fieldId = useId();

  // The ONLY place the plaintext exists. Unmounting the modal drops it.
  const [pin, setPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [reveal, setReveal] = useState(false);
  const [clientError, setClientError] = useState<string | null>(null);

  const saveM = useMutation({
    mutationFn: (password: string) => setPlotInspectionAccessCredential(plotId, password),
    // No automatic retry: a mutation that silently re-sends a password is both
    // a duplicate write and an extra copy of the secret on the wire.
    retry: false,
    onSuccess: () => {
      setPin('');
      setConfirmPin('');
      qc.invalidateQueries({ queryKey: ['plot-inspection-credential', plotId] });
      onSaved();
    },
  });

  function handleSubmit() {
    if (saveM.isPending) return;   // belt-and-suspenders against double submit
    const policyError = validatePlotInspectionPin(pin);
    if (policyError) {
      setClientError(policyError);
      return;
    }
    if (pin !== confirmPin) {
      setClientError('รหัส Supplier ตรวจแปลงทั้งสองช่องไม่ตรงกัน');
      return;
    }
    setClientError(null);
    saveM.mutate(pin);
  }

  const title = configured ? 'เปลี่ยนรหัส Supplier ตรวจแปลง' : 'ตั้งรหัส Supplier ตรวจแปลง';
  const submitLabel = configured ? 'ยืนยันเปลี่ยนรหัส' : 'ยืนยันตั้งรหัส';
  const readOnly = !canUpdate;
  const errorMessage = clientError
    ?? (saveM.isError ? describeInspectionCredentialError(saveM.error) : null);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-border bg-card shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="space-y-4 overflow-y-auto px-6 py-5">
          {/* Identity — an admin must never be one click from setting the
              password of the wrong plot. */}
          <dl className="grid grid-cols-1 gap-2 rounded-md bg-secondary px-3 py-2.5 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">Supplier</dt>
              <dd className="font-medium text-foreground">{supplierName || supplierCode || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">รหัส Supplier</dt>
              <dd className="font-mono font-medium text-foreground">{supplierCode || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">รหัสแปลง</dt>
              <dd className="font-mono font-medium text-foreground">{plotCode || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">ชื่อแปลง</dt>
              <dd className="font-medium text-foreground">{plotName || '—'}</dd>
            </div>
          </dl>

          <p className="text-sm text-muted-foreground">
            รหัสนี้ใช้ร่วมกับหมายเลขสำหรับเข้าตรวจ เพื่อค้นหาแปลงที่ได้รับอนุญาต
          </p>

          {configured && (
            <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>หลังเปิดใช้งานระบบใหม่ ผู้ตรวจที่ใช้รหัสเดิมจะไม่สามารถเข้าตรวจแปลงนี้ได้</span>
            </p>
          )}

          {readOnly ? (
            <p className="rounded-md bg-secondary px-3 py-2 text-xs text-muted-foreground">
              คุณไม่มีสิทธิ์ตั้งหรือเปลี่ยนรหัส Supplier ตรวจแปลงนี้ (แสดงผลอย่างเดียว)
            </p>
          ) : (
            <>
              <PinField
                id={`${fieldId}-pin`}
                label="รหัส Supplier ตรวจแปลง"
                helperText="กรอกตัวเลขอย่างน้อย 4 หลัก"
                value={pin}
                onChange={setPin}
                disabled={saveM.isPending}
                autoFocus
                reveal={reveal}
                onToggleReveal={() => setReveal((v) => !v)}
              />
              <PinField
                id={`${fieldId}-confirm`}
                label="ยืนยันรหัสอีกครั้ง"
                value={confirmPin}
                onChange={setConfirmPin}
                disabled={saveM.isPending}
                reveal={reveal}
                onToggleReveal={() => setReveal((v) => !v)}
              />
            </>
          )}
        </div>

        <div className="sticky bottom-0 mt-2 border-t border-border bg-card px-6 py-4">
          {errorMessage && <p className="mb-3 text-sm text-destructive">{errorMessage}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-secondary">
              ยกเลิก
            </button>
            {!readOnly && (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={saveM.isPending}
                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
              >
                {saveM.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {submitLabel}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
