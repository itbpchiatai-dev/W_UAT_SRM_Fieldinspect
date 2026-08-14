# Retrospective — deviations from standard

> บันทึกทันทีเมื่อมี deviation จาก standard/established policy (ดู rules/task-tracking.md).
> เสนอ update global standard ตอน end-of-project retrospective ไม่ใช่ระหว่างทาง.

## 2026-06-18 — patches/ ที่ ship ได้ (carve-out จาก local-only policy)

**Deviation:** เดิม `.gitignore` ตั้ง `patches/` เป็น local-only ทั้ง dir โดยตั้งใจ
(comment: "throwaway, not shipped" — `git log -- patches/` ยืนยันไม่เคย commit;
`PROGRESS.md` ก็ระบุ "Local-only patches/ dir gitignored"). v3.1.0 เปลี่ยนเป็น
carve-out: ship เฉพาะ `patches/README.md` + `patches/v3_1_0_db_connections_patch.py`
(opt-in **module** patch) — throwaway one-off patches อื่น (เช่น v3_0_15) ยัง local-only.

**ทำไม:** handover ของ Database Connections (§4) ต้องการ patch ที่ส่งถึง existing
projects ให้ adopt โมดูลใหม่ได้ — ขัดกับ policy "patches/ ไม่ ship". module patch
ต่างจาก compat fix: ฟีเจอร์ใหม่ทั้งก้อนที่ scaffold template ใหม่มี แต่โปรเจกต์เก่าไม่มี
ทางได้นอกจาก patch (root cause เดียวกับ [[standard upgrade propagation]] ที่ยัง
unsolved — imperative scaffold merge ไม่ได้). User ตัดสินใจ ship (เลือก "ตาม handover").

**ผลกระทบ / ต้องเสนอที่ retro:** policy เรื่อง patches/ ควร formalize — แยก
"throwaway compat patch" (local-only) ออกจาก "module adoption patch" (shipped) ให้ชัด
ใน AGENTS.md + .gitignore convention. ตอนนี้ทำเป็น explicit `!exception` ต่อไฟล์ซึ่ง
ต้องเติมมือทุกครั้งที่มี module patch ใหม่ — อาจเปลี่ยนเป็น naming convention
(เช่น `patches/module_*.py` ship, ที่เหลือ ignore) ตอน formalize.

**Pending:** v3.1.0 ทั้งก้อนรอ SECURITY_APPROVER sign-off ก่อน release จริง (AGENTS.md §7).
