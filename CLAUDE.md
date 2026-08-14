# CLAUDE.md

> Entry point สำหรับ Claude / AI agents — ส่วน technical spec อยู่ใน [`AGENTS.md`](./AGENTS.md)

## Source of Truth

**[`AGENTS.md`](./AGENTS.md) v3.0 คือ source of truth** — อ่านตาม task tier ที่ระบุใน §0

ไฟล์นี้มีแค่ entry rules สั้นๆ — ห้ามใส่ technical spec ที่ซ้ำกับ AGENTS.md

## เริ่มจากตรงไหน

ทำตาม AGENTS.md §0 (Decision tree) — สรุปสั้น:

| Task tier | ตัวอย่าง | อ่าน |
|---|---|---|
| **Tiny** | typo, CSS, copy | AGENTS.md §1-§5 (Tier 1) |
| **Normal** | feature, API, component | + Tier 2 + `docs/<layer>.md` |
| **Risky** | auth, migration, deploy, security | + Tier 2 + `docs/security.md` + **confirm user** |

## กฎเหล็ก (สั้นที่สุด)

1. **Codebase pattern > Standard** — มี pattern เดิม → ทำตาม (AGENTS.md §1)
2. **ห้าม hardcode secrets** — env vars + `project.config`
3. **ห้ามเดา** — `rg`/`grep` หา pattern ใน repo ก่อนเขียน
4. **High-risk ops ต้อง confirm** — auth, migration, deploy, secret, CORS, integration (AGENTS.md §4)
5. **ภาษา:** Thai กับ user, English ใน code/comments/commit

## เมื่อ user สั่งงานใหม่

1. ระบุ task tier (Tiny / Normal / Risky)
2. อ่าน `AGENTS.md` ตาม tier
3. ใช้ `rg`/`grep` หา existing pattern (§1)
4. อ่าน `docs/<layer>.md` ของ layer ที่ touch (Normal+)
5. ตรวจ `project.config` → `MATURITY_LEVEL` เพื่อรู้ว่า Tier 4 ของไหน apply
6. เริ่มทำ — DoD checklist (AGENTS.md §8)

## High-Risk Operations (Confirm Required)

ดู AGENTS.md §4 — หลัก: action ที่ "ย้อนยาก หรือกระทบ shared system" ต้อง confirm ก่อนเสมอ

## Handover Docs

AI **เสนอ diff** สำหรับ `docs/human/*` — ไม่ silent update (AGENTS.md §9)

---

**Next:** อ่าน [`AGENTS.md`](./AGENTS.md)
