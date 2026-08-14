# docs/human/runbook.md

> Operations runbook — for production support and incident response
>
> **For AI agents:** this file is auto-maintained. Update when deployment, monitoring, or incident patterns change. Flag changes for user review.

---

## 1. Production Environment

### 1.1 Service Topology

```
Internet
   │
   ▼
[Reverse Proxy / WAF]   (e.g., nginx, Traefik, Cloudflare)
   │
   ├─→ frontend (nginx serving SPA, 1+ replicas)
   │
   └─→ backend (FastAPI, 2+ replicas)
            │
            ▼
       [PostgreSQL 16]   (primary + replica)
```

### 1.2 Environments

| Env | URL | Branch | Auto-deploy | Approval |
|---|---|---|---|---|
| Local | `http://localhost:5173` | any | manual | none |
| Staging | (from `project.config`) | `main` | yes (on merge) | none |
| Production | (from `project.config`) | tags `v*.*.*` | no | required |

### 1.3 Container Hosts

Document specific hosts (update per project):

| Env | Host | Compose file |
|---|---|---|
| Staging | `staging.internal` | `/opt/<slug>/docker-compose.yml` |
| Production | `prod-1.internal`, `prod-2.internal` | `/opt/<slug>/docker-compose.yml` |

---

## 2. Deployment Procedure

### 2.0 Pre-Deploy Checklist (ทุก deploy — ติ๊กให้ครบก่อนลงมือ)

- [ ] **Backup DB แล้ว** (หรือยืนยันกับ DBA ว่า centralized DB มี snapshot ล่าสุด) — `deploy.sh` ทำ best-effort ให้ แต่ centralized DB ต้องประสาน DBA
- [ ] **Migration backward-compatible** — เพิ่ม column = nullable/มี default; ลบ/เปลี่ยนชื่อ = แยกหลาย deploy (ดู `docs/database.md` §6.4)
- [ ] **ซ้อมบน staging ที่ restore จาก backup prod จริง** แล้ว และคลิก flow หลักผ่าน
- [ ] **รู้คำสั่ง rollback (§3)** และ commit/image เดิมยัง redeploy ได้
- [ ] อ่าน `CHANGELOG.md` / `MIGRATION.md` ของ version ที่จะขึ้น — มี breaking change / manual step ไหม
- [ ] หลัง deploy: `/health` + `/health/ready` เขียว (`deploy.sh` เช็ค + auto-rollback ให้อยู่แล้ว)

### 2.1 Staging (automatic)

Trigger: merge to `main`

Pipeline:
1. Lint + test (quality gate)
2. SSH to staging host
3. `git pull` + `docker compose up -d --build` (build จาก source บน host)
4. Run pending migrations
5. Smoke test `/health`

**Operator action:** verify deploy succeeded in CI dashboard

### 2.2 Production (manual)

```bash
# 1. From local machine: create release tag
git checkout main
git pull
git tag -a v1.2.3 -m "Release 1.2.3"
git push origin v1.2.3

# 2. In GitHub Actions: approve "Deploy Production" workflow

# 3. Pipeline SSH เข้า prod host แล้ว:
#    a. git fetch --tags && git checkout <tag>
#    b. docker compose up -d --build   (build จาก source)
#    c. docker compose exec backend alembic upgrade head
#    d. smoke test /health
```

### 2.3 Manual Production Deploy (Emergency Only)

If CI is broken and immediate fix needed:

```bash
ssh prod-1.internal
cd /opt/<project_slug>

# Step 1: pull + build จาก source
git fetch --tags
git checkout <emergency-tag-หรือ-commit-SHA>
docker compose up -d --build

# Step 2: migration (ถ้ามี schema change)
docker compose exec backend alembic upgrade head

# Step 3: verify
curl -fsS https://<production-url>/health
```

> DB เป็น centralized server — ถ้าต้อง backup ก่อน ให้ประสาน IT/DBA (ดู §6)

**After:** create post-incident ticket explaining bypass of normal pipeline.

### 2.4 Patch / Version Upgrade (รวม big jump เช่น current → v40)

การกระโดดข้ามหลาย version = รัน migration สะสม + การเปลี่ยนแปลงทั้งหมดพร้อมกัน เสี่ยงกว่า patch รอบเดียว:

1. **ซ้อม staging จาก backup prod เป็นด่านหลัก** (ไม่ใช่ optional) — จับ migration ที่พังเพราะข้อมูลจริง (เช่น duplicate ก่อนใส่ unique, multiple alembic heads) ก่อนถึง user
2. **ตรวจ migration dependency:** `alembic history` — ถ้ามี expand → backfill → contract คร่อมหลาย version อาจต้อง **ไล่เป็นช่วง** (current → vXX → v40) ไม่ใช่กระโดดทีเดียว
3. **อ่าน `MIGRATION.md` + `CHANGELOG.md`** ช่วง version ที่ข้าม หา breaking change / manual step
4. **Backup คือเส้นชีวิตของ big jump** — `alembic downgrade` ข้ามหลาย migration ไม่ปลอดภัย; rollback จริง = restore backup (ยอมรับว่าข้อมูลหลัง backup หาย)
5. รัน `./deploy.sh` (มี backup + health gate + auto-rollback ในตัว) แล้วตรวจผลก่อนแจ้ง user

---

## 3. Rollback Procedures

### 3.1 Code Rollback (no schema change)

```bash
ssh prod-host
cd /opt/<project_slug>
git fetch --tags
git checkout <previous-tag>
docker compose up -d --build
curl -fsS https://<production-url>/health
```

Time: ~2-3 นาที (รวมเวลา build)

### 3.2 Code + Schema Rollback

```bash
ssh prod-host
cd /opt/<project_slug>

# Step 1: downgrade migration ก่อน (ระหว่าง code ใหม่ยังอ่าน schema ได้)
docker compose exec backend alembic downgrade -1

# Step 2: rollback code
git checkout <previous-tag>
docker compose up -d --build
```

Time: ~5 minutes

### 3.3 Full Restore from Backup

⚠️ **Data loss risk** — ใช้เฉพาะเมื่อ migration กู้ไม่ได้ · **ประสาน IT/DBA ก่อนเสมอ**
(DB อยู่บน centralized server — แต่ละ app มี database แยก แต่ใช้ server ร่วมกัน)

```bash
ssh prod-host
cd /opt/<project_slug>

# 1. หยุด app
docker compose stop backend frontend

# 2. Restore database ของ app นี้ (ชี้ centralized DB ตาม backend/.env)
set -a; . backend/.env; set +a
gunzip < /backups/<backup-file>.sql.gz | \
  docker run --rm -i -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME"

# 3. rollback code
git checkout <matching-tag>
docker compose up -d --build
```

Time: ขึ้นกับขนาด DB (~10 นาทีต่อ 10GB)

---

## 4. Monitoring & Alerts

### 4.1 Dashboards

- Backend metrics: Grafana / your monitoring stack
- Frontend errors: Sentry
- Logs: Loki/CloudWatch/Splunk (per project)

(Update with specific URLs per project deployment)

### 4.2 Alert Channels

| Severity | Channel | Response Time |
|---|---|---|
| P1 (down/data loss) | PagerDuty + phone | 15 min |
| P2 (degraded) | Slack #alerts | 1 hour |
| P3 (warning) | Email | next business day |

### 4.3 Health Endpoints

| Endpoint | Purpose | Expected |
|---|---|---|
| `/health` | Liveness | `200 {"status": "ok"}` |
| `/health/ready` | Readiness (DB check) | `200 {"status": "ready"}` |
| `/health/detailed` | Full health | `200 {"status": "ok"|"degraded", "checks": {...}}` |
| `/metrics` | Prometheus scrape | OpenMetrics format |

---

## 5. Common Incidents

### 5.1 High Error Rate (5xx > 1%)

**Symptoms:**
- Sentry shows spike in exceptions
- Alert P1 triggered

**Diagnose:**
```bash
# Tail logs
ssh prod-host
docker compose logs --tail=200 backend | grep -i error

# Check Sentry: group by exception, look for new errors after last deploy

# Recent deploy?
git log --oneline -10
```

**Action:**
1. If correlated with recent deploy → **rollback** (Section 3.1)
2. If not deploy-related → check dependencies (DB, AI API, Azure AD)
3. If issue is in dependency → engage dependency owner; consider degraded mode

---

### 5.2 Database Connection Errors

**Symptoms:**
- `/health/ready` returns 503
- Logs: `asyncpg.exceptions.PostgresError` or `OperationalError`

**Diagnose:**
```bash
ssh prod-host
docker compose exec db pg_isready -U $DB_USER

# Check connections
docker compose exec db psql -U $DB_USER -d $DB_NAME -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state='active';"

# Check disk
df -h
```

**Common causes & actions:**

| Cause | Action |
|---|---|
| Connection pool exhausted | Increase `DB_POOL_SIZE` + restart backend; investigate slow queries |
| Disk full | Free up space; archive old logs/backups |
| Postgres restarted | Verify HA failover; investigate why primary failed |
| Network partition | Check container networking; restart compose stack |
| Long-running query | `pg_cancel_backend(pid)` for culprit |

