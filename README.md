# Srm Fieldinspect

## Quick Start

```bash
python scripts/setup.py
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

## Keys ที่ต้องเพิ่มทีหลัง (backend/.env)

```bash
CLAUDE_API_KEY=        # จาก https://console.anthropic.com/
AZURE_AD_CLIENT_SECRET= # จาก Azure Portal (ถ้าใช้ internal users)
```

## Local Development

รัน runtime ทั้งหมดด้วยคำสั่งเดียว (Windows) — **backend + DB อยู่ใน Docker** (Compose project เดียว `srm_fieldinspect`), **frontend รันบน Windows host**:

| Task | Command |
|---|---|
| Start | `start-service.bat` |
| Status | `status-service.bat` |
| Restart | `restart-service.bat` |
| Stop | `stop-service.bat` |

- Frontend: http://localhost:5173 · Backend: http://localhost:8000 · API docs: http://localhost:8000/docs
- **Database safety:** volume `srm-fieldinspect-db-data` — **ห้าม** `docker compose down -v` และห้ามลบ volume นี้ (ถ้าหายให้ restore จาก backup — ห้ามสร้าง DB ว่างแทน)

ดู [`docs/deployment.md`](docs/deployment.md) §4 เป็น source of truth ของ local runtime

## Documentation

- [AGENTS.md](AGENTS.md) — technical standard
- [docs/human/onboarding.md](docs/human/onboarding.md) — developer setup
