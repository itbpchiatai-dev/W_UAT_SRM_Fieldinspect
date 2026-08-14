# docs/deployment.md

> Deployment reference — Docker multi-stage builds + docker-compose
> ครอบคลุม: image build, environment variables, health checks, production hardening

---

## 1. Docker Strategy

ทุก project ใช้ **multi-stage Docker build** เพื่อ:
1. แยก build deps ออกจาก runtime image (image เล็ก)
2. ไม่มี source/test files ใน production image
3. Reproducible builds (pinned base images)

Image naming pattern: `${PROJECT_SLUG}-backend`, `${PROJECT_SLUG}-frontend`
(`PROJECT_SLUG` มาจาก `project.config` → inject ผ่าน `.env` → docker-compose interpolation)

---

## 2. Backend Dockerfile

**`backend/Dockerfile`:**

```dockerfile
# syntax=docker/dockerfile:1.7

# ─── Stage 1: Build ──────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --user --no-cache-dir .

# ─── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:$PATH"

# Install runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app -g 1000 \
    && useradd -r -u 1000 -g app -d /home/app -s /sbin/nologin app \
    && mkdir -p /home/app /app \
    && chown -R app:app /home/app /app

# Copy installed packages from builder
COPY --from=builder --chown=app:app /root/.local /home/app/.local

WORKDIR /app
COPY --chown=app:app . .

# Run as non-root
USER app

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 2.1 Key Practices

- **`python:3.12-slim-bookworm`** — small base image, security-supported
- **Pinned base** (no `latest`)
- **Non-root user** — runs as `app` (UID 1000)
- **`--user` install** in builder, copied to runtime
- **No build tools** in runtime image
- **Healthcheck** built-in

### 2.2 `.dockerignore`

**`backend/.dockerignore`:**

```
__pycache__
*.pyc
*.pyo
*.pyd
.Python
.env
.env.*
!.env.example
.venv
venv/
.git
.gitignore
.pytest_cache
.mypy_cache
.ruff_cache
.coverage
htmlcov/
tests/
docs/
*.md
.vscode
.idea
```

---

## 3. Frontend Dockerfile

**`frontend/Dockerfile`:**

```dockerfile
# syntax=docker/dockerfile:1.7

# ─── Stage 1: Build ──────────────────────────────────────────
FROM node:20-bookworm-slim AS builder

WORKDIR /build

# Install deps (cached layer)
COPY package*.json ./
RUN npm ci

# Build
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_AZURE_AD_TENANT_ID
ARG VITE_AZURE_AD_CLIENT_ID
ARG VITE_AZURE_AD_REDIRECT_URI
ARG VITE_DEFAULT_LANGUAGE=th

ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_AZURE_AD_TENANT_ID=$VITE_AZURE_AD_TENANT_ID \
    VITE_AZURE_AD_CLIENT_ID=$VITE_AZURE_AD_CLIENT_ID \
    VITE_AZURE_AD_REDIRECT_URI=$VITE_AZURE_AD_REDIRECT_URI \
    VITE_DEFAULT_LANGUAGE=$VITE_DEFAULT_LANGUAGE

RUN npm run build

# ─── Stage 2: Runtime (nginx) ────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# Non-root nginx
RUN adduser -D -u 1000 -g 'app' app \
    && sed -i 's/user  nginx;/user  app;/' /etc/nginx/nginx.conf \
    && touch /var/run/nginx.pid \
    && chown -R app:app /var/run/nginx.pid /var/cache/nginx /etc/nginx /usr/share/nginx/html

COPY --chown=app:app nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=app:app /build/dist /usr/share/nginx/html

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

### 3.1 nginx Config

**`frontend/nginx.conf`:**

```nginx
server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # Security headers (CSP/HSTS set by API gateway in production, but defaults here)
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Static assets caching
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # Health
    location /health {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript;
}
```

### 3.2 `.dockerignore`

**`frontend/.dockerignore`:**

```
node_modules
dist
.env
.env.*
!.env.example
.git
.vscode
.idea
coverage
*.log
README.md
```

---

## 4. docker-compose — Local Development

Local dev รัน **DB + backend ใน Docker** (Compose project เดียว `srm_fieldinspect`) — **frontend รันบน Windows host** (Vite hot reload, :5173)

### Local Dev Quick Reference (source of truth)

