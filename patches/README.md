# Existing Project Patches

Small compatibility patches for projects that were already generated from this
template. Copy only the relevant patch script into the generated project root,
run it, then review the diff before committing.

## v3.1.0 — Database Connections + Query Sandbox (opt-in module)

Adds the opt-in module: super_admin registers external PostgreSQL targets via the
UI and runs audited, read-only-by-default SQL. **Only run this if the project
needs the feature** — it stores external-DB credentials (Fernet-encrypted) and
executes admin-authored SQL. Read [`../docs/patterns/db-connections.md`](../docs/patterns/db-connections.md)
and get **SECURITY_APPROVER** sign-off first.

Run from the generated project root:

```bash
python patches/v3_1_0_db_connections_patch.py
# generate a Fernet key and set DB_CONNECTIONS_ENCRYPTION_KEY in backend/.env:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
cd backend && alembic upgrade head
python -m app.seed     # seeds 3 permissions + 2 menus + sandbox limits
```

The patch is idempotent: it writes the module files, wires the router/seed/frontend
behind `FEATURE_DB_CONNECTIONS`, sets the flag in `project.config`, and chains
migration `0011` onto the project's current Alembic head (run `alembic heads` first
if you have unmerged heads).

## v3.0.15 — login + app title

Fixes:

- email login / seed / user creation are case-insensitive
- existing `users.email` values are normalized by Alembic migration `0010`
- Login password field has show/hide toggle
- `VITE_APP_NAME` is populated from `PROJECT_DISPLAY_NAME` for the TopBar

Run from the generated project root:

```bash
python patches/v3_0_15_login_app_name_patch.py
cd backend
alembic upgrade head
```

If migration `0010` stops with a case-insensitive duplicate email error, merge
those duplicate user accounts deliberately before running `alembic upgrade head`
again.

If you ran the first patch draft and `alembic upgrade head` reports multiple
heads, edit:

```text
backend/alembic/versions/2026_01_02_0600-0010_normalize_user_emails.py
```

Change its `down_revision` from `"0009_user_approval_fields"` to the existing
latest head shown by:

```bash
alembic heads
```

Then run:

```bash
alembic upgrade head
```
