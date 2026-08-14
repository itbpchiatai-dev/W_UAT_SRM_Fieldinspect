# FarmLog — Plot Cycle Phase Handover (rounds 7-1 → 8-1)

> **Superseded as the entry point by
> [`FARMLOG_HANDOVER.md`](FARMLOG_HANDOVER.md) (round 8-4G)** — start there
> for the full current-state picture (auth model, public phone flow,
> offline queue, Docker runtime, QA baseline). This document remains the
> authoritative deep-dive for the Plot/PlotCycle/Record concurrency,
> locking, and RLS internals below (§1-9, unchanged since round 8-1) — plus
> §10, added round 8-6K, for the plot-reactivation/status-aware-Excel-
> template phase (rounds 8-6H → 8-6K).
>
> Handover doc for the next dev/agent picking up FarmLog after the Plot →
> PlotCycle → Record redesign. This is a **feature-phase handover**, not the
> project-wide `ROADMAP.md` (that tracker predates this phase and is stale
> since ~Step 12.5 — do not retrofit this phase into it). Read this before
> touching anything under `plot_cycles`, `rollover`, `records` (append-only!),
> the Excel import `close_and_start_new_cycle` action, any
> `get_plot_for_update` / lock-ordering code, or the `records_scope`/
> `plots_scope`/`plot_cycles_scope` RLS policies.
>
> Written 2026-07-10 (round 7-13); updated 2026-07-16 (round 8-0.8) to
> cover rounds 8-0.4 → 8-0.8 (Plot/PlotCycle/Record field-ownership lock,
> Record append-only, cycleLabel + public token cycle-binding, and the Plot
> aggregate concurrency lock); **updated again 2026-07-16 (round 8-1)** for
> the RLS uuid-cast hardening migration (`0037_rls_uuid_guard`) — see §3b.

## 1. Executive Summary

FarmLog's data model changed from **Plot carrying everything** to
**Plot → PlotCycle → Record**:

- A **Plot** is the permanent physical field: supplier, plot code, GPS, QR
  key, permanent `is_active` flag. It no longer carries live crop/yield data
  directly — those live on the cycle. **Round 8.0.7**: the Plot row is also
  the **aggregate concurrency lock** for the plot + its active cycle + its
  inspection snapshot — see §3a.
- A **PlotCycle** (รอบปลูก) is one planting season: crop/variety/lot/planting
  date/plant count/expected yield/`cycleLabel` (free-text name, round 8.0,
  migration `0036`). A plot has **at most one active cycle** at
  a time (DB-enforced). Closing a cycle (harvested/cancelled) preserves its
  history; a fresh cycle can start on the same plot without ever touching the
  QR.
- **QR stays bound to the Plot**, never the cycle — printing/reprinting QR
  signs is unaffected by any cycle lifecycle event.
- A **Record** (inspection) binds to the plot's **active cycle at the moment
  it's created** — server-derived, never client-supplied. No active cycle →
  record creation is rejected (409), by design. **Round 8.0.5: Records are
  now append-only** — there is no `PATCH /records/{id}`; the only mutation
  an existing record can undergo is `POST /records/{id}/deactivate`
  (administrative correction, `records.delete`-gated). A new inspection
  always creates a new Record. **Round 8-0.6**: the public
  `inspection_session_token` is bound to the plot's active **cycle** (not
  just plot/supplier) at mint time — a token can't be used to submit into a
  *different* cycle after a rollover happened mid-session.
- **Closing a cycle ≠ deactivating a plot.** These are two independent
  concepts: `plot.is_active` (permanent) vs. "does this plot have an active
  cycle right now" (`activeCycleId`). A plot can be permanently open with no
  active cycle ("รอเริ่มรอบปลูก"), and — rarely — a plot can be deactivated
  while still holding an active cycle (deactivation doesn't auto-close it).
- **Excel import supports the full lifecycle**, including an atomic
  close-then-open **rollover** action (`close_and_start_new_cycle`), backed
  by the same transactional helper the single-plot Rollover UI uses.
- **Round 8.0.7 — Plot is the aggregate lock.** Every write path that
  touches a plot, its active cycle, or its inspection-derived snapshot now
  acquires `plot_repository.get_plot_for_update` (`SELECT ... FOR UPDATE`)
  **before** locking any `PlotCycle` row — never the reverse, to avoid
  deadlock. This closes a real race (confirmed against live PostgreSQL in
  round 8-0.8, §5a): a `deactivate record` and a concurrent `rollover cycle`
  on the same plot now deterministically serialize instead of one silently
  clobbering the other's snapshot write.
- **Round 8-1 — RLS uuid-cast hardening.** `records_scope`/`plots_scope`'s
  `'assigned'` branch now guards the `app.user_id` GUC cast with `NULLIF`
  (migration `0037_rls_uuid_guard`), matching the fix `plot_cycles_scope`
  already had since migration 0035 — see §3b. Pure hardening: no visibility
  or write-semantics change, verified live against the dev DB including a
  full downgrade→upgrade cycle.
- **This phase is QA-complete as of round 8-1** — full backend + frontend
  suites green, no known blocking bugs, a real two-session PostgreSQL lock
  test has passed, and the last known latent RLS issue is now resolved and
  verified live, ready for handover.

## 2. Data Model / Migration State

**Current alembic head: `0037_rls_uuid_guard`** (verify with
`cd backend && ./.venv/Scripts/alembic.exe current` — see §8 for why not
`python -m alembic`). Rounds 8-0.4 → 8-0.8 added **zero** new migrations
beyond 0036 — everything from the field-ownership lock through the Plot
aggregate concurrency lock is app-layer only. Round 8-1 added exactly one
migration (`0037`), a pure RLS-policy hardening with no schema/data change.

| Migration | File | What it did |
|---|---|---|
| 0034 | `backend/alembic/versions/2026_07_13_0000-0034_plot_cycles.py` | Created `plot_cycles` table + `records.plot_cycle_id` (NOT NULL), backfilled existing plots/records into cycle 1 |
| 0035 | `backend/alembic/versions/2026_07_14_0000-0035_plot_cycles_rls.py` | RLS policy on `plot_cycles`, delegated via the parent plot (plot_cycles has no `supplier_id` of its own) |
| 0036 | `backend/alembic/versions/2026_07_15_0000-0036_plot_cycle_label.py` | Added `plot_cycles.cycle_label` (nullable free-text name, e.g. "jun2026") — surfaced as `cycleLabel` on `PlotCycleRead`/`RecordRead`/`RecordSummary`, and in the Excel import template/columns |
| 0037 | `backend/alembic/versions/2026_07_16_0000-0037_rls_uuid_guard.py` | **Round 8-1** — hardened `records_scope`/`plots_scope`'s `'assigned'` branch cast from a bare `current_setting('app.user_id', true)::uuid` to `NULLIF(current_setting('app.user_id', true), '')::uuid` (same fix migration 0035 already applied to `plot_cycles_scope`). No schema change, no visibility/write-semantics change — see §3b. |

**Round 8.0.4 — Plot/PlotCycle/Record field-ownership lock** (app-layer,
no new migration): every column now has exactly one writer —
- `plots.current_crop` / `current_variety` / `current_lot_no` /
  `current_planting_date` / `plant_count` / `expected_yield_full` /
  `expected_yield_unit` are **PlotCycle-owned mirror columns**, written only
  by `plot_cycle_repository.sync_plot_mirror_from_cycle` /
  `clear_plot_cycle_mirror_and_inspection_snapshot`. `PlotCreate`/`PlotUpdate`
  no longer accept any of these fields at all — a physical-plot-only
  `POST /plots` needs a separate `POST /plots/{id}/cycles` (or the atomic
  `POST /plots/with-cycle`) to become inspectable.
- `plots.current_stage` / `current_yield_pct` / the 4 condition scores /
  `current_gps_lat` / `current_gps_lng` / `last_inspected_*` are the
  **inspection-derived snapshot**, written only by
  `plot_repository.sync_current_status_from_record` /
  `resync_current_status_from_latest` — always cycle-scoped: a record from a
  closed/older cycle can never move these (raises instead of silently
  skipping — see §3a).
- A **Record**'s own fields (crop/variety/stage/yield/scores/GPS/photos/
  notes/customFields) are frozen at create time — see the append-only note
  in §1/§3.

**Invariants (DB-enforced or app-enforced — know these before changing anything):**
- **At most one `active` cycle per plot** — partial unique index
  `uq_plot_cycles_active_per_plot` on `plot_cycles(plot_id) WHERE status='active'`.
  This is the real race-proof backstop; app code pre-checks with a row lock
  (`get_active_cycle_for_plot_for_update`) but the index is what actually
  prevents two active cycles under concurrency (surfaces as a clean 409, not
  a 500, via `IntegrityError` handling in every write endpoint).
