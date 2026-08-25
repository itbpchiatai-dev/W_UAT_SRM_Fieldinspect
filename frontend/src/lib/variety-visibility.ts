/**
 * Round 8-25O — พันธุ์/สายพันธุ์ (variety) visibility gate.
 *
 * Chiatai's own variety choices are commercially sensitive against
 * non-Chiatai viewers (Suppliers, external accounts) — every plot/record
 * screen that shows it gates it through this ONE function so the boundary
 * can't drift between pages. UI-only, per the round 8-25O brief: the field
 * still travels in every API response regardless of caller — this hides it
 * from the rendered page and generated Excel files, never from someone
 * reading the raw network response.
 *
 * "Internal" (sees variety) = holds at least one `internal:*` role OR
 * `farmlog:supervisor` — the same trust grouping Plots.tsx's
 * FULL_SCOPE_ROLE_NAMES already uses for full cross-supplier access, though
 * this check is broader (any internal:* role, not only admin/super_admin)
 * since seeing variety is a lower bar than full scope. Everything else —
 * `supplier:*`, `farmlog:field_officer`, `external:*`, no role, or no user
 * at all — hides variety. Default is HIDE, not show: a future role name
 * this function doesn't recognize must be explicitly added to see variety,
 * never silently default to visible.
 */
export function canViewVariety(roles: { name: string }[] | undefined | null): boolean {
  if (!roles) return false;
  return roles.some((r) => r.name.startsWith('internal:') || r.name === 'farmlog:supervisor');
}
