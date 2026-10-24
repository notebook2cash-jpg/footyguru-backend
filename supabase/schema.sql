-- ============================================================
-- FootyGuru backend — Supabase schema
-- รันใน Supabase Dashboard → SQL Editor (ครั้งเดียวตอนตั้งค่า)
-- ============================================================

-- ---------- ตารางแมตช์ (ข้อมูลหลัก + cache) ----------
create table if not exists public.matches (
  id            text primary key,          -- matchId เช่น '3018845'
  match_date    date not null,             -- '2026-10-24' (อิง Asia/Bangkok)
  league        text,
  home          text,
  away          text,
  home_logo     text,                      -- URL ชี้ไป Supabase Storage (ของเรา)
  away_logo     text,
  kickoff       text,                      -- เวลาแข่ง เช่น '20:30'
  pred          text,                      -- คาดการณ์สกอร์ '3 - 2'
  pick          text,                      -- 'home' | 'away' | 'draw'
  conf          int,                       -- ความมั่นใจ 2-5 (คำนวณจาก margin)
  hr            int,                        -- อันดับเจ้าบ้าน
  ar            int,                        -- อันดับทีมเยือน
  venue         text,
  detail        jsonb,                     -- h2h, form, บทวิเคราะห์, ตัวเจ็บ, formation ฯลฯ
  has_detail    boolean not null default false,
  source_url    text,
  content_hash  text,                      -- ไว้เช็คว่าข้อมูลเปลี่ยนไหม (incremental)
  scraped_at    timestamptz not null default now()
);

create index if not exists matches_date_idx    on public.matches (match_date);
create index if not exists matches_league_idx  on public.matches (match_date, league);

-- ---------- ตารางโลโก้ (กันดาวน์โหลดรูปซ้ำ) ----------
create table if not exists public.team_logos (
  name          text primary key,          -- ชื่อทีม (ไทย)
  source_url    text,                      -- URL ต้นทางบน baan.football
  storage_path  text,                      -- path ใน bucket
  url           text,                      -- public URL บน Supabase Storage
  created_at    timestamptz not null default now()
);

-- ---------- ตารางคะแนนลีก (standings) ----------
-- 1 แถว/ลีก · data = jsonb {generated_at, season, key, league_name, league_name_th, table:[StandingRow...]}
create table if not exists public.standings (
  league     text primary key,         -- 'premier-league','laliga','serie-a','ligue-1','bundesliga','bundesliga-2'
  data       jsonb,
  updated_at timestamptz not null default now()
);
alter table public.standings enable row level security;

-- ---------- ข่าว (news) ----------
create table if not exists public.news (
  id         text primary key,          -- news id จาก thsport.app
  category   text,                      -- 'football' (ฟุตบอลต่างประเทศ)
  title      text,
  summary    text,
  body       text,
  image      text,                      -- og:image URL
  published  text,                      -- '24 ต.ค. - 16:19'
  flag       text,                      -- ธงลีก/ชาติ
  url        text,                      -- ลิงก์ต้นฉบับ
  scraped_at timestamptz not null default now()
);
create index if not exists news_cat_idx on public.news (category, id desc);
alter table public.news enable row level security;

-- ---------- Row Level Security ----------
-- เปิด RLS แล้ว "ไม่สร้าง policy สาธารณะ" => อ่าน/เขียนได้เฉพาะ service_role key
-- (scraper ใน GitHub Actions + Render API ใช้ service key ฝั่งเซิร์ฟเวอร์เท่านั้น)
alter table public.matches    enable row level security;
alter table public.team_logos enable row level security;

-- ---------- Storage bucket สำหรับโลโก้ (public read) ----------
insert into storage.buckets (id, name, public)
values ('team-logos', 'team-logos', true)
on conflict (id) do nothing;

-- อนุญาตให้ "อ่าน" รูปในบั๊กเก็ตนี้ได้สาธารณะ (เพื่อให้แอปโหลดรูปผ่าน CDN)
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname='storage' and tablename='objects' and policyname='team-logos public read'
  ) then
    create policy "team-logos public read"
      on storage.objects for select
      using ( bucket_id = 'team-logos' );
  end if;
end $$;