| Task | Command |
|---|---|
| Start | `start-service.bat` |
| Status | `status-service.bat` |
| Restart | `restart-service.bat` |
| Stop | `stop-service.bat` |

- Frontend `http://localhost:5173` · Backend `http://localhost:8000` · API docs `http://localhost:8000/docs`
- **Database safety:** volume `srm-fieldinspect-db-data` — **ห้ามรัน `docker compose down -v`** และห้ามลบ volume นี้; ถ้า volume หายให้ **restore จาก backup** ห้ามสร้าง DB ว่างแทน

Local dev ใช้ **root `docker-compose.yml` + `docker-compose.dev.yml` overlay**
รวมเป็น Compose project เดียว `srm_fieldinspect`:

- overlay เพิ่ม service `db` (pgvector/pgvector:pg16, external volume
  `srm-fieldinspect-db-data`, bind `127.0.0.1:5432`)
- overlay override `backend`: publish `127.0.0.1:8000`, `DB_HOST=db`,
  bind mount `./backend:/app`, `uvicorn --reload` (1 worker), depends_on db health
- **frontend รันบน Windows host** (Vite :5173) — ไม่อยู่ใน Docker สำหรับ local dev

> ไฟล์ `docker/docker-compose.yml` เดิม (DB-only, project `srm-fieldinspect-dev`)
> ถูก **retire** แล้ว — ดู deprecation note ในไฟล์นั้น. `docker/init-db.sql`
> ยังใช้อยู่ (overlay อ้างถึง)

**`docker/init-db.sql`** — รันครั้งแรกตอน Postgres initdb:

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";   -- pgvector (AI embeddings)
```

### 4.1 Commands

```bash
# แนะนำ: เปิด DB + Backend (Docker) + Frontend (Windows) ด้วยคำสั่งเดียว
start-service.bat

# เทียบเท่า canonical command (DB + backend ใน Compose project เดียว):
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.dev.yml up -d db backend

# Frontend รันบน Windows host แยก (start-service.bat จัดการให้แล้ว)
cd frontend && npm run dev

# Migration (รันใน backend container)
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.dev.yml exec backend alembic upgrade head

# ปิด DB + Backend + Frontend (ไม่ลบ container/volume)
stop-service.bat

# psql shell เข้า dev DB
docker exec -it srm-fieldinspect-db psql -U srm_fieldinspect -d srm_fieldinspect
```

`start-service.bat` **ไม่** สร้าง volume ให้อัตโนมัติแล้ว — ถ้า
`srm-fieldinspect-db-data` หาย script จะ **หยุดและแจ้งให้ restore จาก backup**
(กัน DB ว่างเปล่าเริ่มขึ้นเงียบ ๆ). Compose ประกาศ volume นี้เป็น `external`
เพื่อไม่ให้ `docker compose down -v` หรือการเปลี่ยนชื่อ project ลบข้อมูล local

---

## 4.2 Local Production-like (round 8-16A)

รันแบบใกล้ production บนเครื่อง local: **assets ที่ build แล้ว** เสิร์ฟด้วย nginx,
backend **ไม่มี source bind mount และไม่มี `--reload`**, ทั้งคู่อยู่หลัง **URL เดียว
same-origin** — ใช้ Compose project เดิม `srm_fieldinspect` และ service เดิม
(ไม่มีการสร้าง project/container ชุดที่สอง)

| | Dev (`docker-compose.dev.yml`) | Production-like (`docker-compose.prodlike.yml`) |
|---|---|---|
| UI | `http://localhost:5173` (Vite บน Windows host) | **`http://localhost:8080`** (nginx ใน Docker) |
| Frontend | ไม่อยู่ใน Docker | `srm-fieldinspect-frontend` (built assets) |
| Backend cmd | `uvicorn --reload` (1 worker) | `uvicorn --workers 4` (จาก Dockerfile CMD) |
| Backend source | bind mount `./backend:/app` | **ไม่มี** — ใช้ code ใน image |
| API | cross-origin ไป `:8000` (CORS) | **same-origin** `/api/*` → nginx proxy → `backend:8000` |
| รูปตรวจแปลง | bind mount `backend/var/inspection-photos` (host) | **volume `srm-fieldinspect-media`** |

