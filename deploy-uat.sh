#!/usr/bin/env bash
# deploy-uat.sh — deploy a committed revision to the UAT host.
#
#   ./deploy-uat.sh              # deploy HEAD
#   ./deploy-uat.sh <git-ref>    # deploy a specific commit/tag/branch
#   ./deploy-uat.sh --dry-run    # back up + build + verify, then STOP
#
# --dry-run still writes a verified backup on the host and tags the current
# images for rollback; what it skips is shipping, migrating and swapping. So
# it changes nothing a user can see, but it is not read-only.
#   ./deploy-uat.sh rollback     # put the previous images back
#
# WHY THIS EXISTS INSTEAD OF deploy.sh
# -----------------------------------
# deploy.sh is written for a production host that has a git clone and pulls
# `main`. UAT has NEITHER: /opt/uat_SRM is a plain directory (no .git anywhere
# on the box) and the remote's only branch is initial-import. Its first real
# command would fail. Do not point deploy.sh at UAT.
#
# Six things went wrong the first time this deploy was done by hand. All six
# are handled below; each is marked LESSON so nobody has to rediscover it:
#
#   1. `source backend/.env` fails — values contain spaces (APP_NAME=SRM
#      FieldInspect UAT). Individual keys are read with grep/cut instead.
#   2. The client pg_dump must match the server's major version.
#   3. A dump taken as the table OWNER silently contains ZERO rows for every
#      RLS table (FORCE row security + policies granted only to the app role).
#      The dump is taken as the app role with app.scope=all, and then VERIFIED
#      against live row counts. A dump that fails verification aborts the
#      deploy — it is worse than no backup, because it looks like one.
#   4. A working-tree build context (.venv, node_modules) is big enough to
#      kill buildkit. The context is always `git archive <ref>`, which also
#      guarantees only committed code ships.
#   5. `docker compose build` builds both images at once and crashed buildkit
#      repeatedly. They are built one at a time.
#   6. The UAT box cannot build at all (~480Mi free of 3.4Gi, shared with 12
#      containers from five other projects). Images are built here and shipped
#      with docker save | ssh docker load.
#
# The frontend bundle is environment-specific: Vite inlines VITE_* at BUILD
# time. The build args are read from the TARGET host's own compose env file at
# deploy time, never hardcoded here — build with the wrong ones and the app
# ships pointing at localhost:8000.
#
# MIGRATIONS: this runs `alembic upgrade head` BEFORE swapping containers, so
# the old container keeps serving during the migration. That is correct only
# for backward-compatible migrations (new nullable columns, new tables). A
# migration that drops or renames something WILL break the running container
# in that window — deploy those by hand, in a maintenance window.

set -euo pipefail

UAT_HOST="${UAT_HOST:-root@101.44.63.140}"
UAT_KEY="${UAT_KEY:-$HOME/.ssh/uat_ed25519}"
UAT_DIR="${UAT_DIR:-/opt/uat_SRM}"
BACKEND_IMAGE="${BACKEND_IMAGE:-uat_srm-backend}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-uat_srm-frontend}"
HEALTH_RETRIES="${HEALTH_RETRIES:-20}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=""
DRY_RUN=0

say()  { printf '\n=== %s ===\n' "$*"; }
die()  { printf '\nDEPLOY ABORTED: %s\n' "$*" >&2; exit 1; }
sshu() { ssh -i "$UAT_KEY" -o BatchMode=yes -o ConnectTimeout=15 "$UAT_HOST" "$@"; }

cleanup() { [ -n "$WORK" ] && [ -d "$WORK" ] && rm -rf "$WORK"; }
trap cleanup EXIT

