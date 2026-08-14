# Round 8-6D — Filtered Excel User Acceptance + Role Scope QA — BLOCKED ON MANUAL BROWSER UAT

> Status: **BLOCKED ON MANUAL BROWSER UAT**. No real browser-automation tool
> and no user-controlled open browser session were available in this agent
> session (same conclusion as round 8-2, see `ROUND_8_2_BROWSER_QA.md`).
> Parts E (Admin Browser UAT) could not be performed. Parts F/G/H/I — which
> can be exercised at the API/DB level without a browser — WERE performed,
> read-only, directly against the live dev backend/DB, and are reported
> below with real results. This file does **not** claim any visual/
> interactive UI testing occurred.

## Why Browser UAT (Part E) could not proceed

Checked before starting (per Part D's gate):
- `ToolSearch` for a browser-automation/screenshot/DOM-rendering tool: none
  registered this session (only `WebFetch`, which converts a page to
  Markdown via a text model — no rendering, no click/type/scroll, no
  screenshot, explicitly disqualified by this round's own Part D).
- No Playwright/Puppeteer dependency in `frontend/package.json`.
- Bootstrapping Playwright fresh via `npx playwright` for this one QA round
  was judged out of scope: it would pull down a full Chromium binary purely
  for this session, was not clearly authorized by this round's script, and
  the round explicitly provides a `BLOCKED ON MANUAL BROWSER UAT` outcome
  as the correct path when no tool exists — so that path was taken instead
  of improvising infrastructure.
- No way to see or drive the user's own open browser session.

## What WAS verified (automated/read-only, not Browser QA)

All done directly against the live dev stack already running (backend
`srm-fieldinspect-backend` on `127.0.0.1:8000`, DB `srm-fieldinspect-db` on
`127.0.0.1:5432`, frontend dev server on `:5173` — no new runtime created,
no ports/containers changed):

- **Runtime health**: frontend `:5173` → 200, backend `/health` → 200,
  Alembic current == heads == `0042_plot_cycle_po_lot`.
- **DB snapshot before/after QA**: `suppliers=10, plots=102,
  plot_cycles=111, records=337, plot_access_phones=7`,
  `max(plots.updated_at)`, `max(plot_cycles.updated_at)`,
  `max(records.created_at)` — **identical before and after**. No mutation
  occurred during this round.
- **Workbook verification** (Part F, via a direct authenticated
  `GET /plots/import-template` call, parsed with stdlib `zipfile`+
  `xml.etree`, no added dependency): 3 sheets in the correct order
  (นำเข้ารอบใหม่ / ข้อมูลปัจจุบัน / ตัวอย่าง), Sheet 1 row count matched
  `GET /plots` for the same filter exactly (2/2 for SUP001+crop=พริก),
  every Sheet 1 row `action=start_next_cycle` with `lotNo`/`plantingDate`
  blank but still styled yellow (`FFFFF9C4`), Sheet 2 retained the old
  lotNo/plantingDate, Sheet 3 rows all styled red.
- **Preview-only import UAT** (Part G, via direct `POST
  /plots/import/preview` calls — never `/commit`): unedited file → real
  `same_active_cycle_label` block observed on the one plot in this dataset
  whose active cycle actually has a `cycleLabel` set (SUP010-P001); edited
  temp workbook (unique cycleLabel + PO/pCode, blank lotNo) → `validRows=1`,
  `resolvedAction=close_and_start_new_cycle` with the old cycle correctly
  identified, `lotMode=auto`, `proposedLotNo` in the correct
  `PO-plotCode-XX` shape. A second plot with no active cycle correctly
  resolved to `start_new_cycle`. **No commit was ever issued.**
- **Role/scope QA** (Part H): confirmed via direct DB query (not guessed)
  that this database has exactly 2 roles with any users at all —
  `internal:super_admin` (1 user, used for all Admin-scope testing above)
  and `supplier:owner` (4 users, `auth_provider='local'` so a password
  login is technically possible for them, but no known/documented
  credentials exist for this session to use). **Zero**
  `farmlog:field_officer` users exist in this database. Supplier Owner and
  Field Officer scope QA are therefore `NOT RUN` — not because the roles'
  logic is untested (the `plots_scope` RLS policy's `'assigned'` branch was
  read directly from the live database and matches source exactly), but
  because no live session as either role could be established, and none
  was created (forbidden this round).

## Not tested (Part E, with reason)

| Item | Reason not tested |
|---|---|
| Desktop/Tablet/Mobile visual layout of `/farmlog/admin/plots` | No browser-rendering tool available |
| Case 1-6 click-through workflow (Supplier required, filter summary, filename race, stale-error clearing) as an interactive UI flow | Same — no browser tool. All 6 cases' underlying **logic** is instead covered by the round 8-6C unit/integration test suite (`Plots.test.tsx`, 104 tests, all passing) and by this round's fresh API-level re-verification, but neither is a substitute for an actual click-through |
| Screenshots of any viewport | No browser tool to capture them |
| Supplier Owner / Field Officer browser session | No known credentials / no such account exists |

## Manual checklist for the user

If you can open the app in a real browser, please walk through Part E's
Cases 1-6 (Supplier-required gate, filter summary, filename race, stale
error clearing) at 1440×900 / 768×1024 / 375×812, and Part H's Supplier
Owner check (log in as one of the 4 existing `supplier:owner` accounts,
confirm you only see/download your own Supplier's template, and that
requesting another Supplier's `supplier_id` is rejected). Let me know the
result and I can fold it into this document.

## Scope

No frontend or backend code was changed this round (QA-only, no bug
reproduced). No migration, no user/role/permission created or modified, no
Excel import committed.