**สำคัญ:** prodlike overlay **ไม่ include** `docker-compose.dev.yml` — ตัว dev overlay
คือที่มาของ bind mount กับ `--reload` ดังนั้นแค่ไม่ใส่ ก็ได้ backend แบบ production
โดยไม่ต้อง override อะไรกลับ

### คำสั่ง canonical

> **ต้องมี `--no-deps` เสมอ** (round 8-16B) — `backend` มี `depends_on: db`
> ถ้าไม่ใส่ Compose จะเดินเข้าไปหา service `db` ทุกครั้งที่ up/rebuild
> ตอนนี้ยังปลอดภัยเพราะ db block ใน `prodlike` เหมือน `dev` ทุกตัวอักษร แต่ความ
> ปลอดภัยนั้นอาศัยการดูแลไฟล์ 2 ชุดให้ตรงกันด้วยมือ — วันไหน drift ไปแม้แต่นิดเดียว
> `up -d backend frontend` ธรรมดาจะ **recreate DB container** ทันที
> `--no-deps` ตัดการ traverse ทิ้งทั้งหมด (ข้อมูลไม่หายอยู่แล้วเพราะ `db_data`
> เป็น external volume แต่ container ถูก recreate กลางงานไม่ใช่เรื่องที่ควรฝากไว้กับโชค)
> **DB lifecycle เป็นของ dev overlay / `start-service.bat`** — เปิด db จากที่นั่นก่อน

```bash
# Start (db ไม่ถูกแตะและไม่ถูก traverse)
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.prodlike.yml \
    up -d --no-deps backend frontend

# Status
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.prodlike.yml ps

# Rebuild หลังแก้ code/nginx.conf
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.prodlike.yml \
    up -d --build --no-deps backend frontend

# Stop (ไม่ลบ container/volume)
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.prodlike.yml stop backend frontend
```

### Rollback กลับ dev

```bash
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.dev.yml up -d backend
docker compose --env-file backend/.env -p srm_fieldinspect \
    -f docker-compose.yml -f docker-compose.prodlike.yml stop frontend
```

ไม่ต้อง revert ไฟล์ใดๆ: dev backend ใช้ bind mount ซึ่งบัง `/app` ทั้งหมด และ
`nginx.conf`/`frontend/Dockerfile` ถูกใช้เฉพาะ frontend container ที่ dev ไม่ได้ใช้
→ การแก้ไฟล์ของ prodlike **ไม่มีผลกับ dev workflow** (ยืนยันด้วยการทดสอบ
rollback ไป-กลับจริงในรอบ 8-16A)

### Media volume — `srm-fieldinspect-media`

**round 8-16B: ประกาศอยู่ใน root `docker-compose.yml` แล้ว** — production, dev
และ prodlike จึงอ้าง contract เดียวกัน ไม่มีการประกาศซ้ำให้ drift

- mount ที่ `/app/var/inspection-photos` (ตรงกับ `INSPECTION_PHOTOS_DIR` default)
- ประกาศ `external: true` แบบเดียวกับ `proxy-net` →
  **`docker compose down -v` ลบไม่ได้** และการเปลี่ยนชื่อ project ก็ลบไม่ได้
- สร้างครั้งเดียวต่อ host: `docker volume create srm-fieldinspect-media`
  (ถ้ายังไม่มี `docker compose up` จะ error ทันที ไม่สร้างให้เงียบ ๆ)
- `backend/Dockerfile` pre-create directory นี้เป็นของ `app:app` (uid/gid 1000)
  → volume ใหม่สืบทอด ownership อัตโนมัติ **ไม่ต้อง chmod 777 และ backend
  ไม่ต้องรันเป็น root**
- `backend/.dockerignore` มี `var/` — รูป**ไม่ถูก bake เข้า image** อีกต่อไป

| Environment | รูปเก็บที่ไหน |
|---|---|
| Production (root only) | volume `srm-fieldinspect-media` |
| Prodlike (root + prodlike) | volume `srm-fieldinspect-media` (เดียวกัน) |
| **Dev (root + dev)** | **bind mount `backend/var/inspection-photos` บน Windows host** |