- **Plot is the aggregate lock, and lock order is always Plot → PlotCycle**
  (round 8.0.7, verified against live PostgreSQL round 8-0.8 — see §3a).
  Never write code that locks a `PlotCycle` row before the `Plot` row for the
  same plot — that's the one ordering that can deadlock two transactions
  against each other.
- **`plot.is_active` (permanent) and "has an active cycle" are independent.**
  Never assume one implies the other.
- **QR (`plots.qr_key`) is generated once at plot creation and never
  regenerated** by any cycle lifecycle action (start/edit/close/rollover) or
  by Excel import. If you ever see code touching `qr_key` outside
  `plot_repository.create_plot`, that's a red flag — stop and check.
- **Records are never touched, edited, or deleted by any cycle lifecycle
  action.** Closing/rolling over a cycle only ever writes to `plot_cycles`
  and the plot's denormalized mirror columns — history is additive-only.
- **The plot mirror columns** (`current_crop`, `current_variety`,
  `current_lot_no`, `current_planting_date`, `plant_count`,
  `expected_yield_full`, `expected_yield_unit`) are kept in sync with the
  **active** cycle by `plot_cycle_repository.sync_plot_mirror_from_cycle`,
  and cleared when a plot has no active cycle
  (`clear_plot_cycle_mirror_and_inspection_snapshot`). They exist for
  backward-compat display; the **read-model** (`activeCycle*` fields,
  round 7.3.1) is the authoritative source for "does this plot have an
  active cycle" — see §4.

## 3. Backend Contracts

All plot-cycle routes live in `backend/app/api/v1/plots.py`; the shared
lifecycle logic (create/close/sync-mirror/rollover) lives in
`backend/app/repositories/plot_cycle_repository.py`.

| Endpoint | Method | Notes |
|---|---|---|
| `/api/v1/plots/{plotId}/cycles` | GET | List cycle history (all statuses), scoped, `plots.read` |
| `/api/v1/plots/{plotId}/cycles` | POST | Start a new cycle — 409 if an active one already exists — **locks Plot before Cycle (round 8.0.7)** |
| `/api/v1/plots/{plotId}/cycles/{cycleId}` | PATCH | Edit the **active** cycle's plan fields only — 409 if not active — **locks Plot before Cycle** |
| `/api/v1/plots/{plotId}/cycles/{cycleId}/close` | POST | Close active cycle → harvested/cancelled; clears plot mirror + inspection snapshot — **locks Plot before Cycle** |
| `/api/v1/plots/{plotId}/cycles/{cycleId}/rollover` | POST | **Round 7.9B** — atomic close-then-open in ONE transaction; returns both `closedCycle` and `newCycle` — **locks Plot before Cycle** |
| `/api/v1/plots/{plotId}` | PATCH | Physical-plot fields only — **locks Plot** (round 8.0.7; was unlocked before) |
| `/api/v1/plots/{plotId}/deactivate` | POST | Permanent close, `plots.delete`-gated — **locks Plot** |
| `/api/v1/records` / `/records/with-photos` | POST | Logged-in inspection create — **locks Plot then active-cycle** before insert + snapshot sync |
| `/api/v1/records/{recordId}/deactivate` | POST | **The only mutation an existing record can undergo** (round 8.0.5) — flips `isActive` only; **locks Plot** before the flip, then resyncs the snapshot under the same lock. There is **no `PATCH /records/{recordId}`** — it does not exist in the router at all. |
| `/api/v1/public/records` / `/records/with-photos` | POST | Public inspection create — token must name the plot's current active **cycle** (round 8-0.6); re-locks Plot then Cycle immediately before insert (round 8.0.7) |
| `/api/v1/plots/import/preview` | POST | Excel preview — **read-only**, never writes, never acquires a write lock |
| `/api/v1/plots/import/commit` | POST | Excel commit — **all-or-nothing** transaction, 4 supported actions (see below); **locks every existing plot the file touches up front, sorted by id (round 8.0.7)** |
| `/api/v1/public/plots/verify-inspection-code` | POST | Public QR gate — reads the **active cycle** for crop/variety/plan display; 404 (generic) if no active cycle |

**Permissions**: all lifecycle writes (start/edit/close/rollover) gate on
`plots.update` — no new permission was introduced for any of this phase.
RLS/scope boundaries are unchanged from before this phase; `plot_cycles` has
no `supplier_id` column, so its RLS policy delegates through the parent plot.

**Excel import — 4 actions** (`backend/app/services/plot_import.py`):
1. `create_plot_with_cycle` — new plot + its first active cycle (generates QR) — brand-new row, no existing-plot lock needed
2. `start_new_cycle` — existing plot, no active cycle → new active cycle — locked (round 8.0.7), re-checks no active cycle exists under the lock
3. `update_current_cycle` — existing plot, has active cycle → edit its plan — locked
4. `close_and_start_new_cycle` (round 7.8/7.9A) — existing plot, has active
   cycle → **atomic rollover** (reuses `plot_cycle_repository.rollover_cycle`,
   the same helper the single-plot Rollover endpoint calls — these two
   callers cannot drift from each other) — locked

Preview is always read-only; commit re-validates server-side (never trusts
a client-sent preview) and is one transaction — any invalid row means
**nothing** is written. **Round 8.0.7**: before executing any row, commit
collects every EXISTING plot the file's rows will mutate and locks them all
up front, in one deterministic order (sorted by plot id) — this is what lets
two admins committing two different Excel files at the same time, whose rows
happen to reference overlapping plots in a different order, avoid
deadlocking each other. A plot deactivated between preview and commit fails
the whole file at this lock step (all-or-nothing, unchanged).

**Round 8-6A/B/C — filtered "next cycle" template**: `GET
/plots/import-template` takes an optional Supplier/province/crop/variety/
applied-search filter (frontend: the Plots page's "Excel ตามตัวกรอง"
button — a Supplier must be selected first). With any filter set, the
response is a 3-sheet workbook seeded from real ACTIVE plots instead of the
generic blank template:
- **นำเข้ารอบใหม่** (Sheet 1 — the ONLY sheet the importer reads) — one
  `start_next_cycle` row per matching active plot. Identity/reference
  columns are read-only (grey); the editable planting-cycle columns
  (crop/variety/`cycleLabel`/poNumber/pCode/lotNo/plantingDate/plantCount/
  expectedYieldFull/expectedYieldUnit) are highlighted yellow.
  `cycleLabel` echoes the plot's CURRENT active cycle label and **must be
  changed** to a new name before commit, or Preview blocks the row with
  `same_active_cycle_label`. `lotNo`/`plantingDate` are always left blank
  on this sheet — Auto Lot runs when `lotNo` is blank and a PO is supplied.
- **ข้อมูลปัจจุบัน** (Sheet 2) — a reference-only snapshot of the current
  cycle (old lotNo/plantingDate/cycleLabel intact) for the user to compare
  against before editing Sheet 1; never read by the importer.
- **ตัวอย่าง** (Sheet 3) — the same worked examples as the generic
  template, styled red; never read by the importer either.

Downloading and Previewing this file **never writes** — same read-only
guarantee as the rest of this table — only a successful **Commit** closes
the plot's current cycle and starts the new one. Import stays
all-or-nothing (unchanged, see above).

**Record creation requires an active cycle** — `records._create_record`
resolves the plot's active cycle server-side and snapshots
crop/variety/planting_date from it (never from client input); no active
cycle → 409. **Round 8.0.5 — Records are append-only**: once created, a
record's inspection fields (crop/variety/stage/yield/scores/GPS/photos/
notes/customFields) can never be changed; a new inspection always creates a
new Record; the only allowed mutation is `POST /{recordId}/deactivate`.
`cycleLabel` (round 8.0) is echoed on `RecordRead`/`RecordSummary` from the
record's OWN bound cycle (`record.plot_cycle.cycle_label`) — never from the
plot's current active cycle, since a historical record's cycle may since
have closed/rolled over.

## 3a. Concurrency / Locking Contract (round 8.0.7, verified live round 8-0.8)

**The problem this closed**: before round 8.0.7, no write path locked the
`Plot` row itself — only the active `PlotCycle` row (and only on some
paths). A `deactivate record` and a concurrent `rollover cycle` on the same
plot could interleave: the deactivate reads "the active cycle is X", the
rollover replaces X with Y and clears the snapshot, then the deactivate's
snapshot resync (still thinking the active cycle is X) writes stale data
back — a real race with no lock preventing it.

