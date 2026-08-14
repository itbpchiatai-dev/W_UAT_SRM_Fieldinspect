# FarmLog — Final Project Handover (rounds 7-1 → 8-4G)

> **This is the top-level, project-wide handover** for the next dev/agent
> picking up FarmLog. It supersedes `PLOT_CYCLE_HANDOVER.md` as the entry
> point — that document is still valid and MORE DETAILED for the Plot →
> PlotCycle → Record concurrency/locking/RLS internals (§3a/§3b there); this
> document adds everything built after it (Excel import polish, Yield final
> estimate + reports, Supplier Owner login-based inspection, the public
> phone-number multi-plot flow, the offline queue, and the Docker runtime
> consolidation) and gives the full current-state picture in one place.
>
> Written 2026-07-19 (round 8-4G — Final Browser QA, User Acceptance and
> Project Handover).

## 1. Executive Summary

FarmLog lets field staff and supplier accounts record crop inspections
against plots that move through **planting cycles** (Plot → PlotCycle →
Record). Three ways to create an inspection exist side by side:

1. **Logged-in, internal roles** (Admin/Supervisor/Field Officer) — full or
   assignment-scoped access, `/farmlog/records/new`.
2. **Logged-in, Supplier Owner** (round 8-4F) — same form, scoped to their
   own supplier's plots only.
3. **Public, phone-number-based** (`/public/inspect`, no login) — a field
   worker or supplier contact enters a registered access phone number,
   sees every plot that number is authorized for (can span multiple plots
   and, historically, multiple suppliers per number), and submits without
   any account. Supports full offline queuing.

**As of round 8-4G this project is QA-complete and handed over**: local
Docker runtime consolidated into a single Compose project, full backend
(1056 tests) and frontend (814 tests) suites green, Supplier Owner
inspection permission live-verified end-to-end including one real record
creation, public phone flow regression-verified, and documentation brought
current. The one enduring gap across the entire project history is **no
interactive browser automation tool has ever been available** in any
session — see §10.

## 2. Runtime & Infrastructure (rounds 8-4D / 8-4E / 8-4E.1)

**Single Docker Compose project: `srm_fieldinspect`**

```
srm_fieldinspect
├─ srm-fieldinspect-backend   127.0.0.1:8000   (Docker, DB_HOST=db, uvicorn --reload)
└─ srm-fieldinspect-db        127.0.0.1:5432   (Docker, pgvector/pgvector:pg16)
   └─ external volume: srm-fieldinspect-db-data
Windows host:
└─ frontend (Vite)            127.0.0.1:5173
```

**Canonical commands** (see `docs/deployment.md` §4, the source of truth):

| Task | Command |
|---|---|
| Start | `start-service.bat` |
| Stop | `stop-service.bat` |
| Status | `status-service.bat` |
| Restart | `restart-service.bat` |
| Raw Compose equivalent | `docker compose --env-file backend/.env -p srm_fieldinspect -f docker-compose.yml -f docker-compose.dev.yml up -d db backend` |

- `docker-compose.dev.yml` (root) is the dev overlay on top of the
  production `docker-compose.yml` — production behavior (no `db` service,
  no host port publishing, no bind mount) is **unaffected** when the overlay
  isn't included.
- `docker/docker-compose.yml` is a **retired deprecation stub** (comment-only,
  no `name:`, no services) — kept only so scaffold tooling that checks for
  its existence still works. It cannot recreate the old `srm-fieldinspect-dev`
  project. **Never revive it as a runnable compose file.**
- `docker/init-db.sql` is still live — the dev overlay's `db` service mounts
  it.
- ⚠️ **Volume `srm-fieldinspect-db-data` is `external: true` — never
  `docker compose down -v`, never `docker volume rm` it.** If it's ever
  missing, `start-service.bat` refuses to auto-create an empty one; restore
  from a `pg_dump -Fc` backup instead (see `docs/deployment.md`).
- `seed-workerinventory-db` (a **different, unrelated project**,
  `Seed_WorkerInventory`) runs on host port 5433 — never touch it; its own
  `docker/` folder happens to share a name with this repo's, which is the
  historical reason the old project-name collision existed (resolved in
  round 8-4E).
- **Known security debt (not yet fixed, deliberately deferred):** a
  plaintext `POSTGRES_PASSWORD`-equivalent credential literal exists in the
  dev overlay chain (introduced round 8-4D era) — needs migration to a
  proper secret store + rotation in its own dedicated round. Do not rotate
  it as a side effect of an unrelated round (would risk a DB lockout).

## 3. Data Model — Plot → PlotCycle → Record

**Full details, concurrency/locking contract, and RLS internals live in
[`PLOT_CYCLE_HANDOVER.md`](PLOT_CYCLE_HANDOVER.md) — read that before
touching `plot_cycles`, `rollover`, `records` (append-only), the Excel
import lifecycle actions, `get_plot_for_update`/lock ordering, or the
`records_scope`/`plots_scope`/`plot_cycles_scope` RLS policies.** Summary:

- **Plot** = permanent physical field (supplier, code, GPS, QR key,
  `is_active`). Also the **aggregate concurrency lock** for itself + its
  active cycle + its inspection snapshot (lock order always Plot →
  PlotCycle, never reversed).
- **PlotCycle** (รอบปลูก) = one planting season (crop/variety/lot/planting
  date/plant count/expected yield/`cycleLabel`). At most one `active` cycle
  per plot, DB-enforced (partial unique index).
- **Record** = one inspection, binds to the plot's active cycle **at create
  time**, server-derived. **Append-only** — no `PATCH /records/{id}` exists;
  the only mutation is `POST /records/{id}/deactivate`.
- QR (`plots.qr_key`) is generated once at plot creation, bound to the
  **Plot**, never regenerated by any cycle lifecycle event.

### 3.1 Excel Import — 4 lifecycle actions (`app/services/plot_import.py`)

1. `create_plot_with_cycle` — new plot + first active cycle (generates QR)
2. `start_new_cycle` (a.k.a. "start next cycle") — existing plot, no active
   cycle → new active cycle
3. `update_current_cycle` — existing plot, has active cycle → edit plan
   fields
4. `close_and_start_new_cycle` — **atomic rollover**, reuses the same
   `plot_cycle_repository.rollover_cycle` helper the single-plot Rollover
   UI button calls (these two callers cannot drift from each other)

Preview (`/plots/import/preview`) is always read-only; commit
(`/plots/import/commit`) is one all-or-nothing transaction, server-side
re-validated (never trusts a client-sent preview), locks every existing
plot the file touches up front in a deterministic order (sorted by id) to
avoid cross-file deadlocks.

## 4. Yield Final Estimate & Reports (rounds 8-2.7 → 8-2.8B)

- `plot_cycles.final_estimated_yield` (migration `0038_cycle_final_estimate`)
  = `expected_yield_full × final_yield_pct / 100`, CHECK `>= 0`. Set when a
  cycle is closed (`final_yield_pct` captured at close time), giving a
  frozen actual-vs-expected snapshot per cycle that survives rollover.