dev overlay ประกาศ bind mount ทับ target เดียวกันโดยตั้งใจ — Compose merge
service volumes **ตาม target** ดังนั้น declaration ของ dev แทนที่ named volume
ของ root ทำให้ dev ยังเขียนรูปลงโฟลเดอร์ที่เปิดดูจาก Windows ได้เหมือนเดิม
(ถ้าไม่มีบรรทัดนั้น named volume จะชนะ `/app` bind สำหรับ subpath นี้ และที่เก็บรูป
ของ dev จะเปลี่ยนแบบเงียบ ๆ)

> **ห้าม `docker compose down -v` และห้าม `docker system prune`** — ทั้ง
> `srm-fieldinspect-db-data` และ `srm-fieldinspect-media` เป็น external volume
> ที่ป้องกัน `down -v` ไว้แล้ว แต่ `prune` ยังลบได้

### `/media/*` ไม่ใช่ static route

รูปเก็บ URL prefix `/media/inspection-photos/...` ไว้ใน DB แต่**ไม่มี route ใด
เสิร์ฟจาก filesystem** — สิทธิ์ดูรูปขึ้นกับ Record ที่เป็นเจ้าของ จึงอ่านได้ทาง
`GET /api/v1/records/{id}/photos/{photo_id}` (ต้อง auth) เท่านั้น
`nginx.conf` ตอบ **404** ให้ `/media/*` โดยเจตนา เพื่อไม่ให้ตกลง SPA fallback
แล้วคืน `200 + index.html` (ซึ่งดูเหมือน URL รูปใช้งานได้ทั้งที่ไม่มีใคร authorize)

### ยังไม่ใช่ Production จริง

- **`APP_ENV` ยังเป็น `dev`** — ตั้ง `production` ไม่ได้เพราะ config fail-fast เมื่อ
  `RATE_LIMIT_STORAGE_URI` ยังเป็น `memory://` (ต้องมี shared storage ก่อน)
- **HTTP ธรรมดาบน localhost** — production จริง terminate TLS ที่ reverse proxy ของ IT
  นี่คือความเหมือนด้าน **topology** ไม่ใช่ด้าน transport
- **`TRUSTED_PROXY_IPS` ยังว่าง** — เมื่อผ่าน proxy ทุก request จะเห็น IP เป็นของ
  nginx container ตัวเดียว ทำให้ rate limit นับรวมกัน (ดู `docs/security.md`)

---

## 5. docker-compose — Production

Production รัน backend + frontend ใน Docker — DB อยู่บน centralized server (ดู §6)

**`docker-compose.yml`** อยู่ที่ **root** ของ project:

- build จาก source บน host (`docker compose up -d --build`) — ไม่ pull image
- network: `proxy-net` (`external: true`) — **ข้อบังคับ**; IT สร้างก่อน deploy: `docker network create proxy-net`
- volume: `srm-fieldinspect-media` (`external: true`) — **ข้อบังคับ** (round 8-16B);
  สร้างก่อน deploy: `docker volume create srm-fieldinspect-media`
- env: `backend/.env` (คัดลอกจาก `backend/.env.prod.example` แล้วแก้ค่าจริง)
- ไม่มี service `db` — production ชี้ไป centralized DB server

#### 5.0-pre ข้อจำกัดที่ต้องแก้ก่อนขึ้น Production จริง (round 8-16D)

**1. DB connection budget เกิน default ของ Postgres**

`lifespan` รันต่อ worker → image รัน `--workers 4` จึงได้ **4 pool อิสระ**:

```
ต่อ worker : DB_POOL_SIZE(10) + DB_MAX_OVERFLOW(20) = 30
4 workers  : 4 × 30                                 = 120  ← theoretical ceiling
Postgres   : max_connections=100 − superuser_reserved(3) ≈ 97 usable
                                                      120 > 97  ❌
```
บวก alembic ตอน deploy (sync psycopg, สั้น ๆ) ภายใต้โหลดจริงจะเจอ
`FATAL: sorry, too many clients already` **ต้องได้ค่า `max_connections` จริง
จาก Infra ก่อน** แล้วเลือกทางใดทางหนึ่ง: ขยาย `max_connections`, ลด
`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`, ลดจำนวน worker, หรือใส่ PgBouncer
— **ห้ามเดาค่า** เพราะขึ้นกับว่ามีแอปอื่นแชร์ DB server หรือไม่

**2. Scheduler รันซ้ำทุก worker**