**The fix — Plot is the aggregate lock.** `plot_repository.get_plot_for_update`
(`SELECT ... FOR UPDATE`, `backend/app/repositories/plot_repository.py`) is
now acquired **first**, before any `PlotCycle` row lock, by every write path
that touches: the plot itself, its active cycle, or its inspection-derived
snapshot. **Lock order is always Plot → PlotCycle, never the reverse** — a
reversed order across two code paths is exactly what could deadlock two
transactions against each other.

Paths that acquire this lock (see §3's table for the full per-endpoint
list): PATCH plot, deactivate plot, start/update/close/rollover cycle,
logged-in record create, public record create (re-locked immediately before
insert, after `_verify_and_resolve`'s earlier unlocked read), record
deactivate, and Excel import commit (locks every existing plot the file
touches, sorted by id, before executing any row). `plot_repository.
sync_current_status_from_record` / `resync_current_status_from_latest`
acquire the lock **themselves** internally too — re-locking a row the caller
already locked moments earlier in the same transaction is safe (no
self-deadlock in PostgreSQL), so these stay correct regardless of what
future caller reaches them.

**Deliberately NOT locked**: `PUT /plots/{id}/assignments` (doesn't touch
lifecycle/mirror/snapshot) and all read-only endpoints (`GET`, Excel
preview, `verify-inspection-code`). If a future round expands the
assignments endpoint to affect snapshot-adjacent state, revisit this
exemption.

**Verified against real PostgreSQL, round 8-0.8**: a throwaway two-session
script (`get_plot_for_update` in session A, then session B) proved session B
genuinely blocks (timed out waiting) while session A holds the lock, and
acquires successfully immediately after session A rolls back — not just
correct call-order in mocked unit tests. See §5a. **Still missing**: a real
concurrent-WRITE integration test (two sessions each completing a full
mutation, e.g. deactivate-vs-rollover, against a live DB) — see §7.

## 3b. RLS UUID-Cast Hardening (round 8-1, migration 0037)

**The problem this closed** (previously risk #3 in §7, now **resolved**):
`records_scope`/`plots_scope` (migration 0016)'s `'assigned'` branch does
`user_id = current_setting('app.user_id', true)::uuid` inside an
**uncorrelated subquery**. PostgreSQL's planner hoists that subquery into an
InitPlan that runs once, unconditionally, regardless of which `CASE` branch
(`'all'`/`'supplier'`/`'assigned'`) actually applies at runtime. So an
empty/missing `app.user_id` GUC could throw `invalid input syntax for type
uuid` even under `scope='all'`/`'supplier'`, where `app.user_id` is never
otherwise consulted. Migration 0035 already fixed this for
`plot_cycles_scope`; round 8-1 brought `records_scope`/`plots_scope` in line
with the same fix.

**The fix**: `NULLIF(current_setting('app.user_id', true), '')::uuid` — an
empty string becomes `NULL` (no match, no crash) instead of a cast error.
Applied via `ALTER POLICY` in migration `0037_rls_uuid_guard`, at **exactly
4 points**: `records_scope` USING + WITH CHECK, `plots_scope` USING + WITH
CHECK. `plot_cycles_scope` was not touched (already guarded since 0035).

**Scope/permission semantics are unchanged** — this was pure hardening:
- `all`/`supplier`/`assigned`/`ELSE false` branches are identical to 0016,
  just as before.
- Write (`WITH CHECK`) semantics unchanged — `records_scope`/`plots_scope`
  both still allow `assigned` writes in `WITH CHECK` exactly as 0016 did
  (unlike `plot_cycles_scope`, which deliberately excludes `assigned` from
  `WITH CHECK` — a pre-existing, unrelated difference, not something this
  round changed).
- Table/role/policy names, `FOR ALL` scope, grants, and RLS enable/force
  flags are all untouched.
- The app-layer `_NO_USER_ID` sentinel (`app/api/deps/scope.py`) is **kept**,
  not removed — it's now defense-in-depth on top of the DB-level fix, not a
  replacement for it.

**Verified, round 8-1**:
- Offline: `alembic upgrade/downgrade --sql` rendered exactly the expected
  `ALTER POLICY` statements, no `INSERT`/`UPDATE`/`DELETE` on application
  data (only the standard `alembic_version` bookkeeping row).
- Applied to local dev DB; `pg_policies` confirmed the guarded cast at all
  4 points and `plot_cycles_scope` unchanged; `relrowsecurity`/
  `relforcerowsecurity` still `true` on all 3 tables.
- Full downgrade → confirmed policies revert to 0016's exact bare-cast text
  → re-upgrade → confirmed back at `0037` with the guard restored.
- **Live RLS matrix** (running as the RLS-enforced `srm_app` role, not a
  bypass role): `scope='all'` + empty `user_id` → full visibility, no
  crash; `scope='supplier'` + empty `user_id` + real supplier → sees
  exactly that supplier's plots; `scope='assigned'` + empty `user_id` → 0
  rows, no crash; `scope='assigned'` + a **real** assigned user → sees
  exactly their assigned plot(s); `scope='assigned'` + a syntactically
  valid but nonexistent UUID → 0 rows, no leak; `plot_cycles` visibility
  unchanged. Row counts on `plots`/`records`/`plot_cycles`/
  `plot_assignments` identical before and after — **no application data
  was mutated**.
- **Rollback path**: `alembic downgrade 0036_plot_cycle_label` — tested
  live this round (see above), restores the exact pre-0037 policy text.

## 4. Frontend Contracts

| Area | File | Behavior |
|---|---|---|
| Plot List | `frontend/src/pages/farmlog/admin/Plots.tsx` | Shows plot status (ใช้งาน/ปิดแล้ว) **and** cycle status (กำลังปลูก/รอเริ่มรอบปลูก) as two independent badges; yield sourced from `activeCycle*` fields, not the mirror |
| Plot Detail | `frontend/src/pages/farmlog/admin/PlotDetail.tsx` | Current-cycle card, cycle history (with "รอบที่ N" badges), and the **Rollover modal** trigger ("จบรอบ + เริ่มรอบใหม่") — distinct from any plot-deactivate action (there isn't one on this page) |
| Rollover modal | `frontend/src/components/farmlog/PlotCycleModals.tsx` (`RolloverCycleModal`) | Single API call to the rollover endpoint — **never** calls close-cycle then start-cycle as two requests |
| Plot picker (new record) | `frontend/src/components/farmlog/SmartPlotPicker.tsx` | **Round 7.11** — a plot with no active cycle is shown (not hidden) but disabled, badge "รอเริ่มรอบปลูก", tooltip; QR-scan match on such a plot shows a Thai error instead of selecting |
| Public Inspect | `frontend/src/pages/farmlog/PublicInspect.tsx` | Crop/variety/lot/planting-date shown **read-only** from the verified active cycle; field worker can never edit them |
| Logged-in RecordForm | `frontend/src/pages/farmlog/RecordForm.tsx` | Same read-only treatment for crop/variety/planting-date; a 409 (picked a no-active-cycle plot anyway) shows a friendly Thai fallback message, not the raw backend detail |
| Reports/History | `PlotStatusReport.tsx`, `RecordPreview.tsx`, `PlotDetail.tsx` history | Sourced from the **active cycle** / the record's own bound cycle — a plot that rolled over does not show its old cycle's data as current, and a historical record keeps showing the cycle it actually belongs to |
| Record List / Preview / Plot Detail history | `RecordList.tsx`, `RecordPreview.tsx`, `PlotDetail.tsx` | **Round 8.0.5** — cycle badge shows `cycleLabel` first if present, falls back to "รอบที่ N", then generic "รอบปลูก"; always reads the RECORD's own cycle, never the plot's current active cycle |
| Record edit | *(removed, round 8.0.5)* | There is no edit UI for an existing record — `RecordForm` is create-only; `/farmlog/records/:id` redirects straight to the read-only `RecordPreview` |

## 5. QA / Test Baseline (as of round 8-1)

| Check | Result |
|---|---|
| Backend full suite (`pytest -q`) | **674 passed** (663 + 11 new migration/RLS tests, round 8-1) |
| Frontend tests (`npm run test`) | **355 passed / 31 files** (unchanged — round 8-1 did not touch frontend) |
| Frontend typecheck / lint / build | all ✅ (build has a pre-existing chunk-size warning, unrelated to this phase); not re-run in round 8-1 per its explicit no-frontend-touch scope |
| Backend ruff (`ruff check app tests`) | **16 pre-existing findings**, none in any file this phase touched (unchanged from round 8-0.8; round 8-1's new files are ruff-clean) |
| Alembic | `current == heads == 0037_rls_uuid_guard` |
| Browser automation | **none available** in any session this phase — verified instead via route-load smoke + the unit/component test suites above. **No real click-through/visual QA has been done** (unchanged gap, see §7). |
| DB mutation this phase | **none** — every round (including the round 8-0.8 live PostgreSQL lock test and round 8-1's migration apply + live RLS matrix, §5a/§3b) either rolled back every read-only session, or (for the one real schema-level change, migration 0037 itself) only altered policy *definitions* — never application data — and was proven reversible by a live downgrade→upgrade cycle |

## 5a. PostgreSQL Two-Session Lock Verification (round 8-0.8)

Round 8.0.7 added the Plot aggregate lock but only proved it via mocked
unit tests (correct call order/wiring, not real DB blocking). Round 8-0.8
ran a throwaway script against the live dev DB to close that gap:

1. Session A calls `plot_repository.get_plot_for_update(plotId)` and holds
   the transaction open (no commit).
2. Session B calls the same function on the same plot with a short
   `asyncio.wait_for` timeout — **confirmed to time out (blocked)** while A
   holds the lock.
3. Session A rolls back (releases the lock, nothing committed).
4. A fresh session calls `get_plot_for_update` again — **confirmed to
   acquire immediately**.
5. Plot state (non-sensitive fields) compared before/after — **identical,
   no persistent mutation**.

**Result: PASS.** This is real evidence of PostgreSQL-level blocking, not
just mocked-test call order. The script used the SMOKE plot (§6), ran under
the same RLS/session helpers the app itself uses (`get_db_session`,
`_set_rls_config`), and was deleted after running — it is not part of the
committed test suite (deliberately: it needs a live DB, which the rest of
this repo's test suite does not).

**Still not covered**: a full concurrent-**write** integration test (e.g.
two sessions racing a real deactivate against a real rollover end-to-end,
including the actual UPDATE statements) — this round only proved the lock
primitive itself blocks/releases correctly, not the full read-modify-write
sequences built on top of it. See §7.

## 6. Dev DB Sample Data

One plot was created via live import-commit smoke testing (rounds 7-7 through
7-9A) and has been **kept intentionally** as a QA sample:

| Field | Value |
|---|---|
| plotCode | `SMOKE-CYCLE-20260709-112623` |
| supplierCode | `SUP001` |
| isActive | `true` |
| Cycles | 3 total — cycle 3 **active**, cycles 1–2 harvested (a complete, real rollover history) |
| Records | 0 (still 0 after round 8-0.8's lock test — that test never wrote anything) |
| qrKey | present (never print the full value — see §8) |

**Decision (round 7-12, user-approved): KEEP as a QA sample.** Do not hard
delete. Round 8-0.8 reused it again (read-only, §5a) — still not cleaned up.
If cleanup is ever wanted, the only sanctioned path is
`POST /api/v1/plots/{id}/deactivate` (soft, API-only) — never a direct SQL
delete/update, and never without asking first.

## 7. Known Risks / Deferred Items

1. **No real browser/visual QA has been performed this entire phase** — every
   round relied on route-load smoke + unit/component tests. Recommend a
   manual click-through pass (Plot List → Plot Detail → Rollover modal →
   SmartPlotPicker no-active-cycle state → Public Inspect) before treating
   this phase as production-verified.
2. **Frontend bundle ~1.99 MB** (main JS chunk) — pre-existing, unrelated to
   this phase, flagged by every build. Candidate for `dynamic import()`
   code-splitting in a dedicated round.
3. ~~**Latent RLS `uuid`-cast issue**~~ — **RESOLVED, round 8-1** (migration
   `0037_rls_uuid_guard`, see §3b). The `'assigned'` branch of
   `records_scope`/`plots_scope` now uses
   `NULLIF(current_setting('app.user_id', true), '')::uuid`, same as
   `plot_cycles_scope` already did since migration 0035. Verified live
   against the dev DB (§3b) — including a correction to this risk's earlier
   assumption: **this dev environment's `.env` already has `DB_APP_USER=
   srm_app` set**, so the app runtime *does* connect as the RLS-enforced
   `srm_app` role here, not a bypassing owner role — the crash risk was live
   in this dev DB too, not just a theoretical production-only concern. The
   `_NO_USER_ID` sentinel (`app/api/deps/scope.py`) is kept as
   defense-in-depth on top of the now-fixed DB policy.
4. **Large / split working tree** — this worktree only carries a subset of
   the real project; the actual committed history lives in the parent
   project on branch `master` (see the "FarmLog repo split" note this repo's
   contributors already know about). **Any commit/handover of this phase's
   changes must happen at the parent project, not from inside this
   worktree** — `git status` here will always show a large untracked set
   that is not this phase's doing.
5. **The `SMOKE-CYCLE-...` sample plot is live in dev data** (§6) — anyone
   running a "how many plots do we have" query or a supplier-level report
   against dev should expect to see it under SUP001.
6. **No full concurrent-WRITE integration test exists yet** (round 8-0.7 /
   8-0.8) — the lock PRIMITIVE (`get_plot_for_update` blocks/releases
   correctly) is now proven against a real PostgreSQL instance (§5a), and the
   lock ORDERING (Plot before PlotCycle, everywhere) is proven by mocked
   unit tests + source-position guards. What's still missing is a test that
   runs two REAL competing full mutations end-to-end (e.g. an actual
   deactivate-record transaction racing an actual rollover-cycle transaction,
   both against a live DB, asserting the final committed state is whichever
   one "won" cleanly with no partial write) — that needs a DB-backed test
   fixture this repo doesn't have yet (see recommendation in §9).
7. **`sync_current_status_from_record` / `resync_current_status_from_latest`
   re-lock the Plot row even when the caller already holds it** (round
   8.0.7) — safe (re-locking a row your own transaction already holds is a
   no-op wait in PostgreSQL, never a deadlock) but does cost one extra
   round-trip on every record create/deactivate. Not worth optimizing away
   unless it shows up in real latency profiling — flagged here so nobody
   "fixes" it as a bug.

## 8. Do Not Touch / Safety Notes

- **Never reset or reseed the dev DB** without explicit, fresh confirmation.
- **Never hard-delete** a plot, cycle, or record — every lifecycle action in
  this phase is additive/soft by design (history is a feature, not an
  accident).
- **Never change QR/`qr_key` generation or the public deep-link contract** as
  a side effect of cycle work — QR belongs to the Plot, full stop.
- **Never bundle changes to users/roles/auth/RLS policy into a UI/feature
  round** — if a real RLS/permission bug is found, stop, report the root
  cause, and propose it as its own round (round 8-1 followed exactly this
  for risk #3, now resolved — see §3b).
- **Never hand-edit migrations 0034/0035/0036/0037** (already applied) — any
  further schema change is a **new** migration.
- **Never mutate the DB during a QA/read-only round** without the user
  explicitly approving that specific action first — every round in this
  phase that needed a live write (import commit smoke, the rollover smoke)
  asked first; the round 8-0.8 live PostgreSQL lock test (§5a) stayed
  read-only by always rolling back, never committing.
- **Never lock a `PlotCycle` row before the `Plot` row for the same plot**
  (round 8.0.7) — that ordering is what can deadlock two transactions
  against each other. Every new write path that touches a plot/cycle/
  snapshot must call `plot_repository.get_plot_for_update` first.
- **Never refactor away the "duplicate" Plot lock** in
  `sync_current_status_from_record`/`resync_current_status_from_latest`
  (risk #7 in §7) without discussing the tradeoff first — it's deliberate,
  not an oversight.
- **Alembic gotcha specific to this repo**: `backend/alembic/__init__.py`
  shadows the real `alembic` package when run as `python -m alembic` from
  inside `backend/` — use the venv's `alembic.exe` entry point directly
  (`./.venv/Scripts/alembic.exe current`), not `python -m alembic`.

## 9. Recommended Next Rounds

1. ~~Production hardening: the RLS `NULLIF` migration for the `'assigned'`
   scope uuid-cast~~ — **done, round 8-1** (migration `0037_rls_uuid_guard`,
   see §3b).
2. **Manual browser QA pass** — the one gap every round in this phase has
   had to defer for lack of tooling. Should cover at minimum: Plot List
   badges, Plot Detail + Rollover modal end-to-end (with explicit approval
   before any live submit), SmartPlotPicker's disabled/badge state, Public
   Inspect on a real device.
3. **Frontend bundle code-splitting** (risk #2) — not urgent, no functional
   impact, but the warning has been present for several rounds.
4. **Optional**: decide the long-term fate of `SMOKE-CYCLE-...` once manual
   QA (item 2) no longer needs it as a multi-cycle example.
5. **User-facing documentation**: a short SOP for suppliers/admins on how to
   use the Excel import's `close_and_start_new_cycle` action and the
   Rollover modal — these are new concepts (รอบปลูก, closing vs.
   deactivating) that end users haven't seen documented yet, distinct from
   this technical handover.
6. **Full concurrent-write integration test** (risk #6 in §7) — needs a
   DB-backed test fixture (this repo currently has none; every existing test
   is DB-less/mocked). Would exercise real two-session races end-to-end
   (deactivate-vs-rollover, two Excel imports touching the same plot, etc.)
   rather than just the lock primitive §5a already proved.

## 10. Plot Status Filter, Explicit Reactivation & Status-Aware Excel Template (rounds 8-6H → 8-6K)

**Plot status filter contract.** `plot_status: Literal["all","active","inactive"]`
(default `"all"`) is the one true filter on `GET /plots`, `GET /plots/
provinces`, and `GET /plots/import-template` — shared repo helper
`plot_repository._apply_plot_status_filter`. `active_only=true` (pre-existing
param) is kept for backward compatibility, treated as exactly equivalent to
`plot_status=active`; the only rejected combination is `active_only=true` +
`plot_status=inactive` (422, genuinely contradictory).

**Explicit reactivation contract (round 8-6H).** Reopening an inactive Plot
is NEVER inferred from any other action. Two dedicated paths, both requiring
BOTH `plots.delete` (the activation privilege) AND `plots.update`:
`POST /plots/{id}/reactivate` (activate only, no cycle) and `POST /plots/
{id}/reactivate-with-cycle` (activate + open the first new cycle,
atomically, via `plot_repository.reactivate_plot_with_cycle`). The Excel
importer's `reactivate_plot_with_cycle` action (round 8-6H Part F) mirrors
the with-cycle endpoint exactly — same repo helper, no parallel
`is_active=True` assignment anywhere in the importer.

**Excel status-aware template (round 8-6J).** `GET /plots/import-template`
takes the same `plot_status` filter, on both the single-Supplier and
`template_mode=all_suppliers` modes (never the pre-8-6A no-filter generic
template, which stays byte-for-byte unchanged). Sheet 1 ("นำเข้ารอบใหม่")
dispatches per plot: active → `start_next_cycle` row; inactive →
`reactivate_plot_with_cycle` row. `plot_status="all"` (default) puts BOTH
kinds of row in the same sheet — an inactive plot is no longer
unconditionally routed to the "รายการที่ไม่รวม" (excluded) sheet; it's only
excluded there now when the caller's `plot_status` filter itself excludes it
(e.g. asked for `active` only), or when its Supplier is inactive.

**`currentPlotStatus` is informational-only.** A reference-style (non-
editable, gray) column appended to `IMPORT_COLUMNS` — `plot_import.py`'s
`_parse_row`/`_Parsed` never read it at all, so editing this cell in a
downloaded-then-reuploaded file has **zero** effect on which operation runs;
`action` is the only column that decides that. Verified both by a unit test
(`test_editing_current_plot_status_cell_never_changes_the_row_action`) and
live against the dev DB (round 8-6K Part F Scenario 3 — see below). A legacy
22-column file (downloaded before this column existed) still imports
unchanged — the reader maps columns by header NAME, not position.

**Active Sheet 1 row: lotNo/plantingDate deliberately blank.** For an ACTIVE
plot's `start_next_cycle` row, `lotNo` and `plantingDate` are ALWAYS blank —
by design, not an oversight:
  - `lotNo` blank → the backend's Auto Lot generator runs, producing
    `{cycleLabel}-{supplierCode}-{pCode}-{running}` (V2 formula, round
    8-12A — see §11 for the full contract); a non-blank value would force
    Manual mode for what's meant to be a fresh lot. The row must carry a
    nonblank `cycleLabel` and `pCode` for Auto to succeed (both are
    otherwise-optional columns that become required the moment `lotNo` is
    left blank); `supplierCode` in the formula is always the resolved
    `Supplier.code` from the Plot/Supplier relationship on the server, never
    the row's own `supplierCode` cell. **`poNumber` and `plotCode` are not
    part of the formula at all** — a row can (and normally does) carry a PO
    Number, but it never appears in the rendered lot text.
  - `plantingDate` blank → it must be the NEW cycle's planting date, never
    silently copied from the cycle being closed.
  This behavior is UNCHANGED by round 8-6K — do not "fix" it in a future
  round without discussing first; Sheet 2 ("ข้อมูลปัจจุบัน") is where the
  OLD lotNo/plantingDate/cycleLabel stay visible, for comparison only (that
  sheet is never read by the importer).

**Inactive row: seeded from latest HISTORICAL cycle.** Unlike the active
row, a `reactivate_plot_with_cycle` row's planting-cycle fields (crop,
variety, cycleLabel, poNumber, pCode, lotNo, plantingDate, plantCount,
expectedYieldFull/Unit) are pre-filled from the plot's most recent cycle
regardless of status (active/harvested/cancelled) — an inactive plot has no
active cycle by definition, and its mirror columns may already be cleared by
the deactivate flow, so history is the only reliable source. `cycleLabel` is
deliberately copied too (not blanked), so the user SEES the old value and
can rename it — reusing it unedited is caught by the batch duplicate check
below. A plot with no cycle history at all still gets a row, every field
blank (never invented).

**Batch cycle-label validation (round 8-6K Part B — N+1 fix).** The
reactivate-row cycleLabel-reuse check (round 8-6J Part E: a row's cycleLabel
must not reuse ANY of the plot's historical labels) originally called
`plot_cycle_repository.get_cycles_for_plot` once PER ROW inside
`_validate_row` — an N+1 for a file with many reactivation rows. Round 8-6K
replaced this with a two-phase `_validate_all`: the per-row pass now only
flags `state.needs_cycle_label_history_check` (no DB call); a second pass
(`_apply_cycle_label_history_checks`) collects every flagged row's
`plot.id` and calls the new `plot_cycle_repository.get_cycle_labels_for_
plots(db, plot_ids)` — **exactly one query for the whole file**, returning
`dict[plot_id, set[str]]` (raw labels only, `cycle_label IS NOT NULL`
filtered in SQL). Normalization (trim + casefold) still happens in Python
(`plot_import._label_reused_in_history_labels`), matching the exact contract
the single-row check had — same error message, same `error_code
="reactivate_cycle_label_reused"`. Preview and Commit both go through
`_validate_all`, so both get the batched check identically. A row that
already failed earlier (bad supplier/plot/action, wrong permission) never
sets the flag, so it's never part of the batch.

**Latest test baseline (round 8-6K).** Backend: **1348 passed** (full
`pytest tests/ -q --no-cov`), including a new dedicated file
`test_plot_import_cycle_label_batch_validation.py` (8 tests: repo helper
query-count/grouping + the normalization comparison helper) plus 4 new tests
in `test_plot_import_reactivate_action.py` (blank-label no-query, N-rows-one-
query, zero-reactivate-rows no-query, earlier-failure excluded from batch).
Frontend: **1031 passed / 47 files** (unaffected — round 8-6K touched
backend only). `ruff check`/`py_compile` clean on every changed file;
`tsc --noEmit`/`eslint`/`npm run build` all clean (frontend unchanged).
Alembic `current` == `heads` (`0042_plot_cycle_po_lot`) — **no migration
this round or the two before it (8-6H/8-6I/8-6I.1/8-6J/8-6K)**.

**Browser QA status — still not done.** Every round since at least 8-6H has
deferred real browser/visual QA for lack of an automation tool in this
environment; round 8-6K is no exception. Nothing here has been verified in
an actual browser — only unit/component tests (jsdom) and, for round 8-6K
specifically, live-but-headless Python calls straight into the backend
service layer against the real dev DB (see below). A manual checklist for a
human to run through in a real browser is included in this round's Final
Report.

**No-mutation evidence (round 8-6K, live dev DB).** Read-only snapshot of
the one real inactive plot in this dev DB, **SUP010-P002** (`plot_id=
fbd43d6d-…`, `is_active=False`, `updated_at=2026-07-03 06:38:51.534572+00`,
1 cycle — cancelled, no label — 1 record, 1 access phone, `qr_key` present)
taken BEFORE and AFTER exercising: the full 6-cell Download matrix (SUP010 ×
{active,inactive,all}, all-suppliers × {active,inactive,all}) and 4 Preview-
only scenarios (valid reactivate, `currentPlotStatus` tamper, wrong-action ×
2) directly against this row. Every field was byte-identical before/after —
**zero INSERT/UPDATE/DELETE**, confirming Download and Preview are truly
read-only end to end, not just by code inspection. (SUP010's plot code in
this DB is literally `"SUP010-P002"`, not `"P002"` — the code column
includes the Supplier prefix; keep that in mind for any future script
against this same fixture.)

## 11. Auto Lot Formula V2 + Supplier Lot No (rounds 8-12A → 8-12C)

**Migrations 0048 (`supplier_lot_auto_lot_v2`) and 0049
(`auto_lot_v2_integrity`)** — current alembic head as of round 8-12C is
`0049_auto_lot_v2_integrity`. Both are additive (new nullable columns + new
partial indexes/constraints); **never hand-edit either** — any further
schema change here is a new migration.

**The V2 formula** (`backend/app/services/lot_number.py`):

```
{cycleLabel}-{supplierCode}-{pCode}-{running}
```

e.g. `2605-SUP010-WM-141-003`. Replaces V1's `{PO}-{plotCode}-{running}`
(migration 0042, round 8-5A) for every **new** Auto Lot row; V1 rows already
in the DB are untouched and keep rendering/behaving exactly as before —
there is no backfill and none is planned.

- **`cycleLabel` is free-form** — whatever the user typed (`2605`, `26-may`,
  `MAY26`, …), used **verbatim**. Never parsed as a date/year/month. Do not
  "fix" a formula that looks odd for some label value — that is by design.
- **`supplierCode`** comes from the row's **resolved `Supplier.code`**
  (server-side, RLS/scope-checked), never the raw `supplierCode` cell the
  user typed — see `ctx_supplier_code` in `plot_import.py`. This is what
  stops a file from mislabeling another supplier's lot.
- **`pCode`** is used in full, never truncated/abbreviated.
- **`running`** is zero-padded to a **minimum of 3 digits** and grows past
  999 naturally (`1000`, `1001`, …) — never wraps, never truncates, never
  caps. Allocated **only at commit**, under the Plot lock; every preview
  (Excel Preview, the Start/Edit/Rollover modal) shows the placeholder
  `###` instead of a real number, so preview can never reserve/guess one.
- **Running scope is `(supplierCode, cycleLabel, pCode)`, counted ACROSS
  plots** — a deliberate change from V1, which counted per `(plot, PO)` and
  legitimately reused the same running number across different plots (live
  V1 data confirms this: two plots under one supplier can both have running
  01/02). V2 has no plot code in the formula, so without the cross-plot
  scope two plots opening a cycle with the same label+pCode would mint an
  identical lot — the scope exists specifically to prevent that.
- **Manual always wins.** Any nonblank `lotNo` on a create/rollover/edit row
  is used **verbatim** — the Auto generator never runs, regardless of
  whether cycleLabel/pCode are present. Re-verified live in round 8-12C
  (Part C negative-preview check).
- **PO Number stays a real `PlotCycle` field** (`po_number`, still
  displayed, still stored, still shown on the Cycle Yield Report) but is
  **not part of the Auto Lot formula at all** in V2 — a row can have a PO
  and the lot text will never contain it.
- **`auto_lot_series_key`** (`plot_cycles.auto_lot_series_key`, migration
  0048) is the **internal** bookkeeping key a V2 running number is scoped
  to — a length-prefixed encoding (`v2|6:SUP010|4:2605|6:WM-141`, never a
  plain delimiter-join, since any separator a user can type is forgeable).
  Server-derived only, **never in any request schema and never returned by
  any API response** — grep the OpenAPI schema for `autoLotSeriesKey` before
  trusting any future client work near this; it must always come back
  empty. `NULL` on a row ⟺ that row is V1/legacy/manual, never a V2 auto
  row.

**DB constraints/indexes (migrations 0048/0049)** —
`backend/app/db/models/plot_cycle.py`:
- `uq_plot_cycles_auto_lot_running` — V1's original per-`(plot_id,
  po_number, lot_running_no)` uniqueness, now scoped with
  `auto_lot_series_key IS NULL` so it only ever applies to V1/legacy rows.
- `uq_plot_cycles_auto_lot_series_running` — the V2 backstop: one running
  number per `(auto_lot_series_key, lot_running_no)`, `auto_lot_series_key
  IS NOT NULL`. This is what actually prevents a running collision under
  concurrency (surfaces as a clean 409 — see below — never a 500).
- `uq_plot_cycles_auto_lot_v2_lot_no` (round 8-12A.1, migration 0049) —
  belt-and-suspenders text-uniqueness on the rendered `lot_no` itself for V2
  rows, since two *different* series can in theory render the same text
  when a component contains `-`.
- A `CHECK` constraint requires every `lot_no_source='auto'` row to carry
  both `lot_no` and `lot_running_no`, and to satisfy either the V1 branch
  (`auto_lot_series_key IS NULL AND po_number IS NOT NULL`) or the V2 branch
  (`auto_lot_series_key IS NOT NULL AND cycle_label/p_code both
  non-blank`) — an Auto row can never end up with a NULL lot from a
  half-finished request.

**Supplier Lot No** (`plot_cycles.supplier_lot_no`, migration 0048,
`String(100)`) — a completely independent, optional, free-form identifier
the **Supplier** assigns to a cycle. Client-writable
(`PlotCycleCreate`/`PlotCycleUpdate`/rollover's `newCycle`). **No
uniqueness constraint** — duplicates across plots/cycles are allowed and
expected (verified live, round 8-12C Part C). It never feeds the Auto Lot
formula, the running number, or the Manual/Auto decision, and is never
displayed merged into the system `lot_no` — every surface (Plot Detail,
cycle history, Plots list, Excel Import Preview, Cycle Yield Report JSON +
Excel) renders it as its **own** field/column, `—`/`null` when absent.

**Excel import** (`backend/app/services/plot_import.py`,
`IMPORT_COLUMNS`) — `supplierLotNo` is a normal input column, alongside the
existing `lotNo`. Preview's `proposedLotNo` shows the V2 formula with the
`###` placeholder for Auto rows (`_compute_lot_preview`); Preview never
allocates a running number and never writes anything (verified live, round
8-12C Part C: DB row/fingerprint identical before and after a Preview
call). A row missing `cycleLabel` or `pCode` while asking for Auto (`lotNo`
blank) is rejected at Preview with a clear Thai error — never silently
falls back to Manual or a partial lot. Commit re-validates server-side
(never trusts the client's preview) inside the same one-transaction,
all-or-nothing contract as every other import action.

**Concurrency / race behavior** — verified **live against real PostgreSQL**
in round 8-12C (not mocked): two rollover requests fired truly concurrently
(same new `cycleLabel`+`pCode`, different plots, both Auto) via
`POST /plots/{plotId}/cycles/{cycleId}/rollover` produced exactly the
accepted outcome — **one 200 with `lotRunningNo=1`, one clean 409** (the
loser hit `uq_plot_cycles_auto_lot_series_running` → `IntegrityError` →
mapped to the same generic "Plot already has an active planting cycle" 409
every other lock-loss path already used — see §3's `IntegrityError`
handling). Confirmed the losing transaction left the plot's original
active cycle **completely untouched** (same `activeCycleId`, same lot,
still exactly one cycle row) before retrying; the retry then succeeded
with `lotRunningNo=2` — the next number in the same series. No HTTP 500 at
any point, no duplicate `lot_no`, no duplicate
`(auto_lot_series_key, lot_running_no)` pair, and the two rows'
`supplierLotNo` values never crossed plots.

**Report display** (`backend/app/schemas/report.py` `ReportCycleYieldRow`,
`backend/app/repositories/report_repository.py` `cycle_yield_rows`,
`backend/app/api/v1/reports.py` Cycle Yield Report) — round 8-12C added
`supplierLotNo`, read **verbatim** off the row's own `PlotCycle` in the
SAME single join query the report already used (no N+1, no extra query).
The Excel export gained a `Supplier Lot No` column immediately after `Lot
No ระบบ`/`ที่มา Lot`; `null` renders as a blank cell, never `—` in the
spreadsheet (the frontend renders `—`). The frontend (`CycleYieldReport.tsx`)
shows System Lot and Supplier Lot on separate lines — never merged into one
string.

**QA plots created this round (round 8-12C Part C/D), final state: gone.**
Two throwaway plots, `QA-AUTOLOT-812C-<run_tag>-A` / `-B` (SUP001), were
created live via the Excel Import commit API to exercise the formula,
Preview, and the concurrency race end-to-end, then rolled over once more
(concurrency test) and finally **closed (`cancelled`) + deactivated** via
the real lifecycle API — never a direct SQL write, never hard-deleted.
History (2 cycles each, 4 total) is preserved exactly like any other closed
plot; no `Record` was ever created against them (`records` count unchanged
across the entire round: 346 before and after). If a future round wants to
purge them entirely, hard-delete is still against house rules — ask first.

**Latest test baseline (round 8-12C).** Backend: **2146 passed** (full
`pytest -q --no-cov`). Frontend: **1409 passed / 50 files** (`npx vitest
run`), typecheck/lint/build all clean (build has the same pre-existing
chunk-size warning as every prior round, unrelated). `ruff check` /
`py_compile` clean on every file this round touched. Alembic `current ==
heads == 0049_auto_lot_v2_integrity` — **no new migration this round**.

**Browser QA — still not available.** Same gap as every round since 8-6H
(§7 item 1, §10): no browser automation tool exists in this environment.
Round 8-12C's evidence is unit/component tests (jsdom) plus **live,
headless HTTP calls straight into the real running backend + dev DB**
(Excel preview/commit, the concurrency race, cleanup) — genuine end-to-end
API evidence, but still not a human/visual pass. Recommend a manual
click-through (Start/Edit/Rollover modal's Auto Lot preview text, the new
Supplier Lot No field, Plot Detail's two separate lot fields, the Cycle
Yield Report's new column, Excel Import Preview's updated help copy) before
treating this feature as production-verified.

## 12. PO Number Made Optional (rounds 8-13A → 8-13B)

**PO Number is optional everywhere** — omitted, explicit `null`, and a
blank/whitespace string all mean "no PO" and are stored as `null`. This
changed nothing about the DB schema (`plot_cycles.po_number` was already
`nullable=True`) or the Auto Lot V2 formula/allocation/concurrency — PO left
that formula back in round 8-12A; round 8-13A/B only removed the last places
a request or form still *required* a value for it. **P.Code is unchanged and
still required** on every action that opens a new cycle.

**Backend (round 8-13A)**:
- `PlotCycleCreate.po_number`: `str = Field(..., ...)` → `str | None =
  Field(None, ...)`. The validator no longer raises on blank — it always
  calls `normalize_po_number(v)` (trim + upper-case a real value, `None` for
  blank/`None`), the same function `PlotCycleUpdate` already used.
  `PlotCycleRollover.newCycle`, `PlotWithCycleCreate.cycle`, and the
  `reactivate-with-cycle` endpoint's body all reuse `PlotCycleCreate`
  directly — one schema change covered all four write paths, no duplicate
  validation existed anywhere to update.
- Excel import (`plot_import.py`, `_validate_row`): the `_NEW_CYCLE_ACTIONS`
  check that used to require both `poNumber` and `pCode` now only requires
  `pCode`. Applies to all 5 new-cycle actions (`create_plot_with_cycle`,
  `start_new_cycle`, `close_and_start_new_cycle`, `start_next_cycle`,
  `reactivate_plot_with_cycle`). `update_current_cycle`'s blank-PO-preserves
  behavior was never touched (it was already optional there).
- Template (`TEMPLATE_COLUMN_DESCRIPTIONS["poNumber"]`, `plots.py`'s worked
  examples): description says "(ไม่บังคับ) เว้นว่างได้"; the
  `start_next_cycle` worked example (row 5) deliberately ships with a blank
  `poNumber` cell as a still-valid example, while the `create_plot_with_cycle`
  example (row 3) keeps a PO — proving both are legitimate.
- The repository (`plot_cycle_repository.py`) and the DB CHECK constraint
  (`auto_lot_requires_fields`, migration 0048/0049) needed **zero changes** —
  both already accepted/allowed `po_number IS NULL` for V2 auto rows; only
  the API-layer requiredness was ever the gate. **V1 legacy rows are
  unaffected**: the CHECK constraint's V1 branch (`auto_lot_series_key IS
  NULL`) still requires `po_number IS NOT NULL`, unchanged.

**Frontend (round 8-13B)**:
- `frontend/src/api/plots.ts` — `PlotCycleCreatePayload.poNumber: string` →
  `poNumber?: string | null`. `pCode: string` unchanged (still required by
  the type). `PlotCycleUpdatePayload = Partial<PlotCycleCreatePayload>` needed
  no direct edit.
- `frontend/src/components/farmlog/PlotCycleModals.tsx`:
  - `cyclePlanFields.poNumber` (CREATE schema): `z.string().trim().min(1,
    ...)` → `z.string().max(100).optional().or(z.literal(''))` — the exact
    same shape `cycleEditPlanFields.poNumber` already used.
  - Label: always `"PO Number (ไม่บังคับ)"`, **never** a `*` — in every mode.
    `P.Code` keeps its `*` on create.
  - `toPayload` (CREATE): `poNumber: values.poNumber?.trim() || null` (was
    `.trim()` unconditionally, which only worked because the schema
    guaranteed a nonblank string before).
  - `toEditPayload` (EDIT) — **the one real behavior change**: `poNumber` is
    now **always sent** (`values.poNumber?.trim() || null`), the same
    always-sent pattern `supplierLotNo` already used, instead of the old
    omit-when-blank pattern. This is deliberate: an optional field where
    blank could mean either "leave it" or "clear it" is ambiguous, and the
    old omit-based logic could never express "clear this PO" at all. `pCode`
    keeps the OLD omit-when-blank/preserve behavior — unaffected, still
    required on create, so blank-on-edit still just means "don't touch it".
  - Edit-mode helper text under the PO field: `"เว้นว่างเพื่อคงค่าเดิม"` →
    `"ไม่บังคับ — เว้นว่างเพื่อลบ PO Number ของรอบนี้"` (P.Code's helper text
    is untouched).
- **Display code needed zero changes.** `PlotDetail.tsx`'s `Field`/`Cell`
  components, `CycleYieldReport.tsx`'s `PO {r.poNumber || '—'}`, and
  `Plots.tsx`'s list line were already null-safe from round 8-5B/8-12C
  onward — `poNumber: string | null` was always the *read*-model type, only
  the *write*-side (create payload) was ever restrictively required. History
  rows already read each cycle's own `poNumber`, never falling back to the
  active cycle's.

**Live E2E result (round 8-13B, local/dev only)**: reused the existing
inactive `QA-AUTOLOT-812C-…-A` plot (no new plot needed). Excel
`reactivate_plot_with_cycle` with `poNumber` blank, `pCode=QAPOOPT`,
`lotNo` blank →
Preview: `validRows=1`, `proposedLotNo =
"{cycleLabel}-SUP001-QAPOOPT-###"` (no PO/plotCode in it), DB fingerprint
identical before/after (Preview never mutates). Commit: succeeded,
`reactivatedPlots=1`, `resultLotNo` real V2 lot with `lotRunningNo=1`,
`resultLotNoSource=auto`, `po_number` verified `null` via a direct `GET
/plots/{id}`, `supplierLotNo` matched input, `autoLotSeriesKey` absent from
every response. Negative-preview checks: blank PO + blank P.Code → error
names ONLY `pCode`, never `poNumber`; blank PO + Manual Lot + P.Code →
valid. Cleanup: closed (`cancelled`, reason "PO optional QA cleanup (round
8-13B)") + deactivated via the real API — `isActive=false`, no active
cycle, 3-cycle history intact (1 harvested from 8-12C, 1 cancelled from
8-12C's concurrency test, 1 cancelled from this round), `records` count
unchanged (347 before/after), non-QA-plot data fingerprint byte-identical
before/after the whole round, no hard delete, no direct SQL.

**Latest test baseline (round 8-13B).** Backend targeted suites (PO schema,
lot resolution, Excel import/template/reactivate, Cycle Yield Report,
rollover): **301 passed** — backend production source was NOT touched this
round (frontend-only), confirmed by mtime. Frontend: **1424 passed / 50
files** (`npx vitest run`, up from 1409 — round 8-13A didn't touch frontend
so that was still the baseline), typecheck/lint/build all clean. Alembic
`current == heads == 0049_auto_lot_v2_integrity` — **no new migration**.

**Browser QA — still not available** (same gap, §7/§10/§11). Round 8-13B's
evidence is the same combination as 8-12C: component tests (jsdom) plus live
headless HTTP E2E straight into the real backend + dev DB. Manual checklist
still outstanding: Start/Rollover/Reactivate modal's PO field on a real
screen (label text, no `*`, helper copy), Edit modal's clear-PO flow via
actual typing/deleting, Plot Detail/List/Report's `—` rendering at real
viewport widths (1440×900 desktop, 390×844 mobile per the round's own
checklist) — none of this has been visually verified yet.

## 13. Oracle Reference Fields — Backend, Frontend & Final QA (rounds 8-21A → 8-21C)

**Migration 0050 (`plot_cycle_oracle_refs`)** — current alembic head as of
round 8-21C is `0050_plot_cycle_oracle_refs`. Adds three nullable
`VARCHAR(255)` columns to `plot_cycles`: `oracle_supplier_code`,
`oracle_invoice`, `ref_account`. Additive only, no default, **no backfill** —
every pre-existing cycle reads `NULL` on all three. Never hand-edit; any
further change here is a new migration.

**What these three fields are** — independent, OPTIONAL, free-text
back-office reference identifiers, one set per `PlotCycle` (not `Plot`: they
may legitimately change cycle to cycle). They carry **no business logic**:
unlike `lot_no`/`po_number`/`p_code` they never feed the Auto Lot formula
(§11) or any running number, and unlike `cycle_label` they are never
required. Normalization is a single shared rule — trim, blank/whitespace →
`NULL` — in `backend/app/services/cycle_reference_fields.py`
(`normalize_cycle_reference_text`), reused by the Pydantic schemas, the
repository, and the Excel importer so the three call sites can never drift on
what "blank" means.

**API contract** (`PlotCycleCreate`/`PlotCycleUpdate`/`PlotCycleRead`,
`app/schemas/plot.py`) — `oracleSupplierCode`/`oracleInvoice`/`refAccount`,
each `string | null`, `max_length=255`:
- **Create** (Start / Create-plot-with-cycle / Rollover's `newCycle` /
  Reactivate-with-cycle): omitted, `null`, or blank → `NULL`; nonblank →
  trimmed and stored.
- **Edit** (`PATCH .../cycles/{cycleId}`): standard `exclude_unset`
  semantics — key **absent** → untouched; key **present** (even `null` or
  blank) → normalized and written, so a present-but-blank value **clears**
  the field. Same contract `supplierLotNo` already uses (§11).
- **Rollover never auto-copies** the closing cycle's values into the new
  cycle — `plot_cycle_repository.rollover_cycle` only ever forwards whatever
  the caller explicitly passed for the *new* cycle; it never reads
  `current_cycle.oracle_supplier_code`/etc. A caller that wants the new cycle
  to carry the same value must pass it explicitly. Verified both by unit test
  and, in round 8-21C, live against the real dev DB.
- **Never exposed anywhere outside the `PlotCycle` contract** — confirmed via
  a full OpenAPI schema sweep (round 8-21C): the three fields exist ONLY on
  `PlotCycleCreate`/`PlotCycleUpdate`/`PlotCycleRead`/`PlotImportRowPayload`
  (the Excel echo). Zero occurrences on `PlotRead`, `PlotSummary`, any
  `Record*` schema, or any Public Inspect schema — deliberately not mirrored
  onto `PlotRead.activeCycle*` the way `poNumber`/`pCode`/`supplierLotNo`
  are, to keep the blast radius to exactly what was asked for.

**Excel import** (`backend/app/services/plot_import.py`, `IMPORT_COLUMNS`) —
`oracleSupplierCode`/`oracleInvoice`/`refAccount` sit immediately after
`supplierLotNo`, before `plantingDate`. The reader maps by header **name**,
so an older workbook with none of the three columns still imports every
other action unchanged (verified live, round 8-21C).

**`update_current_cycle`'s blank-cell rule is DELIBERATELY DIFFERENT** from
every other optional Excel column on this row (`poNumber`/`pCode`/
`supplierLotNo` all *preserve* on a blank cell):
- Column **absent** from the workbook entirely (an older download) →
  **preserve** the stored value.
- Column **present** but the cell is blank → **clear** the stored value to
  `NULL`.
- Column present with text → trim and set.

The distinction is carried by `_Parsed.oracle_supplier_code_given` (etc.),
set from `columns_present = frozenset(headers)` — the sheet's own header
row, computed once per file in `_validate_all` — never from whether the
row's own cell happens to be blank. `_execute_row`'s `update_current_cycle`
branch only puts a key into the `fields` dict passed to
`plot_cycle_repository.update_cycle` when `*_given` is `True`, reusing that
function's existing `"key" in fields` presence check (§11's `supplierLotNo`
pattern) — no new repository logic was needed for "preserve", only for
"clear-on-present-blank".

**Frontend** (`frontend/src/components/farmlog/PlotCycleModals.tsx`,
`frontend/src/pages/farmlog/admin/PlotDetail.tsx`,
`frontend/src/components/farmlog/PlotImportModal.tsx`):
- One shared UI group, "ข้อมูลอ้างอิง Oracle", inside `CyclePlanFields` (the
  same component that already carries PO/P.Code/Supplier Lot No) — covers
  Start/Rollover(new cycle)/Reactivate for free. No `*` on any of the three
  labels. `maxLength={255}` mirrors the backend cap.
- Edit prefills all three from the loaded cycle and **always resends** them
  (never omitted) — an empty box clears, an untouched box round-trips its own
  current value. Same always-sent convention `supplierLotNo`/`poNumber` (edit
  mode) already use.
- Plot Detail's current-cycle panel and cycle-history table both show all
  three (history: each row reads **its own** cycle, never the active one).
  Rollover modal's "closing cycle" summary block gained the same three
  fields. `null` renders as `—` everywhere via the existing `Field`/`Cell`
  helpers — no new null-handling code was needed.
- Excel Preview table shows all three on one line grouped right after
  "Supplier Lot", and a warning — *"สำหรับ update_current_cycle: ช่อง Oracle
  ที่เว้นว่างจะล้างค่าเดิม"* — appears **only** when the previewed file
  contains at least one literal `update_current_cycle` row (never for
  `start_next_cycle`, even one that *resolves* to a rollover). Preview/Commit
  request payloads are byte-for-byte unchanged — this feature only reads
  fields the backend already returns.
- Confirmed **absent** from `RecordForm.tsx` and `PublicInspect.tsx` (unit
  test + source grep, round 8-21B/C) — cycle-level admin data, never surfaced
  on the field inspection form or the public flow.

**Final QA (round 8-21C) — live, read-only, no HTTP token available.** No
browser tool and no verified way to obtain an authenticated bearer token were
available this round (same permission-classifier gap noted for round 8-20C
elsewhere in this codebase's history), so Excel QA was done by calling
`plot_import.build_preview()` **directly inside the backend container**
against the **real dev DB** — the exact function the API endpoint calls, sharing
its request-scoped RLS session GUCs (`app.scope`/`app.user_id`/
`app.supplier_id`) set by hand to match what `get_rls_context` sets for a
real authenticated request. This is genuinely live (real Supplier/Plot/Cycle
rows, e.g. `SUP001`/`SUP001-P001`), genuinely read-only (`build_preview`
never commits; confirmed by an identical row-count fingerprint before/after
across `suppliers`/`plots`/`plot_cycles`/`records`/`plot_access_phones`/
`plot_access_credentials`), but it is **not** the same as driving the actual
`PlotImportModal` UI through a browser — call it "live service-layer QA",
distinct from both automated unit tests and real Browser QA. Scenarios run:
old workbook (3 columns absent) still previews the create row valid; full
values echo correctly; blank cells → `null`; exactly 255 chars → valid;
exactly 256 chars → `status=error`, message names the column
(`oracleInvoice ต้องไม่เกิน 255 ตัวอักษร`); `update_current_cycle` against the
real active `SUP001-P001` cycle resolves `activeCycleId`, echoes a
present-blank cell as `null` (→ clear) and a present-text cell verbatim (→
set). The filter-aware template's row-builder (`_new_cycle_row_values`) was
also exercised directly against the same real plot and correctly read its
(currently `NULL`, never-backfilled) active-cycle values.

**Test baseline (round 8-21C, re-verified clean).** Backend: targeted
Oracle/migration/import sweep **1016 passed**; full suite **2989 passed**
(a same-run flake of 5 unrelated `test_inspection_photo_processing.py`
failures, caused by CPU contention from a concurrent frontend test run, did
not reproduce when re-run in isolation — 37/37 passed alone; not a regression).
Frontend: targeted PlotCycleModals/PlotDetail/PlotImportModal **340 passed**;
full suite **1865 passed / 60 files**, typecheck/lint/build all clean —
identical counts to round 8-21B's baseline (no code changed this round).
Alembic `current == heads == 0050_plot_cycle_oracle_refs` — **no new
migration this round**. Zero backend/frontend source files modified
(confirmed by mtime sweep) — this was a read-only QA round throughout; no
bug was found that needed a code change.

**Browser QA — still not available** (same gap as §12 and every prior
round). Manual checklist outstanding for whoever picks this up next:
Start/Edit/Rollover/Reactivate's "ข้อมูลอ้างอิง Oracle" group on a real
screen (no `*`, 255-char cap felt via typing, helper copy in Edit mode),
Plot Detail current-cycle panel + history table's three new columns at real
viewport widths (desktop **and** mobile — the history table's `min-width`
grew from 1320px to 1680px this round, purely to fit the new columns inside
its existing horizontal-scroll container; never visually confirmed), and the
Excel Preview blank-clears warning appearing/disappearing correctly as a real
uploaded file's rows change.