# ---------------------------------------------------------------- rollback --
do_rollback() {
    say "ROLLBACK"
    sshu bash -s <<REMOTE
set -euo pipefail
STAMP=\$(cat /opt/.srm_last_backup_stamp 2>/dev/null) || {
    echo "no backup stamp on the host — nothing to roll back to" >&2; exit 1; }
echo "restoring images tagged rollback_\$STAMP"
docker tag ${BACKEND_IMAGE}:rollback_\$STAMP  ${BACKEND_IMAGE}:latest
docker tag ${FRONTEND_IMAGE}:rollback_\$STAMP ${FRONTEND_IMAGE}:latest
cd ${UAT_DIR} && docker compose up -d --no-build
echo
echo "Images rolled back. The DATABASE was NOT touched: if this deploy ran a"
echo "migration, undo it deliberately with"
echo "  cd ${UAT_DIR} && docker compose run --rm --no-deps -T backend alembic downgrade <rev>"
echo "The pre-deploy dump is in /opt/uat_SRM_backup_\$STAMP/."
REMOTE
}

# --------------------------------------------------------------- preflight --
preflight() {
    say "PREFLIGHT"
    command -v docker >/dev/null || die "docker not found on this machine"
    command -v git >/dev/null    || die "git not found on this machine"
    [ -f "$UAT_KEY" ]            || die "ssh key not found: $UAT_KEY"
    git -C "$REPO_ROOT" rev-parse --verify "$REF^{commit}" >/dev/null 2>&1 \
        || die "not a commit: $REF"

    SHA="$(git -C "$REPO_ROOT" rev-parse --short "$REF")"
    echo "ref        : $REF -> $SHA"
    echo "host       : $UAT_HOST:$UAT_DIR"

    # Uncommitted work is NOT deployed — the context comes from git archive.
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
        echo
        echo "NOTE: the working tree has uncommitted changes. They will NOT be"
        echo "      deployed — this ships $SHA exactly."
    fi
    sshu true || die "cannot ssh to $UAT_HOST"
    echo "ssh        : ok"
}