`start_scheduler()` อยู่ใน `lifespan` → cron 3 ตัว (`registry_telemetry` รายวัน,
`log_partitions` รายเดือน, `log_retention` รายวัน) **ทำงาน 4 ครั้งพร้อมกัน**
= push telemetry ซ้ำ 4 รอบ และลบ log retention ซ้อนกัน 4 process
ต้องแก้ให้มี leader เดียว (เช่น advisory lock หรือแยก worker process)
— ยังไม่แก้ในรอบ 8-16D เพราะเป็น business logic

**3. RLS ไม่ถูกบังคับถ้าไม่ตั้ง `DB_APP_USER`**

`DB_APP_USER` ว่าง → แอปต่อ DB ด้วย role **เจ้าของตาราง** มี 5 ตารางที่ใช้
`FORCE ROW LEVEL SECURITY` จึงยังปลอดภัย แต่ policy ที่เหลือถูก bypass
ต้อง provision non-owner role บน production DB แล้วตั้ง
`DB_APP_USER`/`DB_APP_PASSWORD` (ยังไม่ทำเป็น boot gate เพราะไม่รู้ว่า role
นั้นมีอยู่จริงบน DB ปลายทางหรือไม่)

**4. Media volume เป็น single-host**

`srm-fieldinspect-media` เป็น `driver=local, scope=local` — แชร์ได้เฉพาะ
container **บน host เดียวกัน** ถ้า scale ข้าม host แต่ละ host จะได้ volume
ของตัวเอง รูปที่ upload ผ่าน replica A จะมองไม่เห็นจาก replica B
(**~50% ของรูปเสียถ้ามี 2 replica**) ถ้าจะ scale ต้องย้ายไป shared storage
(NFS/S3-compatible) ก่อน — ปัจจุบัน compose ยังไม่มี `replicas:`

#### 5.0 สองสัญญาที่เพิ่มใน round 8-16B

**1. Media persistence** — เดิม production ไม่มี volume ใดๆ เลย รูปตรวจแปลงจึงตกอยู่
ใน writable layer ของ container และ**หายทุกครั้งที่ recreate/rebuild** ขณะที่แถวใน DB
ยังชี้ไปหาไฟล์เดิม → record เสียรูปแบบเงียบ ๆ ตอนนี้ backend mount
`srm-fieldinspect-media` ที่ `/app/var/inspection-photos` แล้ว

**2. Frontend build args** — เดิม root compose ไม่ส่ง `VITE_*` เข้า build เลย
image production จึงถูก build โดยที่ทุกค่าว่าง (ไม่มีชื่อแอป ไม่มี auth scope
ไม่มี Azure AD client id) ตอนนี้ส่งครบ 8 ตัว รับค่าจาก environment ของ deploy host:

| Build arg | Default ถ้าไม่ตั้ง | หมายเหตุ |
|---|---|---|
| `VITE_APP_NAME` | `Chia Tai` | |
| `VITE_API_BASE_URL` | *(ว่าง)* | ว่าง = **same-origin** ผ่าน reverse proxy |
| `VITE_PUBLIC_APP_URL` | *(ว่าง)* | ว่าง = QR ใช้ origin ของ browser เอง |
| `VITE_AUTH_SCOPE` | `both` | ต้องตรงกับ `AUTH_SCOPE` ฝั่ง backend |
| `VITE_AZURE_AD_TENANT_ID` | *(ว่าง)* | |
| `VITE_AZURE_AD_CLIENT_ID` | *(ว่าง)* | |
| `VITE_AZURE_AD_REDIRECT_URI` | *(ว่าง)* | ตั้งเป็น URL production จริงตอน deploy |
| `VITE_DEFAULT_LANGUAGE` | `th` | |

> ⚠️ **ห้ามใส่ secret ใน build args** — Vite inline ค่าเหล่านี้ลง bundle ที่ผู้ใช้ทุกคน
> เปิดอ่านได้ ห้ามใส่ `AZURE_AD_CLIENT_SECRET`, `JWT_SECRET_KEY`, DB password,
> pepper หรือ API key เด็ดขาด
> **ห้าม hardcode production domain ในไฟล์ compose** — รับจาก environment เท่านั้น

### 5.1 Production Deploy

**ครั้งแรก** (บน production host):

```bash
git clone <repo-url> /opt/<slug> && cd /opt/<slug>
cp backend/.env.prod.example backend/.env    # แล้วแก้ค่า production
docker network create proxy-net              # ถ้ายังไม่มี
./deploy.sh
```