---

### 5.3 Azure AD Login Failing for All Users

**Symptoms:**
- `/auth/azure-ad/login` returning 401 for everyone

**Diagnose:**
```bash
# Logs
docker compose logs backend | grep -i "azure_ad\|jwt"

# Verify config
docker compose exec backend python -c \
  "from app.core.config import get_settings; s=get_settings(); print(s.AZURE_AD_TENANT_ID, s.AZURE_AD_CLIENT_ID)"

# Test JWKS endpoint
curl -s "https://login.microsoftonline.com/$TENANT_ID/discovery/v2.0/keys" | jq .
```

**Common causes & actions:**

| Cause | Action |
|---|---|
| Client secret expired | Generate new secret in Azure Portal → update `AZURE_AD_CLIENT_SECRET` in secret store → restart |
| Tenant ID typo | Verify against Azure Portal; fix env var |
| Azure AD outage | Check [status.azure.com](https://status.azure.com); wait |
| Token clock skew | NTP sync host clock |

---

### 5.4 Local Login Failing

**Symptoms:**
- `/auth/local/login` returning 401 for known-good credentials

**Diagnose:**
```bash
# Check JWT_SECRET_KEY is set
docker compose exec backend python -c \
  "from app.core.config import get_settings; print(bool(get_settings().JWT_SECRET_KEY))"

# Recent JWT_SECRET_KEY rotation?
# All existing tokens would be invalidated
```

**Action:**
- If recent rotation: communicate to users to re-login
- If not: check rate limit (Section 5.6) — failed attempts might have triggered lockout

---

### 5.5 High Latency (p95 > 2s)

**Symptoms:**
- Latency alert
- Users reporting slow page loads

**Diagnose:**
```bash
# Slow queries (Postgres)
docker compose exec db psql -U $DB_USER -d $DB_NAME -c "
  SELECT query, calls, mean_exec_time, max_exec_time
  FROM pg_stat_statements
  ORDER BY mean_exec_time DESC
  LIMIT 10;
"

# Check pool stats from /metrics
# Look at traces in Jaeger/Tempo for slow span
```

**Common causes & actions:**

| Cause | Action |
|---|---|
| Missing index | EXPLAIN ANALYZE the slow query; add index with CONCURRENTLY |
| N+1 queries | Find code path; use `selectinload` / batch fetch |
| External API slow | Add circuit breaker / shorter timeout |
| Resource constraint | Scale up replicas; increase CPU/memory limits |
| Bot/scraper traffic | Check rate limiter; consider CAPTCHA |

---

### 5.6 Rate Limit Issues

**Symptoms:**
- Users get 429 responses
- Logs show repeated rate limit hits

**Action:**
```bash
# Check current limits
grep -r "limiter.limit" backend/app/api/

# Temporary increase (edit env, restart)
RATE_LIMIT_PER_MINUTE=120
```

**Long-term:** investigate why limits are being hit — abuse, frontend retry storm, or genuine traffic growth?

---

### 5.7 Claude API Errors

**Symptoms:**
- AI-powered features failing
- Logs: `anthropic.APIError`

**Diagnose:**
```bash
docker compose logs backend | grep -i "claude\|anthropic"

# Check Anthropic status: https://status.anthropic.com
# Check API key validity, rate limits
```

**Action:**
- If rate-limited: add retry with backoff; reduce concurrent calls
- If API down: enable degraded mode (cached responses, fallback message)
- If quota exhausted: increase budget; verify nothing is in a tight loop

---

### 5.8 Disk Full

**Symptoms:**
- Backups failing
- Postgres errors

**Action:**
```bash
ssh prod-host
df -h

# Most common: old Docker images
docker system df
docker image prune -a --filter "until=24h"

# Old logs
find /var/lib/docker/containers -name "*.log" -size +100M

# Old backups
ls -lh /backups | head
# Archive to cold storage or delete oldest
```

**Long-term:** set log rotation, automated backup pruning

---

### 5.9 Suspected Security Incident

⚠️ Do **not** delete logs or evidence

**Action:** follow `docs/security.md` Section 12 incident response checklist

Quick containment:
```bash
# Rotate JWT_SECRET_KEY (all sessions invalidated immediately)
# 1. Generate new secret
openssl rand -hex 32
# 2. Update in secret manager
# 3. Restart backend
docker compose restart backend
```

To disable specific user:
```sql
UPDATE users SET is_active = false WHERE id = '<uuid>';
```

To block IP at proxy (nginx example):
```nginx
deny 1.2.3.4;
```

---

## 6. Routine Maintenance

### 6.1 Daily

- [ ] Check overnight alerts
- [ ] Review error rate dashboard
- [ ] Spot-check `/health/detailed`

### 6.2 Weekly

- [ ] Review Sentry top errors
- [ ] Review slow query log
- [ ] Check disk usage trends
- [ ] Review dependency update PRs (Dependabot)

### 6.3 Monthly

- [ ] Database vacuum analyze (or verify autovacuum is healthy)
- [ ] Rotate `JWT_SECRET_KEY` (graceful: requires user re-login)
- [ ] Review audit log volume — archive if needed
- [ ] Review user growth + capacity planning
- [ ] Test backup restoration in staging

### 6.4 Quarterly

- [ ] Rotate DB password
- [ ] Rotate Azure AD client secret
- [ ] Disaster recovery drill (restore from backup to staging)
- [ ] Review IAM/role assignments
- [ ] Review monitoring/alerting effectiveness

---

## 7. Backup & Restore

### 7.1 Automated Backups

ก่อน production deploy ที่มี schema change — ประสาน IT/DBA ให้ snapshot DB ก่อน (ดู §3)

Scheduled backup (cron on prod host):

```cron
0 2 * * * /opt/<slug>/scripts/backup.sh
```

`/opt/<slug>/scripts/backup.sh`:
```bash
#!/bin/bash
set -e
BACKUP_DIR=/backups
DATE=$(date +%Y%m%d-%H%M%S)
set -a; . /opt/<slug>/backend/.env; set +a   # โหลด DB_HOST/DB_USER/DB_NAME/DB_PASSWORD
docker run --rm -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  pg_dump -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" | gzip > "$BACKUP_DIR/daily-$DATE.sql.gz"

# Retention: 7 daily
find $BACKUP_DIR -name "daily-*.sql.gz" -mtime +7 -delete
```

### 7.2 Backup Verification

Test restore in staging monthly:

```bash
# On staging host
set -a; . backend/.env; set +a
gunzip < /backups/daily-latest.sql.gz | \
  docker run --rm -i -e PGPASSWORD="$DB_PASSWORD" postgres:16 \
  psql -h "$DB_HOST" -U "$DB_USER" -d "${DB_NAME}_test"
# Verify row counts, recent records
```

### 7.3 Off-site Backup

Copy daily backups to off-site storage (S3, Azure Blob, etc.) within 24 hours:

```bash
aws s3 cp /backups/daily-$DATE.sql.gz s3://<backup-bucket>/postgres/
```

---

## 8. Database Operations

### 8.1 Connect to Production DB

```bash
ssh prod-host
docker compose exec db psql -U $DB_USER -d $DB_NAME
```

⚠️ Read-only by default — use `BEGIN; ... ROLLBACK;` to test mutations

### 8.2 Common Queries

```sql
-- Active connections
SELECT count(*) FROM pg_stat_activity WHERE state='active';

-- Long-running queries (> 5 min)
SELECT pid, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state='active' AND (now() - query_start) > interval '5 minutes';

-- Kill a query
SELECT pg_cancel_backend(<pid>);

-- Table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname='public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 20;
```

### 8.3 Migration Failure During Deploy

```bash
docker compose exec backend alembic current
docker compose exec backend alembic history --verbose

# If migration partially applied:
#  - Check schema manually in psql
#  - Manually fix or downgrade
docker compose exec backend alembic downgrade <previous-revision>
```

---

## 9. Scaling

### 9.1 Horizontal (more replicas)

Backend is stateless — can scale freely.

```yaml
deploy:
  replicas: 4
```

Behind load balancer.

### 9.2 Vertical

Increase resource limits in `docker-compose.yml`:
```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'
      memory: 4G
```

### 9.3 Database

- **Read replicas** for read-heavy workloads (route reads via separate engine)
- **Connection pooling** with PgBouncer at scale
- **Partitioning** for very large tables

⚠️ Database scaling is **high-risk** — plan with DBA, test in staging

---

## 10. Contact / Escalation

(Update per project)

| Role | Person | Channel |
|---|---|---|
| Engineering on-call | (rotated) | PagerDuty |
| Backend tech lead | (name) | (channel) |
| DevOps | (name) | (channel) |
| Security officer | (name) | (channel) |
| Cloud provider support | (provider) | (portal) |

---

## 11. AI Maintenance Notes

When AI updates this file (e.g., new incident pattern discovered, new monitoring added):

1. Update the relevant section
2. Add to **changelog** below
3. Flag to user with diff for review

### Changelog

- `2026-05-20` — Initial version generated from web-app-standard template
