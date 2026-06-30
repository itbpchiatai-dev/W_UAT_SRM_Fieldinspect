# FarmLog — Build Roadmap (on SRM_FieldInspect scaffold)

> Source of truth ของ "ตัวงาน": `FarmLog_Design_Spec.md` (31 sections + Appendix A/B) และ
> `FarmLog_Build_Order.md` (เฟส A–F, Step 0–21). ไฟล์นี้คือ progress tracker ที่ commit ไว้ใน repo
> เพื่อไม่ให้แผนหายไปตาม session.

## กฎเหล็กของการ build (ห้ามฝ่าฝืน)
1. ทำ **ทีละ Step** — "ทำแค่ Step นี้ ห้ามเกิน" → test "เสร็จเมื่อ" → commit → ค่อยไปต่อ
2. **ห้ามกระโดด** ไปทำ dynamic (เฟส E)/RLS (Step 8)/offline (Step 16) ก่อนกำหนด — ต้นเหตุที่รื้อแก้เยอะ
3. **Codebase pattern > Spec** — reuse scaffold (auth/RBAC/menu/CRUD) ให้มากที่สุด
4. ภาษา: ไทยกับ user · English ใน code/comment/commit
5. ก่อนเขียนทุก Step: `rg`/`grep` หา pattern เดิม + อ่าน `docs/<layer>.md` ของ layer ที่แตะ

## ลำดับ Step (เฟส A–F)
| เฟส | Step | งาน | สถานะ |
|---|---|---|---|
| A พื้นฐาน | 0 | Scaffold (docker/api/web, /health, vite proxy) | ✅ (scaffold) |
| | 1 | DB + migration baseline (users, suppliers) | ✅ |
| | 2 | Auth (login→JWT, /me, guard, logout, seed admin) | ✅ (scaffold) |
| B CRUD fixed | 3 | Suppliers CRUD | ✅ migration 0012 |
| | 4 | Users ผูก supplier + user_type | ✅ migration 0013 |
| | 5 | Plots CRUD + plot_assignments | ✅ migration 0014 |
| | 6 | Records (FIXED columns + custom_fields jsonb เผื่อไว้) | ✅ migration 0015 |
| C ความปลอดภัย | 7 | RBAC เต็ม (permission matrix §4.2) | ✅ |
| | 8 | Postgres RLS + isolation test | ✅ migration 0016_rls |
| D UX | 9 | Dashboard (KPI + crop group) | ✅ |
| | 10 | รายการบันทึก + สรุป + One Page Preview | ✅ |
| | 11 | ฟอร์มกรอกแปลง (popup/มือถือ) + Smart Plot Picker + GPS + photo | ✅ migration 0017 / commit step-11 |
| E dynamic | **12** | **ฟอร์ม schema-driven: field_definitions + field registry + refactor + custom field** | 🔬 implemented (migration 0018, รอ UI acceptance) |
| E dynamic | **12.5** | **Master Data + Yield + reshape record เป็น list-driven + ฟอร์มหน้างาน "ปุ่มกด/สไลเดอร์"** (migration 0019, 0020) | 🔬 implemented (รอ UI acceptance) |
| | 13 | เลือกคอลัมน์ + view_profiles + field-key resolver | ⬜ |
| F เสริม | 14 | สถานะ+อนุมัติ (draft→submitted→approved) + audit | ⬜ |
| | 15 | Upload รูปจริง (volume/MinIO) + thumbnail | ⬜ |
| | 16 | Offline + sync (idempotent ด้วย record_id UUID) | ⬜ |
| | 17 | Excel import/export (คอลัมน์ dynamic) | ⬜ |
| | 18 | Map + "แปลงใกล้ฉัน" (PostGIS) + mini-map | ⬜ |
| | 19 | QR / พ.ศ. / แจ้งเตือน / Reports | ⬜ |
| | 20 | API Key + webhook + OpenAPI docs | ⬜ |
| | 21 | Performance (pagination/virtualization) + backup cron | ⬜ |

## Step 12 build notes (scope decision)
- **Core fields kept their bespoke Step-11 rendering** (Smart Plot Picker / GPS / photo /
  conditional pest+disease) — NOT force-refactored into the registry, to avoid regressing
  working UX and the "ฟอร์มเหมือนเดิม" acceptance. Core 20 are seeded into `field_definitions`
  as `is_core=True` (catalogued + manageable in Field Master: label/required/order/active;
  key/type immutable).