# ------------------------------------------------------------------ backup --
backup() {
    say "BACKUP (database + files + current images)"
    # LESSON 1/2/3 all live in this remote block. The `if !` form is used
    # because a `|| { ... }` continuation would be swallowed by the heredoc.
    if ! sshu bash -s > /tmp/.srm_backup_out 2>&1 <<REMOTE
set -euo pipefail
STAMP=\$(date +%Y%m%d_%H%M%S)
BK=/opt/uat_SRM_backup_\$STAMP
mkdir -p "\$BK"
E=${UAT_DIR}/backend/.env

# LESSON 1 — read keys, never `source`: values contain spaces.
g() { grep -m1 "^\$1=" "\$E" | cut -d= -f2- ; }
DB_HOST=\$(g DB_HOST); DB_PORT=\$(g DB_PORT); DB_NAME=\$(g DB_NAME)
OWNER=\$(g DB_USER);   OWNER_PW=\$(g DB_PASSWORD)
APP=\$(g DB_APP_USER); APP_PW=\$(g DB_APP_PASSWORD)
: "\${DB_PORT:=5432}"

# LESSON 2 — client major version must match the server's.
PGMAJ=\$(docker run --rm -e PGPASSWORD="\$OWNER_PW" postgres:18-alpine \
    psql -h "\$DB_HOST" -p "\$DB_PORT" -U "\$OWNER" -d "\$DB_NAME" -t -A \
    -c "show server_version_num" 2>/dev/null | cut -c1-2 || echo 18)
PGIMG="postgres:\${PGMAJ}-alpine"
echo "pg client  : \$PGIMG"

# Schema as the owner — no RLS involved.
docker run --rm -e PGPASSWORD="\$OWNER_PW" "\$PGIMG" \
  pg_dump -h "\$DB_HOST" -p "\$DB_PORT" -U "\$OWNER" -d "\$DB_NAME" \
          --schema-only --no-owner > "\$BK/db_schema.sql"

# LESSON 3 — data as the APP role, with the GUC the RLS policies read.
docker run --rm -e PGPASSWORD="\$APP_PW" -e PGOPTIONS="-c app.scope=all" "\$PGIMG" \
  pg_dump -h "\$DB_HOST" -p "\$DB_PORT" -U "\$APP" -d "\$DB_NAME" \
          --data-only --no-owner --enable-row-security > "\$BK/db_data.sql"

# Live counts for the verifier to compare against.
docker run --rm -e PGPASSWORD="\$APP_PW" -e PGOPTIONS="-c app.scope=all" "\$PGIMG" \
  psql -h "\$DB_HOST" -p "\$DB_PORT" -U "\$APP" -d "\$DB_NAME" -t -A -F= -c \
  "select 'plots',count(*) from plots
   union all select 'plot_cycles',count(*) from plot_cycles
   union all select 'records',count(*) from records
   union all select 'plot_access_phones',count(*) from plot_access_phones
   union all select 'plot_access_credentials',count(*) from plot_access_credentials" \
  > "\$BK/live_counts.txt"

tar czf "\$BK/uat_SRM_files.tar.gz" -C "\$(dirname ${UAT_DIR})" "\$(basename ${UAT_DIR})"
docker tag ${BACKEND_IMAGE}:latest  "${BACKEND_IMAGE}:rollback_\$STAMP"
docker tag ${FRONTEND_IMAGE}:latest "${FRONTEND_IMAGE}:rollback_\$STAMP"
echo "\$STAMP" > /opt/.srm_last_backup_stamp
echo "backup dir : \$BK"
du -h "\$BK/db_data.sql" "\$BK/uat_SRM_files.tar.gz" | sed 's/^/           /'
REMOTE
    then
        cat /tmp/.srm_backup_out >&2
        die "backup failed"
    fi
    cat /tmp/.srm_backup_out
    STAMP="$(sshu cat /opt/.srm_last_backup_stamp)"

    # LESSON 3, second half — a dump is not a backup until its rows are counted.
    say "VERIFY BACKUP (a zero-row dump aborts the deploy)"
    sshu "mkdir -p /tmp/srm-deploy" </dev/null
    scp -q -i "$UAT_KEY" -o BatchMode=yes \
        "$REPO_ROOT/scripts/verify_pg_dump_rows.py" \
        "$UAT_HOST:/tmp/srm-deploy/verify_pg_dump_rows.py"
    sshu bash -s <<REMOTE || die "the backup does not contain the rows it should — NOT deploying"
set -euo pipefail
BK=/opt/uat_SRM_backup_${STAMP}
EXPECT=\$(sed 's/=/=/' "\$BK/live_counts.txt" | tr '\n' ' ')
docker run --rm -v /tmp/srm-deploy:/s:ro -v "\$BK":/bk:ro \
  --entrypoint python ${BACKEND_IMAGE}:latest \
  /s/verify_pg_dump_rows.py /bk/db_data.sql --expect \$EXPECT
REMOTE
}

# ------------------------------------------------------------------- build --
build() {
    say "BUILD $SHA (from git archive — committed code only)"
    WORK="$(mktemp -d)"
    git -C "$REPO_ROOT" archive "$REF" | tar -x -C "$WORK"
    : > "$WORK/backend/.env"   # compose parses env_file even when only building

    # The frontend bundle is baked per environment: take the build args from
    # the TARGET's own compose env file so the bundle always matches the host
    # it is going to.
    sshu "cat ${UAT_DIR}/.env" > "$WORK/uat.build.env"
    echo "build args from ${UAT_DIR}/.env:"
    sed 's/^/           /' "$WORK/uat.build.env"

    # LESSON 5 — one at a time; `compose build` runs both and crashes buildkit.
    ( cd "$WORK/backend" && docker build -q -t "${BACKEND_IMAGE}:${SHA}" . ) >/dev/null
    echo "backend    : built"

    local args=()
    while IFS='=' read -r k v; do
        [ -n "${k:-}" ] && [ "${k#\#}" = "$k" ] && args+=(--build-arg "$k=$v")
    done < "$WORK/uat.build.env"
    ( cd "$WORK/frontend" && docker build -q "${args[@]}" -t "${FRONTEND_IMAGE}:${SHA}" . ) >/dev/null
    echo "frontend   : built"

    docker tag "${BACKEND_IMAGE}:${SHA}"  "${BACKEND_IMAGE}:latest"
    docker tag "${FRONTEND_IMAGE}:${SHA}" "${FRONTEND_IMAGE}:latest"
}