**Deploy รอบถัดไป:** `./deploy.sh`

`deploy.sh` (hardened) ทำตามลำดับ: **backup DB** → `git pull` → `docker compose build` → `up -d` → `alembic upgrade head` → **รอ `/health/ready`** — ถ้า migrate ล้มเหลวหรือ health ไม่เขียวภายใน retry ที่กำหนด จะ **rollback กลับ commit เดิมอัตโนมัติ** (override ได้ผ่าน env `HEALTH_URL` / `HEALTH_RETRIES` / `BACKUP_DIR`). เขียน migration แบบ backward-compatible เสมอเพื่อให้ container เก่ายังทำงานได้ระหว่าง migrate (ดู [`docs/database.md`](database.md) §6.4)

---

## 6. Centralized Database (Production)

### 6.1 ความแตกต่างจาก Local

| | Local Dev | Production |
|---|---|---|
| DB อยู่ที่ | Docker container บนเครื่อง | Central DB server (shared) |
| `DB_HOST` | `db` (docker service name) | hostname หรือ IP ของ DB server |
| Migration | `cd backend && alembic upgrade head` (backend รันบน host) | `docker compose exec backend alembic upgrade head` |
| Backup | `docker compose down -v` ลบได้เลย | ต้องมี backup strategy จริง (centralized DB) |

### 6.2 Production backend/.env (Centralized DB)

ไฟล์ `backend/.env` บน production host (gitignored, `chmod 600`):

```bash
# Database — ชี้ไป centralized DB server
DB_HOST=<central-db-hostname-or-ip>   # ← ไม่ใช่ "db" อีกต่อไป
DB_PORT=5432
DB_NAME=<project_slug>
DB_USER=<project_slug>
DB_PASSWORD=<strong-password-from-vault>
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# ค่าอื่นๆ เหมือน local .env
APP_NAME=<project_slug>
APP_ENV=production
APP_DEBUG=false
...
```

> ⚠️ ห้ามใช้ `DB_HOST=db` ใน production — `db` คือ docker service name ที่ใช้ได้เฉพาะใน local docker network เท่านั้น

### 6.3 Migration บน Centralized DB

`deploy.sh` รัน migration ให้อัตโนมัติหลัง stack ขึ้น —
backend container เชื่อม centralized DB ตาม `DB_HOST` ใน `backend/.env`

รัน migration แยกเองได้:
```bash
docker compose exec backend alembic upgrade head
```

> ⚠️ extensions (`uuid-ossp`, `pgcrypto`, `pg_trgm`, `vector`) ต้องให้ DBA สร้างก่อน
> (ดู §6.4) — app user มักไม่มีสิทธิ์ `CREATE EXTENSION`

### 6.4 Centralized DB Setup (One-Time, per Project)

ให้ DBA หรือ sysadmin ทำ 1 ครั้งก่อน first deploy:

```sql
-- บน centralized DB server (รัน as postgres superuser)
CREATE USER <project_slug> WITH PASSWORD '<strong-password>';
CREATE DATABASE <project_slug> OWNER <project_slug>;

-- Connect ไป DB ใหม่แล้วเพิ่ม extensions
\c <project_slug>
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
-- pgvector: uncomment ถ้าต้องการ AI embeddings (ต้องใช้ image pgvector/pgvector:pg16)
-- CREATE EXTENSION IF NOT EXISTS "vector";

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE <project_slug> TO <project_slug>;
```

> ⚠️ ถ้า centralized DB ไม่มี superuser access ให้ app user — ต้องให้ DBA รัน `CREATE EXTENSION` ก่อน แล้วค่อย migrate

---

## 7. Environment Variables

### 7.1 Where Each Value Lives

| Variable | Local Dev | Staging/Production |
|---|---|---|
| `DB_PASSWORD` | `backend/.env` (gitignored) | `backend/.env` บน host (gitignored, `chmod 600`) |
| `JWT_SECRET_KEY` | `backend/.env` | Secret manager |
| `AZURE_AD_CLIENT_SECRET` | `backend/.env` | Secret manager |
| `CLAUDE_API_KEY` | `backend/.env` | Secret manager |
| `API_CORS_ORIGINS` | `backend/.env` | Per-environment config |