- **The field registry + `DynamicFieldRenderer`** (frontend `components/farmlog/fieldRegistry.tsx`,
  type→renderer/validator) drives the new **"ฟิลด์เพิ่มเติม"** section in RecordForm, wiring
  values into `records.custom_fields` JSONB. Admin adds custom fields via **Field Master**
  (`/farmlog/admin/fields`, super_admin via `fielddefs.*`).
- Manual UI acceptance (do this to close Step 12): login super_admin → Field Master →
  เพิ่ม custom field type=number (เช่น key `soil_ph`) → เปิดฟอร์มบันทึก → กรอก → validate/save →
  ดูค่าใน custom_fields. Then commit + tick Step 12 done.

## Step 12.5 build notes (Master Data + Yield + list-driven reshape)
- **Master Data** (`master_data` table, migration 0019): admin-editable dropdown source. API
  `/api/v1/masterdata` (GET=records.read, mutate=`masterdata.*` super_admin/supervisor), หน้า
  `/farmlog/admin/masterdata`. Seed: crop/variety(parent)/growth_stage/weather/level/severity/
  irrigation/fertilizer.
- **records reshape** (migration 0020): pivot จาก pest/disease-detail → list-driven + Yield.
  `crop_type`→`crop` (เก็บ data); drop area_rai/plant_height/pest_*/disease_*/weed_severity/
  fertilizer_used/amount; add variety/planting_date/**yield_pct (0–150 default 100)**/
  field_prep_level/care_level/pest_status/disease_status/weed_status/fertilizer.
- **Form** = list-driven: ฟิลด์ส่วนใหญ่เป็น `MasterDataSelect` (โหลด options จาก master_data),
  **Yield = slider 0–150 default 100**, คงเฉพาะ recommendation/notes เป็น text. คง Smart Plot
  Picker/GPS/photo/custom-fields. `field_definitions` core re-aligned (list→`masterdata:<type>`,
  yield_pct→`percent`); stale core keys pruned.
- Dashboard repo remap: pest/disease "found" = status ≠ ('ไม่พบ'/null); crop grouping ← `crop`
  (schema/frontend unchanged).
- **Tap-UI (on-site):** ฟอร์มกรอกหน้างานเป็น **ปุ่มกด** ทั้งหมด (`OptionButtons` +
  `MasterDataButtons`) แทน dropdown — list fields ทุกตัว (core + custom) + boolean = ปุ่ม,
  Yield = slider, คำแนะนำ/หมายเหตุ = text. `MasterDataSelect` คงไว้ใช้เฉพาะหน้า admin.
  Registry: `list`→OptionButtons, `boolean`→toggle. Frontend-only (ไม่มี migration).
- ⚠️ migration 0020 drops columns → demo data ในฟิลด์ที่ตัดหาย (dev ok). ตรวจ verify ผ่าน:
  alembic 0019/0020, seed (master_data 8 types, core re-aligned, stale=0), tsc ผ่าน.

## Decision log
- **RBAC**: ปรับลง scaffold roles/permissions (ไม่สร้าง `can()` engine ใหม่) — ใช้ `require_permission(key)`
- **Data isolation**: app-layer scope ก่อน → Postgres RLS ที่ Step 8 (defense-in-depth)
- **user↔supplier**: `users.supplier_id` FK ใหม่ (nullable) + `plot_assignments` join table
- **⚠️ Deviation (Step 6)**: 20 ฟิลด์ของ `records` ที่ implement จริง **ไม่ตรงกับ Spec §7** —
  โค้ดใช้ `crop_type, growth_stage, area_rai, plant_height_cm, pest_*, disease_*, weed_severity,
  fertilizer_*, irrigation_method, weather_condition, recommendation, notes, lat/lng, photo_urls`.
  Step 12 seed FieldDefinition ให้อิง **คอลัมน์จริงในโค้ด** (codebase pattern > spec).

## หลักฐานสถานะ (ตรวจ ณ commit step-11)
- migrations ถึง `2026_06_29_0500-0017_record_gps_photos`
- models: supplier, user(+supplier_id), plot, plot_assignment, record(+custom_fields)
- api/v1: suppliers, users, plots, records, dashboard
- frontend/src/pages/farmlog: RecordForm, RecordList, RecordPreview, admin/{Suppliers,Plots}
