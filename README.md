# FootyGuru Backend

Backend + cache สำหรับแอปวิเคราะห์บอล — ดึงข้อมูลจาก baan.football **วันละ 2 รอบ** (08:00 / 13:00 ไทย)
แล้วเก็บใน Supabase ให้แอป 10,000 user อ่านผ่าน API ของเราเอง (ไม่แตะเว็บต้นทางเลย)

```
GitHub Actions (cron 2×/วัน) ─► scraper ─► Supabase (Postgres + Storage/โลโก้)
                                                 ▲
                              10,000 แอป ─► Render API (FastAPI) ─┘
```

| ส่วน | เทคโนโลยี | หน้าที่ |
|---|---|---|
| `scraper/` | Python (stdlib ล้วน) | ดึง 3 วัน (เมื่อวาน/วันนี้/พรุ่งนี้) แบบ incremental + อัปโหลดโลโก้ |
| `api/` | FastAPI | อ่าน Supabase → JSON (`/api/days`, `/api/match/{id}`) |
| `supabase/schema.sql` | SQL | สร้างตาราง + bucket โลโก้ |
| `.github/workflows/scrape.yml` | GitHub Actions | cron 2×/วัน |
| `render.yaml` | Render Blueprint | deploy API |

---

## ⚠️ ก่อน deploy — ยืนยัน account ทุกครั้ง
ก่อนขึ้น GitHub / Render / Supabase ให้ยืนยันว่าใช้ **account ไหน** กันเอาขึ้นผิดที่

---

## ขั้นตอนติดตั้ง (ทำครั้งเดียว)

### 1) Supabase
1. สร้าง project ใหม่ (เลือก region ใกล้ไทย เช่น Singapore)
2. SQL Editor → รัน `supabase/schema.sql` (สร้างตาราง + bucket `team-logos`)
3. เก็บค่า: **Project URL** และ **service_role key** (Settings → API)

### 2) GitHub
1. สร้าง repo แล้ว push โฟลเดอร์นี้ขึ้นไป
2. Settings → Secrets and variables → Actions → เพิ่ม 2 secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
3. ทดสอบ: แท็บ Actions → `scrape-baan` → Run workflow (manual) แล้วดู log

### 3) Render
1. New → Blueprint → เลือก repo นี้ (จะอ่าน `render.yaml`)
2. กรอก env 3 ตัว:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `APP_API_KEY`  ← ตั้งรหัสลับอะไรก็ได้ ให้แอปส่งมาใน header `X-API-Key`
3. Deploy → ได้ URL เช่น `https://baan-api.onrender.com`

### 4) แอป Android
ตั้งค่า:
- `API_BASE_URL = https://baan-api.onrender.com`
- `APP_API_KEY  = <ค่าเดียวกับ Render>`
เรียก `GET /api/days` และ `GET /api/match/{id}` (แนบ header `X-API-Key`)

---

## ทดสอบในเครื่อง (ก่อน deploy)

```bash
export SUPABASE_URL="https://xxxx.supabase.co"
export SUPABASE_SERVICE_KEY="<service_role key>"

# รัน scraper 1 รอบ (เขียนลง Supabase จริง)
python3 scraper/scrape_baan.py

# รัน API
pip install -r api/requirements.txt
export APP_API_KEY="dev-key"
uvicorn main:app --app-dir api --reload
# ทดสอบ: curl -H "X-API-Key: dev-key" http://127.0.0.1:8000/api/days
```

---

## การ "เนียน" ต่อเว็บต้นทาง
- แตะ baan.football แค่ **2 รอบ/วัน** (~40–75 req/รอบ), หน่วงสุ่ม 1–2.5 วิ
- Incremental: เมื่อวานที่ดึงแล้ว=ข้าม · วันนี้/พรุ่งนี้เทียบ `content_hash`
- โลโก้ดาวน์โหลดครั้งเดียวตลอดชีพ (เก็บใน Supabase Storage)
- เคารพ 429 / `Retry-After`

## หมายเหตุลิขสิทธิ์
เนื้อหาบทวิเคราะห์เป็นลิขสิทธิ์ของ baan.football — ระบบนี้เพื่อการศึกษา/ต้นแบบ
ถ้าใช้เชิงพาณิชย์จริง แนะนำซื้อ data feed ลิขสิทธิ์สำหรับข้อเท็จจริง + สร้างบทวิเคราะห์เอง