### 7.2 Loading at Runtime

ทั้ง local และ production โหลด env ผ่าน `env_file: ./backend/.env` ใน compose

- **Local dev:** `backend/.env` generated โดย setup
- **Production:** `backend/.env` บน host — copy จาก `backend/.env.prod.example`,
  แก้ค่าจริง, ตั้ง `chmod 600` (`backend/.env` ถูก gitignore — ไม่หลุดเข้า repo)

---

## 8. Health Checks

### 7.1 Backend

```python
@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — process is running."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness(db: DbDep) -> dict[str, str]:
    """Readiness probe — dependencies are reachable."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database not ready")
    return {"status": "ready"}
```

### 7.2 Distinction

| Probe | Purpose | Action on Failure |
|---|---|---|
| `/health` | Container alive | Restart container |
| `/health/ready` | Container can serve | Remove from load balancer (don't restart) |

---

## 9. Logging in Containers

- ✅ Log to **stdout/stderr** (JSON via `structlog`)
- ❌ Don't log to files inside container
- Aggregation: Docker logging driver → external log system (CloudWatch, Loki, Splunk)

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

For production, ใช้ `gelf`, `fluentd`, or cloud driver

---

## 10. Image Tagging Strategy

```
${PROJECT_SLUG}-backend:latest         # ❌ never use in production
${PROJECT_SLUG}-backend:v1.2.3         # ✅ semver tag
${PROJECT_SLUG}-backend:abc1234        # ✅ git short SHA
${PROJECT_SLUG}-backend:v1.2.3-abc1234 # ✅ combined
```

Rules:
- Production deploys ใช้ semver tag หรือ git SHA เสมอ
- `latest` ใช้ได้ใน local dev
- Tag immutable หลัง push (registry retention policy)

---

## 11. Resource Limits (Production)

```yaml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 2G
    reservations:
      cpus: '0.5'
      memory: 512M
```

**Starting points** (adjust per load testing):

| Service | CPU limit | Memory limit |
|---|---|---|
| Backend (FastAPI) | 2.0 | 2 GB |
| Frontend (nginx) | 0.5 | 256 MB |
| PostgreSQL | 4.0 | 4 GB |

---

## 12. Production Hardening Checklist

- [ ] Multi-stage build ลด attack surface
- [ ] Non-root user ใน Dockerfile
- [ ] Pinned base images (no `latest`)
- [ ] `.dockerignore` exclude `.env`, `.git`, tests
- [ ] Healthchecks defined (both `/health` and `/health/ready`)
- [ ] Resource limits (`cpus`, `memory`)
- [ ] Log rotation configured
- [ ] Read-only filesystem where possible: `read_only: true` + named volumes สำหรับ `/tmp`
- [ ] Drop capabilities: `cap_drop: [ALL]` + add only needed
- [ ] No `privileged: true` ใน production
- [ ] Secrets ไม่อยู่ใน image layers (check with `docker history`)
- [ ] Image scanned (Trivy/Snyk) ใน CI
- [ ] TLS termination ที่ load balancer (เช่น Traefik, nginx-proxy) — backend ไม่ต้อง expose HTTPS

---

## 13. Quick Reference

| Task | Command |
|---|---|
| Start local services (Windows) | `start-service.bat` |
| Start DB + backend (Docker) | `docker compose --env-file backend/.env -p srm_fieldinspect -f docker-compose.yml -f docker-compose.dev.yml up -d db backend` |
| Run migrations (local) | `docker compose --env-file backend/.env -p srm_fieldinspect -f docker-compose.yml -f docker-compose.dev.yml exec backend alembic upgrade head` |
| Seed data (local) | `docker compose --env-file backend/.env -p srm_fieldinspect -f docker-compose.yml -f docker-compose.dev.yml exec backend python -m app.db.seed` |
| psql shell (local DB) | `docker exec -it srm-fieldinspect-db psql -U srm_fieldinspect -d srm_fieldinspect` |
| Production deploy | `./deploy.sh` |
| Run migrations (prod) | `docker compose exec backend alembic upgrade head` |
| Tail backend logs (prod) | `docker compose logs -f backend` |
| Shell in backend (prod) | `docker compose exec backend bash` |
| Rebuild after deps change (prod) | `docker compose up -d --build` |
