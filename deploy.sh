#!/bin/bash
# deploy.sh — Production deploy (hardened: backup + health gate + auto-rollback)
# รันบน production server: ./deploy.sh
#
# ลำดับ: backup DB -> build image ใหม่ -> migrate -> รอ health -> ถ้าพัง rollback อัตโนมัติ
# user จะไม่ค้างอยู่บนแอปที่ boot ไม่ขึ้น เพราะถ้า health ไม่ผ่านจะถอยกลับ commit เดิมให้
#
# Prerequisites (IT ต้องเตรียมก่อน):
#   - backend/.env       <- copy จาก backend/.env.prod.example แล้วแก้ค่าจริง
#   - network proxy-net  <- docker network create proxy-net
#   - git clone repo ไว้บน server แล้ว
#
# First time setup:
#   git clone <repo-url> /opt/srm-fieldinspect
#   cp backend/.env.prod.example backend/.env   # แก้ค่าจริง
#   docker network create proxy-net
#   ./deploy.sh
#
# Override ได้ผ่าน env: HEALTH_URL, HEALTH_RETRIES, BACKUP_DIR

set -eo pipefail

COMPOSE="docker compose"

[ -n "$HEALTH_URL" ] || HEALTH_URL="http://localhost:8000/health/ready"
[ -n "$HEALTH_RETRIES" ] || HEALTH_RETRIES=30
[ -n "$BACKUP_DIR" ] || BACKUP_DIR="./backups"

PREV_REF="$(git rev-parse HEAD)"
echo "Deploy srm-fieldinspect ... (commit เดิม: $PREV_REF)"

# 1. Backup DB ก่อน migrate เสมอ (best-effort; centralized DB ให้ประสาน DBA)
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%F_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/db_$STAMP.sql"
if command -v pg_dump >/dev/null 2>&1 && [ -n "$DB_NAME" ] && [ -n "$DB_HOST" ] && [ -n "$DB_USER" ]; then
  echo "Backup DB -> $BACKUP_FILE"
  PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"
else
  echo "WARN: ข้าม DB backup (ไม่มี pg_dump หรือ DB_* ไม่ครบ) — backup centralized DB ก่อนต่อ" >&2
fi

rollback() {
  echo "!! Deploy ล้มเหลว — rollback code กลับ $PREV_REF" >&2
  git reset --hard "$PREV_REF"
  $COMPOSE up -d --build
  echo "Rolled back code. ถ้า migrate รันไปแล้ว schema อาจเปลี่ยน — restore: psql ... < $BACKUP_FILE" >&2
  exit 1
}

# 2. Pull + build image ใหม่ (ยังไม่สลับ container)
git pull origin main
$COMPOSE build || rollback

# 3. Migrate — เขียนแบบ backward-compatible เพื่อให้ container เก่ายังทำงานได้ (ดู docs/database.md §6.4)
$COMPOSE up -d
$COMPOSE exec -T backend alembic upgrade head || rollback

# 4. Health gate — รอ /health/ready เขียว ไม่งั้น rollback อัตโนมัติ
echo "รอ health ($HEALTH_URL) สูงสุด $HEALTH_RETRIES ครั้ง..."
ok=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ "$ok" = "1" ] || rollback

echo "Deploy เสร็จ — $(git rev-parse HEAD) healthy"
