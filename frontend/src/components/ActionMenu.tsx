/**
 * ActionMenu — icon-trigger dropdown for a row's secondary actions
 * (round 20). Same open/close pattern as Layout/UserMenu.tsx (local
 * `open` state + outside-click via a document `mousedown` listener, no
 * portal/library) — reused here so a table row's actions column can
 * collapse from N icon buttons into a single "more" trigger without
 * introducing a new UI dependency.
 */
import { useEffect, useRef, useState } from 'react';
import { MoreVertical } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface ActionMenuItem {
  key: string;
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  disabled?: boolean;
  /** Visually distinguishes a risky action (e.g. deactivate) from routine ones. */
  danger?: boolean;
}

export function ActionMenu({
  items,
  ariaLabel = 'ตัวเลือกเพิ่มเติม',
}: {
  items: ActionMenuItem[];
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div ref={ref} className="relative inline-block text-left">
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        title={ariaLabel}
        className="rounded-md border border-border bg-background p-2 text-muted-foreground shadow-sm transition-colors hover:bg-secondary hover:text-foreground"
      >
        <MoreVertical className="h-5 w-5" />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={ariaLabel}
          className="absolute right-0 z-10 mt-1 w-48 overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-lg"
        >
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                item.onClick();
              }}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50 ${
                item.danger ? 'text-destructive hover:bg-destructive/10' : 'text-foreground'
              }`}
            >
              <item.icon className="h-4 w-4 shrink-0" />
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