- **Report #1 "สถานะแปลง" (Plot Status)** — `GET /api/v1/reports/plot-status`
  — every active plot with its latest denormalized snapshot columns; Excel
  export (`plot-status-report.xlsx`).
- **Report #2 "ผลผลิตตามรอบปลูก" (Cycle Yield)** — `GET
  /api/v1/reports/cycle-yield` — per-cycle actual vs. expected yield,
  including closed/historical cycles via `final_estimated_yield`.
- Both reports are read-only aggregate views (`app/repositories/
  report_repository.py`) gated on `plots.read`; menu entries live under the
  FarmLog "รายงาน" menu.

## 5. Authorization Model (current, post round 8-4F)

| Role | suppliers | plots | records | Data scope |
|---|---|---|---|---|
| `internal:super_admin` | all | all | CRUD | all (RLS `scope='all'`) |
| `internal:admin` | CRUD | CRUD+assign | CRUD | all |
| `farmlog:supervisor` | read | read+assign | read/create/update | all |
| `farmlog:field_officer` | read | read | read/create/update | assigned plots only |
| **`supplier:owner`** | read | read/create/update | **read/create** (round 8-4F) | **own supplier only** — RLS `scope='supplier'` via `user.supplier_id` |
| `supplier:staff` | read | — | read only | own supplier only |

**Supplier Owner scope contract** (round 8-4F, the last authorization
change made):
- `records.create` is the **action** permission only. The **data boundary**
  is `app/api/deps/scope.py::_resolve_scope` — owner **with**
  `user.supplier_id` set → `scope='supplier'`; owner **without** it → falls
  through to `scope='none'` → sees **nothing** (fail-closed by construction,
  never auto-linked).
- The create endpoint (`app/api/v1/records.py::_create_record`) resolves the
  target plot **under that scope** — a plot belonging to another supplier
  returns a generic 404 (never leaks existence), `supplier_id` is always
  derived from the resolved plot (never trusted from the client body), and
  crop/variety/planting-date are snapshotted from the plot's active cycle
  (never client-supplied).
- Owner deliberately does **not** get `records.update`/`records.delete` —
  owners append inspections, they never edit/remove history (same
  append-only principle as every other caller).
- Role permission sync (`backend/app/seed.py::_seed_roles`) is an
  **exact-set idempotent sync**: rerunning `python -m app.seed` after
  editing `DEFAULT_ROLES` both grants newly-added keys AND revokes removed
  ones for every system role — this is the sanctioned way to change any
  role's permission set (never hand-edit `role_permissions` rows), and it
  never touches custom roles or per-user permission overrides.
- Frontend gate: `/farmlog/records/new` is wrapped in
  `<RequirePermission perm="records.create">` — purely permission-driven,
  **no role-name branching anywhere in the frontend**.

## 6. Public Phone-Number Inspection Flow (rounds 8-3 → 8-4C)

Entry point: `/public/inspect`, no login. Replaced an earlier per-plot
"inspection code" scheme (retired migration `0040_retire_inspection_codes`)
with a **phone-based, multi-plot** model (`plot_access_phones` table,
migration `0039`):

1. **Lookup** (`POST /api/v1/public/inspection-access/lookup`) — a phone
   number (+ optional `qrKey` for QR-scan pre-select) returns a short-lived
   `phoneAccessSessionToken`. Response never echoes the phone number itself;
   an unrecognized number gets a generic Thai "not found" message (no PII
   echo).
2. **List plots** (`POST .../inspection-access/plots`) — every plot that
   number is authorized for, across suppliers if applicable. Each item
   carries plot/supplier code+name, `cycleLabel`, Lot No, planting date,
   latest yield %, and `canInspect` (false when the plot has no active
   cycle — shown but disabled, never hidden).
3. **Select plot** (`POST .../inspection-access/select-plot`) — binds
   `inspectorType` (เกษตรกร/Supplier/ส่งเสริม), mints the actual
   `inspectionSessionToken` scoped to that plot's **current active cycle**
   (not just plot/supplier — a rollover mid-session invalidates it, by
   design).
4. **Submit** — same append-only Record creation contract as the logged-in
   flow; crop/variety/planting-date frozen from the active cycle.

No inspection code exists anywhere in this flow anymore. The phone number
itself is never surfaced back to the browser in any response.

## 7. Offline Queue (rounds 8-4A → 8-4C.2)

Field workers can queue inspections while genuinely offline and sync later:

- **IndexedDB store** (`frontend/src/lib/offline-inspection-store.ts`,
  schema V2) — each draft has a `status`: `pending` | `blocked_access` |
  `blocked_cycle_changed` | `blocked_conflict` | `blocked_expired`. No
  `'syncing'` status is ever persisted (lives only in React state), so a
  browser crash mid-sync never leaves a draft stuck.
- **Sync engine** (`frontend/src/lib/offline-inspection-sync.ts`) —
  sequential (never parallel), oldest-first, full HTTP error classification:
  401/no-response stops the whole batch; 404 → `blocked_access` (retryable —
  an admin re-opening access is a real recoverable case); 409
  `planting_cycle_changed` → `blocked_cycle_changed` (**never** retryable —
  the backend fail-closed rejects the original captured `plotCycleId`
  identically every time; the UI shows no retry button, only actionable
  guidance to record fresh); 409 `idempotency_conflict` → `blocked_conflict`;
  422 expired/invalid-timestamp → `blocked_expired`; 429/5xx/unrecognized →
  stop batch, draft stays `pending` (never guessed as sent).
- **Retry semantics** (round 8-4C.2): `resetOfflineInspectionDraftForRetry`
  has its own internal guard — only `blocked_access` can ever be reset, even
  if a caller bug tried to reset something else.
- **Idempotency**: each draft carries a client-generated
  `clientSubmissionId` (UUID, via a `useRef`-based identity — refs update
  synchronously so two same-tick calls can never race into two different
  IDs); the backend's offline-submission endpoint treats resubmission of the
  same ID as a no-op conflict, not a duplicate record.
- Offline multi-plot continuity: a form opened offline via the cached plot
  list always queues on submit (never silently auto-submits even if
  connectivity returns mid-fill) because it never has a real
  `inspectionSessionToken`.

## 8. QA Baseline (round 8-4G, latest verified)

| Check | Result |
|---|---|
| Backend full suite (`pytest -q --no-cov`) | **1056 passed** |
| Frontend full suite (Vitest, single-fork) | **814 passed / 44 files** |
| Frontend typecheck / lint / build | all ✅ |
| Alembic | `current == heads == 0041_offline_submission` |
| Admin API probe | suppliers=10, plots=102, records=327 (active-only default filter; 329 total exist) |
| Supplier Owner (SUP001) API probe | records.create=true, update/delete=false; suppliers=[SUP001] only; plots=12/foreign=0; records=38/foreign=0 |
| Supplier Owner (no `supplier_id`) | fail-closed — 0 plots, 0 records, confirmed live |
| Public phone flow API probe | bogus number → 404 no echo; real number → 200, session token issued, no phone leak; multi-plot list with correct cycle/lot/yield fields; select-plot works, no phone leak |
| **Live record submission** (user-approved, round 8-4G) | **1 record created** on plot `SUP001-P001` via the real `POST /api/v1/records` endpoint through a Supplier Owner token — proved plot-cycle binding, crop/variety snapshot from active cycle (not client-sent), and plot yield-snapshot sync (100.0% → 92.0%) all work end-to-end. `records` count 329 → **330** (exactly +1, all other counts unchanged). Record is `is_active=true`, tagged `submitted_by_name="QA Round 8-4G"` for identification — kept per append-only policy, never deleted. |
| Browser/visual QA | **NOT RUN — no browser automation tool has ever been available** in any round across this project's history. See §10. |

## 9. Production Readiness Checklist

- [x] Single, non-colliding local Docker Compose project
- [x] Backend + frontend automated suites green, no known regressions
- [x] Supplier Owner permission scoped correctly (RLS + app-layer + endpoint,
      triple-verified)
- [x] Public phone flow regression-verified, no PII leaked in any response
- [x] Offline queue error classification is exhaustive and fail-safe
      (never guesses a send succeeded)
- [x] Alembic at head, no pending migrations
- [ ] **Manual browser click-through QA** — never performed, no tooling
      available in any AI session to date; must be done by a human before
      first real production rollout (see §10 checklist)
- [ ] **Rotate/relocate the plaintext dev-DB credential** (§2) into a proper
      secret store — deferred, dedicated round recommended
- [ ] **Production `backend/.env`** reviewed for real secrets (JWT key,
      Azure AD client secret, DB password) — separate from dev values
- [ ] **Frontend bundle code-splitting** — main chunk >500KB warning is
      pre-existing and cosmetic (no functional impact), not urgent
- [ ] Full concurrent-**write** integration test (two real sessions racing
      a deactivate vs. a rollover end-to-end) — the lock *primitive* is
      proven live (`PLOT_CYCLE_HANDOVER.md` §5a), the full read-modify-write
      race is not; needs a DB-backed test fixture this repo doesn't have yet

## 10. Known Limitations & Manual QA Checklist

**No browser automation tool has been available in ANY session across this
project's entire history** (confirmed again this round via tool search).
Every round's "browser QA" has instead been: API-level authorization
probes with short-lived helper tokens, source-code inspection for exact UI
text/behavior, and the automated component/unit test suites. This is a
structural environment limitation, not something any round chose to skip.

**Before first real production use, a human should manually walk through:**

1. **Login** (desktop + mobile) as Admin — Dashboard loads, no redirect
   loop, no console errors.
2. **Admin**: Suppliers/Plots/Records/Reports menus render; Plot Detail
   shows current cycle/crop/variety/Lot No/planting date/plant
   count/expected yield; open `/farmlog/records/new`, confirm the plot
   picker shows only active-cycle plots as selectable (no-cycle plots
   visible but disabled) — do not submit unless intentionally testing.
3. **Supplier Owner** login (an account bound to a real supplier) —
   `/farmlog/records/new` opens, plot picker shows ONLY that supplier's
   plots, crop/variety/lot/planting-date render read-only.
4. **Supplier Staff** login — confirm `/farmlog/records/new` is denied
   (inline 403 panel, not a crash or redirect).
5. **Public** `/public/inspect` (no login) — "หมายเลขสำหรับเข้าตรวจ" copy,
   no Supplier Code/Plot Code manual-entry fields, a valid number shows
   multiple plots with supplier/plot names separated, no-active-cycle plots
   un-selectable, an invalid number shows a generic message with no echo,
   inspector-type selector (เกษตรกร/Supplier/ส่งเสริม), after selecting a
   plot the cycle label/crop/variety/Lot No/planting date/latest yield all
   display, and the phone number is never shown anywhere on screen.
6. **Offline queue** — open the form online, switch DevTools to offline,
   fill and save a draft, confirm an "offline" badge + pending count,
   reload and confirm the draft persists, go back online and confirm it
   asks to re-authenticate before syncing, confirm a `blocked_cycle_changed`
   draft has no retry button (only a clear explanation) while a
   `blocked_access` draft does.

## 11. Do Not Touch / Safety Notes (project-wide)

All of `PLOT_CYCLE_HANDOVER.md` §8 applies. In addition, project-wide:

- **Never** `docker compose down -v` or delete `srm-fieldinspect-db-data`.
- **Never** auto-link a Supplier Owner account with a NULL `supplier_id` to
  a guessed supplier — that's an explicit admin action, never automated.
- **Never** print/log a phone number, JWT/session token, qrKey, password, or
  full email in chat, a report, or committed code — every round in this
  project's history has enforced this; short/truncated IDs are fine.
- **Never** bundle an authorization change into an unrelated feature round —
  round 8-4F's `records.create` grant for Supplier Owner is the only
  authorization change made after the initial RLS design, and it went
  through an explicit Step-0 confirmation checkpoint; follow that pattern
  for any future one.
- **Never** silently edit `docs/human/*` — those require a proposed diff and
  explicit user approval first (this round applied one such approved diff
  to `docs/human/onboarding.md`; `docs/farmlog/*` handover docs like this
  one are not subject to that rule and may be updated directly as the
  project evolves).

## 12. Inspection Photo Pipeline (round 8-14A; output switched JPEG → WebP round 8-14A.1; frontend pre-compression round 8-14B; click-to-view lightbox round 8-14C)

Every **newly uploaded** inspection photo is decoded, sanitized, downscaled
and re-encoded server-side before it is stored — as **WebP** since round
8-14A.1 (was JPEG through round 8-14A; the switch needed no new dependency,
the same Pillow build already had WebP codec support). Both upload routes —
`POST /api/v1/records/with-photos` (logged-in) and
`POST /api/v1/public/records/with-photos` (public phone flow) — call the same
`validate_and_save_photos`, which is the only caller of the processor, so the
two can never drift apart. Implementation:
`backend/app/services/inspection_photos.py`.

### The two size contracts (do not conflate them)

| Contract | Value | Meaning |
|---|---|---|
| `MAX_PHOTO_UPLOAD_BYTES` | **15 MiB** | Largest file a client may SEND, per photo. Enforced while reading, in 1 MiB chunks — the 413 fires on the chunk that crosses the cap, never after buffering the whole body. Unchanged by round 8-14A.1. |
| `TARGET_STORED_PHOTO_BYTES` | **1.2 MiB** | What the encoder aims for. Lowered from 8-14A's 1.8 MiB — WebP reaches equivalent visual quality at a smaller byte budget. |
| `MAX_STORED_PHOTO_BYTES` | **1.5 MiB** | Inviolable ceiling. A photo that cannot be brought under it is rejected (422), never stored. Lowered from 8-14A's 2 MiB. |
| `MAX_PHOTO_COUNT` | **5** | Unchanged. |

The old `MAX_PHOTO_SIZE_BYTES` (5 MiB, retired round 8-14A) stays retired —
input and output caps are separate named constants, and round 8-14A.1 only
retuned the output pair's values, not the naming split.

### Pipeline order

1. **Magic-byte allowlist** (JPEG/PNG/WebP) — the cheapest rejection, before
   any decoder is handed attacker-controlled bytes. Client filename and
   `Content-Type` are never trusted. Unchanged — the ACCEPTED input formats
   did not change round 8-14A.1; only the stored OUTPUT format did.
2. **Strict open** — `LOAD_TRUNCATED_IMAGES` forced `False` per call (it is a
   Pillow global any library could flip on). Truncated input is refused.
3. **Pixel-bomb guard** — `width x height > MAX_IMAGE_PIXELS` (50M) is
   rejected from the **header**, before the bitmap is ever allocated. Pillow's
   own bomb check only *warns* between 1x and 2x its threshold, so this
   explicit check — plus promoting `DecompressionBombWarning` to an error — is
   what makes the limit real.
4. **Animated/multi-frame refused** — only frame 1 would survive the
   re-encode; silently discarding the rest is worse than a clean 422.
5. **`ImageOps.exif_transpose`** — orientation is baked into the pixels.
6. **Transparency composited onto white** (RGBA/LA/P-with-transparency) —
   deliberate even though WebP (unlike JPEG) CAN carry alpha: every stored
   photo behaves identically regardless of source or destination format.
7. **ICC → sRGB** via `ImageCms`. A malformed profile is a colour-accuracy
   problem, not a security one: it falls back to "assume sRGB" and the upload
   still succeeds.
8. **Downscale** to `MAX_IMAGE_EDGE` (2560px) longest edge, LANCZOS, aspect
   ratio preserved, **never upscaled** — a 640x480 photo stays 640x480.
9. **Encode WebP** (`format="WEBP", lossless=False, method=4`) with
   `image.info` cleared: no EXIF, no GPS, no ICC, **no XMP** (WebP can also
   carry XMP, unlike the JPEG path round 8-14A shipped — explicitly covered
   by its own test now). Info is cleared rather than merely "not passed
   through" because several Pillow operations copy `info` onto their result,
   so a metadata blob can otherwise survive the transforms and be re-emitted.
   `method=4` is Pillow's own encoder default — a deliberate middle ground
   between compression ratio and CPU; raising it to 6 needs a benchmark
   first, not a guess, given the existing semaphore-bounded concurrency (§
   below) already competes for CPU across up to 5 photos per request.

### Size search

Quality ladder **85 → 75** at the current size; the first result at or under
1.2 MiB wins. Only if quality 75 still exceeds the 1.5 MiB ceiling does the
longest edge step down (x0.85), floored at `MIN_IMAGE_EDGE` (**1280px**).
Dimensions are never reduced merely to reach the target — only to satisfy the
hard ceiling. Quality never goes below 75. Total encode attempts are capped
(`_MAX_ENCODE_ATTEMPTS`) so the search cannot run away. Still over the ceiling
at the floor → 422, rather than storing an oversized file or degrading the
photo into uselessness. Algorithm structure is otherwise IDENTICAL to round
8-14A — only the encoder call and the two byte thresholds changed.

### Concurrency

Decode/encode is pure CPU. It runs through `asyncio.to_thread` (never inline
on the event loop) behind a semaphore capped at `MAX_CONCURRENT_IMAGE_JOBS`
(**2**) so a burst of concurrent 5-photo uploads cannot exhaust CPU/RAM. The
semaphore is stored per running event loop (`WeakKeyDictionary`), because
asyncio primitives bind to the first loop that awaits them — a single
module-level instance would break under any second loop. A process serves one
loop, so this is still "2 jobs per process". Unchanged by round 8-14A.1.

### Save / rollback

All photos are validated **and normalized** before **any** is written, so a
batch whose 4th photo is corrupt writes nothing. If a save nonetheless fails
part-way (disk full), the files already written **in that call** are deleted
before the original error is re-raised — the caller's own `except` cleanup
cannot help there, because a raising call returns it no URLs. `delete()` was
added to the `PhotoStorage` protocol for exactly this rollback, keyed by the
opaque generated filename (never a filesystem path) so an object-storage
backend can honour it with a delete-by-key. Unchanged by round 8-14A.1.

### Existing photos are untouched — now THREE historical extensions, not two

No batch resize, rename, move, or `records.photo_urls` edit — ever, in either
round. New saves are always `.webp` now (round 8-14A.1), but
`PHOTO_FILENAME_PATTERN` and the media-type map **must keep accepting
`jpg`/`png`/`webp`** — `.jpg` is round 8-14A's own retired output format, on
exactly the same "read-only history" footing as the pre-8-14A `.png`/`.webp`
files. This is a real regression risk every round after 8-14A introduces:
whatever round 8-14A.1's own output format becomes "history" the NEXT time
the encoder changes, and so on — the filename gate must keep growing its
tolerance, never narrow it, or an entire prior round's photos 422 on
download. Verified round 8-14A.1: 34 files / 33,666,074 bytes and the
name+size fingerprint were byte-identical before and after the full round
(including the container rebuild).

### Docker recovery (round 8-14A.1) — RESOLVED

Round 8-14A added Pillow but the running dev container's **image** predated
it (`docker-compose.dev.yml` bind-mounts `./backend:/app` for hot reload, but
runtime dependencies live in the image's `/home/app/.local`, which is NOT
bind-mounted) — the container crash-looped on
`ModuleNotFoundError: No module named 'PIL'`. Root cause #2, blocking the
rebuild itself: `backend/.dockerignore` excluded `.pytest_tmp` by **exact
name only**, so stray `.pytest_tmp_codex_*`/`.pytest_tmp_*_review`
directories (permission-denied to the build-context sender) aborted `docker
compose build` with `error from sender: ... Access is denied`.

**Both fixed this round:**
- `backend/.dockerignore`: `.pytest_tmp` → `.pytest_tmp*` (one line).
- Canonical rebuild + restart, exact commands (no `down`, no volume/network
  change, single project `srm_fieldinspect`):
  ```bash
  docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.dev.yml build backend
  docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.dev.yml up -d backend
  ```

**Verified live, round 8-14A.1**: build succeeded (Pillow-12.3.0 present in
the install log); `docker compose ps` shows `srm-fieldinspect-backend` **Up
(healthy)**; `GET /health` → 200; container logs show a clean startup with no
`ModuleNotFoundError`/import error; inside the running container,
`PIL.features.check("webp")` → `True` and a synthetic in-memory probe (no
disk write, no Record) through `normalize_inspection_photo` produced real
WebP output; `alembic current == heads == 0049_auto_lot_v2_integrity` from
inside the container.

### Storage limitation (unchanged, still open)

Storage remains **local filesystem** (`LocalPhotoStorage`) — none of rounds
8-14A/8-14A.1/8-14B change that. Production still needs a persistent volume
or real object storage; a container rebuild without a mounted volume still
loses photos. See §9.

### Frontend pre-compression (round 8-14B; safety-floor + multi-slot race hotfix round 8-14B.1)

Both photo-capable pages — the logged-in `RecordForm` and the public
`PublicInspect` — now shrink a photo **in the browser** before it ever
leaves the device, to save the field worker's mobile data. Browser-native
only (`createImageBitmap` / Canvas / `canvas.toBlob`, with an
`HTMLImageElement` + object-URL fallback when `createImageBitmap` is
unavailable) — **no new npm dependency**. Implementation:
`frontend/src/lib/inspection-photo-compression.ts`, the one integration
point being `frontend/src/components/farmlog/PhotoSlotPicker.tsx` (shared by
both pages, so their behavior can never drift apart).

**The backend is still the sole authority** — every upload, compressed or
not, still goes through the exact same `validate_and_save_photos` /
`normalize_inspection_photo` pipeline described above. Client compression
is a bandwidth optimization only; a browser that fails to compress (or an
API client that skips this module entirely) still produces a fully valid
upload, just larger over the wire.

| Contract | Frontend (pre-compress) | Backend (authoritative, §12 above) |
|---|---|---|
| Target stored size | **1.0 MiB** | 1.2 MiB |
| Soft/hard max | **1.2 MiB** (soft — best effort, never blocks) | 1.5 MiB (hard — 422 if unmet) |
| Max/min longest edge | 2560 / 1280 px | 2560 / 1280 px (same) |
| Quality ladder | 0.85 → 0.75 (canvas `toBlob` quality, 6 steps) | 85 → 75 (Pillow quality, same shape) |
| Output format | WebP (`image/webp`) | WebP (unchanged) |

Behavior notes:
- Input accepted: JPEG/PNG/WebP, ≤15 MiB, validated (MIME + size + non-empty)
  **before** any decode is attempted; a curated Thai message is always shown
  — never a raw browser/decoder exception.
- Transparent PNG/WebP sources are composited onto white, matching the
  backend's own compositing, so the preview and the eventually-stored photo
  never visually disagree.
- Never upscales; never drops quality below 0.75 or the edge below 1280px
  just to hit the target — a bounded "best effort" is accepted instead. The
  1280px safety floor is **genuinely tried, not just a theoretical bound**
  (round 8-14B.1 fix — the original round 8-14B downscale loop reduced each
  axis independently by ×0.85 and stopped as soon as the NEXT step would
  have gone under 1280, so the floor itself was silently never drawn; the
  fixed loop clamps the LONGEST edge to `max(1280, round(longest × 0.85))`
  each step, so the last size level it can ever produce has its longest
  edge exactly 1280). The full downscale chain from 2560 is now
  2560→2176→1850→1573→1337→**1280** — six size levels × six quality steps
  = 36 attempts in the worst case, so `MAX_ENCODE_ATTEMPTS` was raised from
  30 to **40** (was undercounting the real search space by one whole size
  level) to keep matching headroom over the true maximum. The best
  candidate is now tracked as a `{blob, width, height}` triple rather than
  a bare `Blob`, so the dimensions returned can never end up paired with a
  different size level's Blob.
- If the browser cannot produce a WebP blob at all (`canvas.toBlob` returns
  null, or silently substitutes another format), or the compressed result
  ends up **larger** than the source, the ORIGINAL file is uploaded
  unchanged instead (with a non-blocking Thai warning shown in the
  unsupported-encoder case) — the backend converts to WebP on its own end
  regardless.
- Output filename is always generic (`inspection-photo-<slot>.webp`) —
  the original filename (which may contain PII, e.g. a phone's default
  `IMG_<date>` naming) is never sent when a compressed file is produced.
  The **fallback** path is unaffected — it is the same original `File`
  object, sent exactly as it always was before this round.
- No persistent client storage anywhere in the pipeline — no
  localStorage/sessionStorage/IndexedDB/React Query cache/URL — the only
  output is an in-memory `File` handed straight to the existing multipart
  upload call. The public flow's offline draft queue (§7) is untouched;
  `/public/inspect` remains **Online-only**, and this round adds no new
  path into `putOfflineInspectionDraft`.
- `PhotoSlotPicker` processes **one photo at a time** across all 5 slots
  (a shared in-component queue), to bound CPU/RAM on a phone even if a user
  fills every slot in a burst, and tracks a per-slot "generation" counter so
  a slower stale pick (superseded by a faster second pick, or a removal on
  the same slot) can never overwrite a newer result. Both pages disable
  their own Submit button while any slot is mid-compression via a new
  `onProcessingChange` callback, AND hard-guard the submit handler itself
  (never rely on the disabled attribute alone).
  - **Multi-slot merge race (round 8-14B.1 fix):** the original
    implementation merged a finished slot's result onto `slotsRef.current`,
    which was only refreshed by a `useEffect` synced from the `slots` PROP
    — that effect runs on the NEXT render, which can lag behind two slots
    finishing microtasks apart with no parent re-render in between (e.g.
    slot 0 and slot 2 picked together), silently dropping whichever slot
    committed first. Fixed by routing every commit — a successful prepare
    OR a removal — through one `commitSlots(nextSlots)` helper that updates
    `slotsRef.current` **synchronously** before calling `onChange`, so a
    later slot's merge always sees every earlier commit regardless of
    whether React has re-rendered yet. `remove()` was changed the same
    way — it now reads `slotsRef.current`, not the possibly-stale `slots`
    prop.
  - **Stale queued work is skipped BEFORE decoding**, not just after: the
    per-slot staleness check now also runs at the very start of a queued
    task, before it ever calls `prepareInspectionPhoto` — so a slot removed
    or re-picked while still waiting behind an earlier slot in the queue
    never burns CPU on a decode/canvas/encode whose result is already known
    to be discarded.
- The multipart API contract is byte-for-byte unchanged —
  `buildRecordWithPhotosFormData` / `buildPublicRecordWithPhotosFormData`
  still send `payload` (JSON) + one `photos` part per file; no base64, no
  new field, no `photoUrls` from the client.

### Click-to-view lightbox (round 8-14C)

`AuthenticatedPhoto` (`frontend/src/components/farmlog/AuthenticatedPhoto.tsx`)
is the ONE shared component every page uses to render a stored inspection
photo, and is now also the one place a full-size lightbox is implemented —
fixing/extending it here applies automatically everywhere it's used:
Plot Detail's "ภาพถ่ายล่าสุด" (latest photo) and "ประวัติการตรวจแปลง"
(expanded history row) sections, and Record Preview's photo grid.

**The scoped, authenticated download endpoint is still the only security
boundary and is completely unchanged by this round** — `records.photoUrls`
is never usable as a plain `<img src>` (no static route serves that prefix);
every photo is still fetched as a `Blob` through
`GET /api/v1/records/{recordId}/photos/{photoId}` (the same scope/RLS check
`GET /{recordId}` uses) and rendered via a local `URL.createObjectURL(blob)`.
The lightbox is a pure display feature layered on top — it reuses the
**exact same Blob URL** the thumbnail already loaded:

- Clicking a successfully-loaded thumbnail opens a full-size popup —
  **no second fetch, no new object URL**. A thumbnail still loading or that
  failed to load is never clickable (no button rendered for either state).
- Close via the ✕ button, `Escape`, or a click on the backdrop itself (a
  click on the image or the close button never closes it — the backdrop
  handler checks `event.target === event.currentTarget`).
- `role="dialog"` / `aria-modal="true"`, focus moves to the close button on
  open and returns to the thumbnail on close (guarded — a since-unmounted
  thumbnail never throws), `Tab`/`Shift+Tab` stay locked on the close
  button (the dialog's only interactive control), and
  `document.body.style.overflow` is locked to `"hidden"` while open and
  always restored — on close AND on unmount mid-open.
- If `photoUrl`/`recordId` changes (a different photo, or the same slot
  bound to a new record) while the lightbox is open, it closes immediately,
  the previous object URL is revoked, and a fresh fetch starts for the new
  photo — matching the pre-existing revoke-on-change/revoke-on-unmount
  contract exactly; only revoking still never happens merely from
  opening/closing the popup itself.
- Old `.jpg`/`.png` photos and new `.webp` photos all open identically —
  the lightbox only ever deals in Blob object URLs, never a file extension.
- The dialog never renders the raw storage path, filename, or record UUID —
  only the already-generic `alt` text and the opaque `blob:` URL.
- Rendered via `createPortal(..., document.body)` at `z-[100]` (matching
  the existing `PlotQrPrintSheet` precedent for "must sit above every other
  modal/sidebar/header"), and carries `print:hidden` so it can never appear
  in a printed Record Preview.
- No npm dependency added — `createPortal` (already imported from
  `react-dom` elsewhere in this codebase) and the existing `X`/`ZoomIn`
  lucide icons.

## 13. Master Data Category Cleanup (rounds 8-14E / 8-14E.1 / 8-14F)

Four `master_data` categories were never read by any production consumer
(Record Form only ever used `growth_stage`/`weather` via `MasterDataButtons`
— confirmed by `rg` audit before each round below) and have been fully
retired:

- `level` ("ระดับ (เตรียม/ดูแล)")
- `severity` ("ระดับความรุนแรง")
- `irrigation` ("การให้น้ำ")
- `fertilizer` ("ปุ๋ย")

**UI (rounds 8-14E / 8-14E.1):** `frontend/src/pages/farmlog/admin/MasterData.tsx`'s
`MD_TYPES` no longer lists these 4 — the Admin > Master Data page shows only
ชนิดพืช / พันธุ์-สายพันธุ์ / ระยะการเจริญเติบโต / สภาพอากาศ / จังหวัด.

**Seed (round 8-14F):** `backend/app/db/seed.py`, `seed_mock_farmlog.py`,
and `seed_reset_farmlog_full.py` no longer define base, supplement, or mock
values for these 4 types — re-running any of these seed scripts will never
recreate them.

**Local/dev database (round 8-14F):** the pre-existing rows for these 4
types were deleted from the local/dev `master_data` table (16 rows total,
4 per type) via a single-transaction, hardcoded-type-list delete —
committed once, no partial state. **This did NOT touch Production** — the
round only ran against `APP_ENV=dev` / `DB_HOST=localhost`, confirmed
before any mutation. No backup was taken (explicit user approval for this
specific cleanup) and **no new Alembic migration was created** —
`alembic current`/`heads` were identical (`0049_auto_lot_v2_integrity`)
before and after.

`crop`/`variety`/`growth_stage`/`weather`/`province` rows, and all
business tables (`suppliers`/`plots`/`plot_cycles`/`records`), were
verified unchanged by row count before/after. The Public Master Data
allowlist (`GET /api/v1/public/masterdata`, `Literal["crop", "variety",
"growth_stage", "weather"]`) already excluded these 4 types since round
19.1 — untouched, not part of this cleanup.

**If any new production consumer needs one of these categories in the
future**, re-add its `MD_TYPES` entry in `MasterData.tsx` and its seed
values, then insert rows directly (no migration required — `master_data`
is a plain lookup table, not schema-versioned per row).

## 14. Master Data Crop/Variety Excel Import — Backend Foundation (round 8-15A)

**Backend only — no frontend UI this round.** Bulk-manages `master_data`
rows of type `crop`/`variety` only (a small subset of the Master Data admin
page, [§13](#13-master-data-category-cleanup-rounds-8-14e--8-14e1--8-14f)),
following the SAME parse/validate/preview/commit shape as Plot Import
(`app/services/plot_import.py`) but far simpler — one sheet, 3 columns, no
row locks, no multi-action resolution.

**Files:** `app/services/master_data_crop_variety_import.py` (parse/
validate/commit core + template builder), `app/services/
master_data_crop_variety_import_report.py` (result workbook, its own
self-contained styled-XLSX writer — not a shared import framework),
`app/schemas/master_data_import.py`, `app/repositories/
master_data_repository.py` (+`list_by_type_values`, additive), `app/
services/excel_workbook.py` (+`DataValidationRule`/dropdown support,
additive — Plot Import's template output is byte-identical since it never
passes the new `validations` param), routes added to `app/api/v1/
masterdata.py`.

**Endpoints** (all under `/api/v1/masterdata/crop-variety-import/`):
- `GET  .../template` — `masterdata.read`. Read-only; active crops only,
  each crop's varieties (active + inactive), a crop with none gets one
  blank-variety row; a dropdown on `varietyStatus` (closed set, blocks
  invalid entry) and a SUGGESTION dropdown on `crop` (from active crops,
  free text still allowed — `showErrorMessage=0`, omitted entirely when
  there are zero active crops yet).
- `POST .../preview` — `masterdata.create` **AND** `masterdata.update`
  (both, not either). Read-only — never flushes/commits/updates/deletes.
  Returns per-row `rowStatus` (READY/SKIPPED/ERROR) + `action` + a
  `previewState` bound to the file's SHA-256 AND every row's live
  master_data snapshot (crop/variety existed? active? variety's parent?).
- `POST .../commit` — same two permissions. Re-parses + re-validates the
  SAME file server-side (never trusts the client's JSON), re-compares
  against `previewState`; ANY drift (file, a row, or master_data changed
  underneath) → 409 `ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้ง
  ก่อนนำเข้า`. All-or-nothing in the endpoint's single `get_db` transaction
  (the service only ever flushes); crops created before varieties; an
  `IntegrityError` also becomes a clean 409, nothing written. Logs one
  `ActivityLogger` entry (`masterdata.crop_variety_import.commit`, counts
  only — never row content) on success only. **Mutates master_data.**
- `POST .../preview-report` — read-only, same core as `.../preview`.
  Callable any time after a Preview; never writes anything.
- `POST .../commit-report` — **ALSO mutates master_data** (same core as
  `.../commit`, executed exactly ONCE inside its own request — never calls
  `/commit` internally and is never meant to be called alongside it).
  Returns the completed-result `.xlsx` (COMPLETED/SKIPPED per row) instead
  of JSON.

  **`/commit` and `/commit-report` are two INDEPENDENT mutation endpoints
  for the SAME action, not a pipeline** — a real confirm click must call
  EXACTLY ONE of them, never both (calling `/commit` and then
  `/commit-report` for the "same" user action would execute the commit
  TWICE, each pass creating/activating/deactivating whatever the file
  still resolves to at that moment — the second call's fresh re-parse
  would very likely conflict with what the first call already wrote, but
  relying on that 409 as a safety net is not the intended contract).
  **Round 8-15B's frontend should pick `/commit-report` as the sole commit
  call** if it wants to offer a "download completed workbook" action
  (`/commit-report`'s response already contains everything the JSON result
  does, expressed as an `.xlsx`); use the plain JSON `/commit` only if the
  UI has no workbook-download step at all.

**Business rules (enforced server-side, Excel dropdowns are a UX aid
only):** `crop` is create-only through this file — an existing INACTIVE
crop blocks its row (`ชนิดพืชนี้ปิดใช้งานอยู่ กรุณาเปิดใช้งานผ่านหน้า Master Data
ก่อน`); **Excel can never open/close a crop**, that stays an App-only
action. `variety` is optional (blank = crop-only row); its `parent` is
always the row's own crop (never re-parented) and `active` follows
`varietyStatus` (blank defaults active); an existing variety already bound
to a DIFFERENT crop is a hard error, never migrated. Never hard-deletes,
never renames. Duplicate (crop,variety) pairs, duplicate crop-only rows,
and the same variety value under two different crops in one file are all
rejected (mirrors the DB's own unique `(type, value)` index — a variety
name can only ever belong to one crop, in the table just as in the file).

No migration this round — `alembic current`/`heads` unchanged
(`0049_auto_lot_v2_integrity`). Live-verified read-only (Template GET +
Preview against the real dev DB, including a genuinely new crop/variety) —
`master_data` row counts identical before/after; **commit was never run
live**, only via DB-free pytest mocks (`tests/unit/
test_master_data_crop_variety_import_service.py`).

### 14.1 Boundary/Dropdown/Count Hardening (round 8-15A.1)

Three small gaps closed before frontend work starts, backend-only:

1. **previewState input-boundary hardening** — the multipart `previewState`
   field is now bounds-checked in two layers before it reaches the service:
   `schemas/master_data_import.py`'s Field constraints (`rowNumber>0`,
   `crop`/`variety`/`varietyParentAtPreview<=255` chars — the same cap
   `master_data.value`'s column enforces, `fileSha256` must be 64 lowercase
   hex chars, `action` restricted to a `Literal` of the 6 known values), then
   `app/api/v1/masterdata.py`'s `_parse_cv_preview_state` (raw UTF-8 byte
   size ≤2 MB checked BEFORE any JSON parsing, `rows` length ≤
   `MAX_IMPORT_ROWS`, no duplicate `rowNumber`). Every failure path returns
   the SAME generic 422 (`previewState ไม่ถูกต้อง`) — never echoes the raw
   string or any submitted field. previewState remains a plain optimistic-
   concurrency expectation, never a credential; masterdata.create+update is
   still required regardless, and the service still re-derives the real
   plan from a fresh parse + fresh DB query every time.
2. **Scalable crop dropdown** — the crop suggestion dropdown no longer uses
   an inline literal list (Excel caps that around 255 characters). The
   template workbook now carries a hidden `_reference` sheet (one crop
   value per row) plus a workbook-scoped defined name
   (`_CV_CROP_OPTIONS`, ranged to exactly the active-crop count) that the
   `crop` column's dropdown formula points at instead.
   `services/excel_workbook.py`'s `build_xlsx` gained
   `hidden_sheets`/`defined_names` params for this — fully additive/generic
   (the writer has no idea what "crop" means; every other sheet/caller,
   including Plot Import, is byte-identical since neither param is passed).
   `varietyStatus` is unaffected — still a small inline closed list.
3. **Commit result count de-duplication** — `createdCrops` could be
   over-counted by `/commit-report`'s workbook summary and its
   `ActivityLogger` metadata: both used to tally `row_views[i]["action"]`
   per ROW, so one new crop shared by several new-variety rows (e.g. 1 crop
   + 2 varieties under it) was reported as 2 created crops instead of 1.
   The plain JSON `/commit` result and `/preview`'s summary were already
   correct (both dedupe via a Python `set` internally). Fixed by having
   `commit_row_views`/`preview_row_views` (the service functions both
   report-workbook endpoints call) also return the AUTHORITATIVE
   `CropVarietyImportCommitResult`/`CropVarietyImportSummary` object, and
   having the workbook builder + the endpoints' `ActivityLogger` calls
   source every count from THAT object — never re-derived from `row_views`.
   All four surfaces (JSON commit result, commit-report workbook, activity
   log, preview summary) now agree by construction.

No migration, no live commit, no frontend change in this round either —
same posture as 8-15A. See `tests/unit/
test_master_data_crop_variety_import_preview_state_boundary.py` (Part B),
the Part C block appended to `test_master_data_crop_variety_import_
template.py`, and `tests/unit/test_master_data_crop_variety_import_
count_alignment.py` (Part D) for the full regression coverage.

### 14.2 Frontend — Admin UI (round 8-15B)

Admin > Master Data gained a crop/variety import entry point, wired to the
backend contract above:

- **`GET .../template`** — a standalone "ดาวน์โหลด Template (ชนิดพืชและพันธุ์)"
  button, gated on `masterdata.read` alone (works even for a user who can't
  import).
- **`.../preview` → `.../commit-report`** — `MasterDataCropVarietyImportModal`
  (`frontend/src/components/farmlog/MasterDataCropVarietyImportModal.tsx`),
  opened via a "นำเข้า Excel (ชนิดพืชและพันธุ์)" button gated on **BOTH**
  `masterdata.create` AND `masterdata.update` (same dual-permission gate the
  backend itself enforces on those two routes).
- **Sole commit-report rule, enforced structurally, not just by
  convention**: `frontend/src/api/masterdata.ts` exports NO function that
  calls the plain JSON `/commit` endpoint at all — there is nothing in this
  app's frontend that COULD call it, let alone alongside `/commit-report`.
- **previewState / stale-state UX**: the modal echoes back the exact
  `previewState` object from the last successful Preview as a commit
  mutation variable (never read via closure, so a race with a fresh
  re-preview can't leak a stale snapshot). A 409 clears the on-screen
  preview entirely and requires a fresh Preview; a 422 (row errors found on
  the server's own re-check) shows the backend's message and, if the
  backend embedded a re-computed preview, refreshes the table with it —
  neither case auto-retries or auto-commits.
- A successful commit auto-downloads the completed workbook and calls
  `onImported`, which the page wires to
  `qc.invalidateQueries({ queryKey: ['masterdata'] })` — the same
  invalidation every other mutation on this page already uses, so the
  crop/variety tabs pick up new rows on their normal refetch.
- Browser QA (desktop/mobile layout, real click-through) was **NOT RUN** in
  round 8-15B — no browser automation tool was available in that session
  either; only jsdom component/integration tests.

### 14.3 Live E2E QA (round 8-15C)

A full Template → Preview → Preview-Report → **live Commit-Report** →
post-commit verification → idempotent re-preview → conflict-safety pass ran
against the real local/dev stack (never production), gated behind an
explicit user approval before the one live write. Every number matched the
contract exactly — see the round's own Final Report for full detail; summary:

- **QA data created and RETAINED in the local/dev DB** (no hard delete, per
  the round's explicit instruction):
  - crop `QA-815C-202608061509` (active)
  - variety `QA-VARIETY-815C-202608061509-A`, parent = that crop, active
  - variety `QA-VARIETY-815C-202608061509-B`, parent = that crop, inactive
- Live commit-report result: `createdCrops=1`, `createdVarieties=2` (NOT
  double-counted despite 2 varieties sharing 1 new crop — the round 8-15A.1
  Part D fix confirmed correct against a REAL commit, not just unit mocks),
  exactly one `activity_logs` row (`masterdata.crop_variety_import.commit`,
  `via: "commit-report"`).
- Idempotent re-preview of the SAME (now-committed) file correctly resolved
  both rows to SKIPPED (`readyRows=0`, `cropsToCreate=0`,
  `varietiesToCreate=0`) — re-uploading an already-imported file is a safe
  no-op, never a duplicate-creation risk.
- Conflict safety: a byte-modified copy of the file (same parsed meaning,
  different SHA-256) sent with the OLD `previewState` was correctly
  rejected with 409 and the exact message
  `ข้อมูล Master Data มีการเปลี่ยนแปลง กรุณาตรวจสอบไฟล์อีกครั้งก่อนนำเข้า` — zero
  rows written.
- The crop/varietyStatus dropdowns and the hidden `_reference` sheet /
  `_CV_CROP_OPTIONS` defined name (round 8-15A.1's Part C design) were
  verified against a REAL downloaded template opened in actual Microsoft
  Excel via COM automation (not just XML parsing) — both dropdowns and the
  hidden-sheet mechanism work exactly as designed.
- **Browser QA remained NOT RUN** in this round too — no browser automation
  tool was available; API-level E2E covered every JSON/workbook contract
  point instead. A manual browser QA pass (desktop ~1440×900, mobile
  ~390×844) is still owed before this feature is considered fully verified
  end-to-end.
- Test baseline after this round: backend **2335 passed**, frontend
  **1575 passed** (both unchanged in count from round 8-15B/8-15A.1 — this
  round added no new automated tests, only live-verified the existing
  contract; the one code change was a comment-only fix in
  `app/api/v1/masterdata.py` aligning a stale `row_number>=3` docstring
  reference with the actual `Field(..., gt=0)` constraint).

### 14.4 Active Master Data Enforcement (round 8-15D)

Until this round, `crop`/`variety` on a PlotCycle were **free text** — nothing
checked them against `master_data` on any mutation path (verified by source
audit: zero `master_data` references in `plots.py`, `plot_import.py`, or
`plot_cycle_repository.py`). A typo, or a value an admin had deliberately
deactivated, went straight into a new cycle.

**Business contract** — a NEW cycle's crop/variety, and any CHANGE to an
existing active cycle's, must exist in `master_data` and be `active=true`;
a variety must additionally be parented to the chosen crop:

| Input | Result |
|---|---|
| crop and variety both blank | allowed (neither is mandatory) |
| variety set, crop blank | 422 `กรุณาระบุชนิดพืชก่อนเลือกพันธุ์` |
| crop not found / inactive | 422 naming the reason |
| variety not found / inactive | 422 naming the reason |
| variety's `parent` ≠ chosen crop | 422 `พันธุ์ "…" ไม่ได้อยู่ภายใต้ชนิดพืช "…"` |

**History is never invalidated retroactively.** The validator compares the
*effective* pair against the cycle's *current* pair: if they are identical it
returns immediately, so a cycle whose crop was deactivated later still
accepts edits to unrelated fields (lot, yield, plant count). Any real change
re-validates the WHOLE effective pair — so changing crop while repeating the
same variety string still catches a now-mismatched parent.

**Where it runs** — `app/services/master_data_validation.py`, two shapes so
the rule lives in exactly one place:

- `assert_crop_variety_valid(...)` — one pair, raises `HTTPException(422)`.
  Called before the mutation in `create_plot_with_cycle`, `start_plot_cycle`,
  `rollover_plot_cycle`, `reactivate_plot_with_cycle`, and `update_plot_cycle`.
- `load_crop_variety_lookup(...)` + `crop_variety_errors(...)` — batch shape
  for Plot Excel Import. `_apply_master_data_crop_variety_checks` runs as a
  second pass in `_validate_all` (same pattern as the cycleLabel-history
  check) and issues **exactly two queries per file** regardless of row
  count — locked in by `test_master_data_lookup_batches_into_exactly_two_queries`.

**Never blocked by this:** `close_cycle` and the Excel `final_plot` action.
Both only conclude an existing cycle, so `needs_master_data_check` is never
set for them (a `final_plot` row issues zero Master Data queries — asserted).

**Frontend** — `MasterDataSelect` still queries `activeOnly=true`. A current
value missing from the active list renders as `"<value> (ปิดใช้งาน/ค่าเดิม)"`
and is `disabled`: still visible so the field never blanks out and looks like
data loss, but not re-selectable once the user picks something else.

**Test defaults** — `backend/tests/unit/conftest.py` patches the validator
permissive by default, because dozens of pre-existing plot/cycle tests use
arbitrary crop/variety strings that were never meant to be checked. Tests
that exercise the rule opt out with `@pytest.mark.nodefault_crop_variety`
and patch the repo with explicit active/inactive/parent fixtures.

Test baseline after this round: backend **2376 passed**, frontend
**1579 passed** (+41 / +4 new). Live read-only verification on dev confirmed
a Plot Import Preview of an inactive variety returns `errorRows=1` with the
exact message above and zero DB writes.

## 15. Related Documents

| Doc | Covers |
|---|---|
| [`PLOT_CYCLE_HANDOVER.md`](PLOT_CYCLE_HANDOVER.md) | Full Plot/PlotCycle/Record concurrency, locking, and RLS internals (rounds 7-1→8-1) |
| [`docs/deployment.md`](../deployment.md) | Docker/Compose reference, canonical local + production commands |
| [`docs/security.md`](../security.md) | Security posture, RLS design |
| [`docs/testing.md`](../testing.md) | Test conventions (DB-less backend, Vitest frontend) |
| [`docs/human/onboarding.md`](../human/onboarding.md) | New-developer setup (requires proposed-diff approval to change) |