# -------------------------------------------------------------------- ship --
ship() {
    say "SHIP IMAGES"
    docker save "${BACKEND_IMAGE}:${SHA}" "${BACKEND_IMAGE}:latest" \
                "${FRONTEND_IMAGE}:${SHA}" "${FRONTEND_IMAGE}:latest" \
        | gzip -1 | sshu "gunzip | docker load" | sed 's/^/           /'

    say "SYNC SOURCE (every .env excluded — they hold the OBS keys and secrets)"
    local before after
    before="$(sshu "md5sum ${UAT_DIR}/backend/.env ${UAT_DIR}/frontend/.env ${UAT_DIR}/.env")"
    tar czf - --exclude='.env' -C "$WORK" . | sshu "tar xzf - -C ${UAT_DIR}"
    after="$(sshu "md5sum ${UAT_DIR}/backend/.env ${UAT_DIR}/frontend/.env ${UAT_DIR}/.env")"
    [ "$before" = "$after" ] || die "an .env file changed during sync — restore from the backup"
    echo "           .env files untouched (md5 identical)"
}

# ----------------------------------------------------------------- migrate --
migrate() {
    say "MIGRATE"
    echo "current:"
    sshu "cd ${UAT_DIR} && docker compose run --rm --no-deps -T backend alembic current" \
        2>/dev/null | tail -1 | sed 's/^/           /'
    sshu "cd ${UAT_DIR} && docker compose run --rm --no-deps -T backend alembic upgrade head" \
        2>&1 | grep -E 'Running upgrade|ERROR' | sed 's/^/           /' || true
    echo "now:"
    sshu "cd ${UAT_DIR} && docker compose run --rm --no-deps -T backend alembic current" \
        2>/dev/null | tail -1 | sed 's/^/           /'
}

# -------------------------------------------------------------------- swap --
swap_and_check() {
    say "SWAP CONTAINERS"
    # Only this project's two services — the box runs five other projects.
    sshu "cd ${UAT_DIR} && docker compose up -d --no-build" 2>&1 \
        | grep -E 'Recreated|Started' | sed 's/^/           /'

    say "HEALTH GATE"
    local i b f
    for i in $(seq 1 "$HEALTH_RETRIES"); do
        b="$(sshu "docker inspect -f '{{.State.Health.Status}}' srm-fieldinspect-backend" 2>/dev/null || echo unknown)"
        f="$(sshu "docker inspect -f '{{.State.Health.Status}}' srm-fieldinspect-frontend" 2>/dev/null || echo unknown)"
        printf '           %2d backend=%s frontend=%s\n' "$i" "$b" "$f"
        [ "$b" = healthy ] && [ "$f" = healthy ] && { echo "           both healthy"; return 0; }
        sleep 3
    done
    echo "HEALTH GATE FAILED — rolling the images back" >&2
    do_rollback
    die "deploy rolled back; the database was left as-is (see the backup dir)"
}

# -------------------------------------------------------------------- main --
case "${1:-}" in
    rollback) do_rollback; exit 0 ;;
    --dry-run) DRY_RUN=1; REF="${2:-HEAD}" ;;
    *)        REF="${1:-HEAD}" ;;
esac

preflight
backup
build
if [ "$DRY_RUN" = 1 ]; then
    say "DRY RUN — UAT was backed up and the images built, but nothing was deployed"
    exit 0
fi
ship
migrate
swap_and_check

say "DONE — $SHA is live"
echo "rollback with:  ./deploy-uat.sh rollback"
echo "backup dir   :  /opt/uat_SRM_backup_${STAMP}"
