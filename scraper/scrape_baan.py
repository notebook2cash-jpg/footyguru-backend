#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootyGuru scraper → Supabase  (รันโดย GitHub Actions วันละ 2 รอบ)

- ดึง /gurus?date= ของ 3 วัน (เมื่อวาน/วันนี้/พรุ่งนี้, อิง Asia/Bangkok)
- Incremental: เมื่อวานที่มี detail แล้ว=ข้าม · วันนี้/พรุ่งนี้เทียบ content_hash
- โลโก้: ดาวน์โหลดครั้งเดียว อัปขึ้น Supabase Storage (ไม่ hotlink ต้นทางอีก)
- เขียนลง Postgres (upsert) + ลบข้อมูลนอกหน้าต่าง 3 วัน
- "เนียน": แค่ 2 รอบ/วัน, หน่วงแบบสุ่ม, เคารพ 429/Retry-After

ENV: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os, sys, re, json, time, html, random, hashlib, datetime, urllib.request, urllib.error
from zoneinfo import ZoneInfo

BASE = "https://baan.football"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TZ = ZoneInfo("Asia/Bangkok")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = "team-logos"
MIN_DELAY, MAX_DELAY = 1.0, 2.5   # หน่วงแบบสุ่มระหว่าง request (เนียน)

def log(*a): print(*a, file=sys.stderr, flush=True)

# ---------------- HTTP (ต้นทาง) ----------------
def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "th,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:                      # เคารพ rate-limit
                wait = int(e.headers.get("Retry-After", "30"))
                log(f"    429 → รอ {wait}s"); time.sleep(wait); continue
            if i == tries - 1: raise
            time.sleep(2 ** i)
        except Exception:
            if i == tries - 1: raise
            time.sleep(2 ** i)
    raise RuntimeError("fetch failed: " + url)

def nap():  # หน่วงแบบสุ่มเลียนแบบคน
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), r.headers.get("Content-Type", "application/octet-stream")

# ---------------- Supabase REST ----------------
def _sb_headers(extra=None):
    h = {"apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY,
         "Content-Type": "application/json"}
    if extra: h.update(extra)
    return h

def sb_request(method, path, params="", body=None, headers=None):
    url = f"{SUPABASE_URL}{path}" + (("?" + params) if params else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_sb_headers(headers))
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None

def sb_select_existing(dates):
    q = "match_date=in.(%s)&select=id,content_hash,has_detail" % ",".join(dates)
    try:
        rows = sb_request("GET", "/rest/v1/matches", q) or []
        return {r["id"]: r for r in rows}
    except Exception as e:
        log("    (select existing error)", e); return {}

def sb_upsert_matches(rows):
    if not rows: return
    sb_request("POST", "/rest/v1/matches", "",
               body=rows, headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

def sb_delete_outside(dates):
    # ลบแมตช์ที่ไม่ได้อยู่ใน 3 วันนี้
    q = "match_date=not.in.(%s)" % ",".join(dates)
    try: sb_request("DELETE", "/rest/v1/matches", q, headers={"Prefer": "return=minimal"})
    except Exception as e: log("    (cleanup error)", e)

def sb_get_logo(name):
    try:
        rows = sb_request("GET", "/rest/v1/team_logos",
                          "name=eq.%s&select=url" % urllib.parse.quote(name)) or []
        return rows[0]["url"] if rows else None
    except Exception:
        return None

def sb_put_logo_record(name, source_url, storage_path, public_url):
    sb_request("POST", "/rest/v1/team_logos", "",
               body=[{"name": name, "source_url": source_url,
                      "storage_path": storage_path, "url": public_url}],
               headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

def sb_upload_logo(path, content, content_type):
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}"
    req = urllib.request.Request(url, data=content, method="POST", headers={
        "apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY,
        "Content-Type": content_type, "x-upsert": "true"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"

import urllib.parse
_LOGO_CACHE = {}  # name -> our public url (ภายในรอบนี้)
def ensure_logo(name, source_url):
    if not source_url: return None
    if name in _LOGO_CACHE: return _LOGO_CACHE[name]
    existing = sb_get_logo(name)
    if existing:
        _LOGO_CACHE[name] = existing; return existing
    try:
        content, ctype = fetch_bytes(source_url)
        fname = source_url.split("/")[-1].split("?")[0] or (hashlib.md5(name.encode()).hexdigest()+".webp")
        public = sb_upload_logo(fname, content, ctype)
        sb_put_logo_record(name, source_url, fname, public)
        _LOGO_CACHE[name] = public
        log(f"    + โลโก้ {name}")
        nap()
        return public
    except Exception as e:
        log(f"    (logo fail {name})", e); return source_url  # fallback: ต้นทาง

# ---------------- Parsing (เหมือนตัว standalone) ----------------
def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def abs_url(u):
    if not u: return None
    return u if u.startswith("http") else BASE + html.unescape(u)

def parse_listing(h):
    groups = []
    for gm in re.finditer(r'<section class="guru-league-group"[\s\S]*?(?=<section class="guru-league-group"|</main>|$)', h):
        block = gm.group(0)
        tm = re.search(r'class="guru-league-title"[^>]*>([\s\S]*?)</span>', block)
        league = clean(tm.group(1)) if tm else ""
        items = [x for x in (parse_item(li.group(0), league)
                 for li in re.finditer(r'<li class="guru-item[\s\S]*?</li>', block)) if x]
        if items: groups.append({"league": league, "matches": items})
    return groups

def parse_item(li, league):
    href = (re.search(r'href="([^"]+)"', li) or [None, ""])[1]
    url = abs_url(href)
    mid = (re.search(r'-(\d+)\?', url or "") or re.search(r'-(\d+)(?:#|$)', url or "") or [None, None])[1]
    gid = (re.search(r'guru=(\d+)', url or "") or [None, None])[1]
    time_ = clean((re.search(r'guru-pick-time"[^>]*>([\s\S]*?)</span>', li) or [None, ""])[1])
    names = re.findall(r'class="guru-pick-name([^"]*)">\s*([^<]+?)\s*</span>', li)
    logos = re.findall(r'class="guru-pick-logo">\s*<img src="([^"]+)"', li)
    if len(names) < 2: return None
    teams = [{"name": html.unescape(nm.strip()),
              "logo": abs_url(logos[i]) if i < len(logos) else None,
              "picked": "is-picked" in cls} for i, (cls, nm) in enumerate(names[:2])]
    pred = ""
    si = li.find("guru-pick-score-value")
    if si >= 0:
        nums = re.findall(r'<span>(\d+)</span>', li[si:si + 220])
        if len(nums) >= 2: pred = f"{nums[0]} - {nums[1]}"
    pick = "home" if teams[0]["picked"] else "away" if teams[1]["picked"] else "draw"
    return {"id": mid or href, "matchId": mid, "guruId": gid, "league": league, "url": url,
            "time": time_, "home": teams[0]["name"], "away": teams[1]["name"],
            "homeLogo": teams[0]["logo"], "awayLogo": teams[1]["logo"], "pred": pred, "pick": pick}

def section_between(prose, start, stops):
    i = prose.find(start)
    if i < 0: return ""
    seg = prose[i + len(start):]; cut = len(seg)
    for st in stops:
        j = seg.find(st)
        if 0 <= j < cut: cut = j
    return seg[:cut]

def parse_detail(h):
    out = {}
    bt = re.search(r'banner-top"[^>]*>([\s\S]*?)<', h)
    if bt:
        parts = clean(bt.group(1)).split("|")
        out["league"] = parts[0].strip()
    h1 = clean((re.search(r'<h1[^>]*>([\s\S]*?)</h1>', h) or [None, ""])[1])
    ranks = re.findall(r'\[(\d+)\]', h1)
    if len(ranks) >= 2: out["hr"], out["ar"] = int(ranks[0]), int(ranks[1])
    gi = h.find("guru-content"); seg = h[gi:gi + 20000] if gi >= 0 else h
    s = re.sub(r'<h3[^>]*>', "\n##H3##", seg); s = re.sub(r'<h4[^>]*>', "\n##H4##", s)
    s = re.sub(r'<p[^>]*>', "\n", s); s = re.sub(r'<br\s*/?>', "\n", s)
    s = re.sub(r'<[^>]+>', " ", s); s = html.unescape(s)
    lines = [re.sub(r'[ \t]+', " ", l).strip() for l in s.splitlines()]
    text = "\n".join(l for l in lines if l and l != "\xa0")
    out["venue"] = clean((re.search(r'สนามแข่งขัน\s*:\s*([^\n]+)', text) or [None, ""])[1])
    out["kickoffTH"] = clean((re.search(r'(วันที่[^\n]+น\.)', text) or [None, ""])[1])
    h2 = section_between(text, "สถิติการพบกัน 5 นัดหลังสุด", ["##H3##", "##H4##", "ผลการแข่งขัน"])
    out["h2h"] = [l for l in h2.split("\n") if re.search(r'\d+-\d+', l)][:5]
    fs = section_between(text, "ผลการแข่งขัน 5 นัดล่าสุด", ["วิเคราะห์ทีม"])
    fb = re.split(r'##H4##', fs)
    def form_of(b):
        r = []
        for l in b.split("\n"):
            if "(ชนะ)" in l: r.append("W")
            elif "(แพ้)" in l: r.append("L")
            elif "(เสมอ)" in l: r.append("D")
        return r[:5]
    out["formHome"] = form_of(fb[1]) if len(fb) > 1 else []
    out["formAway"] = form_of(fb[2]) if len(fb) > 2 else []
    ans = section_between(text, "วิเคราะห์ทีมและสภาพความพร้อม", ["ตอกย้ำความมั่นใจ", "Disclaimer"])
    ab = re.split(r'##H4##', ans)
    def fld(b, label):
        m = re.search(re.escape(label) + r'\s*:\s*([^\n]+)', b); return clean(m.group(1)) if m else ""
    def atext(b): return clean(re.split(r'แผนการเล่น\s*:', b)[0])[:1200]
    if len(ab) > 1:
        out["analysisHome"] = atext(ab[1]); out["formationHome"] = fld(ab[1], "แผนการเล่น")
        out["bansHome"] = fld(ab[1], "ตัวแบน"); out["injuriesHome"] = fld(ab[1], "ตัวเจ็บ")
    if len(ab) > 2:
        out["analysisAway"] = atext(ab[2]); out["formationAway"] = fld(ab[2], "แผนการเล่น")
        out["bansAway"] = fld(ab[2], "ตัวแบน"); out["injuriesAway"] = fld(ab[2], "ตัวเจ็บ")
    if not out.get("hr") and out.get("analysisHome"):
        m = re.search(r'อันดับ\s*(\d+)', out["analysisHome"]);  out["hr"] = int(m.group(1)) if m else None
    if not out.get("ar") and out.get("analysisAway"):
        m = re.search(r'อันดับ\s*(\d+)', out["analysisAway"]); out["ar"] = int(m.group(1)) if m else None
    con = section_between(text, "ตอกย้ำความมั่นใจ", ["Disclaimer", "เจาะลึกวิเคราะห์"])
    out["conclude"] = clean(con)[:800]
    gv = re.search(r'เข้าชม\s*([\d,]+)', text); out["views"] = int(gv.group(1).replace(",", "")) if gv else None
    gd = re.search(r'(\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2})\s*[—-]', text); out["pubDate"] = clean(gd.group(1)) if gd else ""
    return out

# ---------------- build row ----------------
def conf_of(pred):
    try:
        a, b = [int(x) for x in pred.split(" - ")]; d = abs(a - b)
        return 5 if d >= 3 else 4 if d == 2 else 3 if d == 1 else 2
    except Exception:
        return 3

def detail_payload(item, det):
    keys = ["h2h","formHome","formAway","analysisHome","analysisAway","formationHome","formationAway",
            "injuriesHome","injuriesAway","bansHome","bansAway","conclude","views","pubDate","kickoffTH"]
    return {k: det.get(k) for k in keys if det.get(k) not in (None, "", [])}

def build_row(item, det, date_str):
    detail = detail_payload(item, det)
    home_logo = ensure_logo(item["home"], item.get("homeLogo"))
    away_logo = ensure_logo(item["away"], item.get("awayLogo"))
    row = {
        "id": item["id"], "match_date": date_str,
        "league": det.get("league") or item.get("league"),
        "home": item["home"], "away": item["away"],
        "home_logo": home_logo, "away_logo": away_logo,
        "kickoff": item.get("time"), "pred": item.get("pred"), "pick": item.get("pick"),
        "conf": conf_of(item.get("pred","")), "hr": det.get("hr"), "ar": det.get("ar"),
        "venue": det.get("venue"), "detail": detail, "has_detail": bool(detail),
        "source_url": item.get("url"),
        "scraped_at": datetime.datetime.now(TZ).isoformat(timespec="seconds"),
    }
    row["content_hash"] = hashlib.sha256(
        json.dumps({k: row[k] for k in ("pred","pick","hr","ar","venue")} | {"detail": detail},
                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return row

# ---------------- main ----------------
def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        log("!! ต้องตั้ง ENV: SUPABASE_URL, SUPABASE_SERVICE_KEY"); sys.exit(1)
    today = datetime.datetime.now(TZ).date()
    dates = [(today + datetime.timedelta(days=o)).isoformat() for o in (-1, 0, 1)]
    yday = dates[0]
    log(f"เริ่ม scrape (Asia/Bangkok) วัน: {dates}")
    existing = sb_select_existing(dates)
    log(f"  มีใน DB อยู่แล้ว {len(existing)} คู่")

    upserts, seen, fetched, skipped = [], 0, 0, 0
    for ds in dates:
        try:
            groups = parse_listing(fetch(ds and f"{BASE}/gurus?date={ds}"))
        except Exception as e:
            log(f"  {ds}: listing error {e}"); continue
        items = [m for g in groups for m in g["matches"]]
        log(f"  {ds}: {len(groups)} ลีก / {len(items)} คู่")
        nap()
        for it in items:
            seen += 1; mid = it["id"]; prev = existing.get(mid)
            # Incremental: เมื่อวาน ที่มี detail แล้ว => ข้าม (จบเกมแล้ว)
            if ds == yday and prev and prev.get("has_detail"):
                skipped += 1; continue
            try:
                det = parse_detail(fetch(it["url"])); fetched += 1
            except Exception as e:
                log(f"    detail error {it['url']}: {e}"); nap(); continue
            row = build_row(it, det, ds)
            if prev and prev.get("content_hash") == row["content_hash"]:
                skipped += 1  # ไม่เปลี่ยน ไม่ต้อง upsert
            else:
                upserts.append(row)
            nap()

    log(f"  สรุป: เห็น {seen} · ดึง detail {fetched} · ข้าม {skipped} · จะ upsert {len(upserts)}")
    # upsert เป็นก้อน
    for i in range(0, len(upserts), 50):
        sb_upsert_matches(upserts[i:i+50])
    sb_delete_outside(dates)
    log(f"✓ เสร็จ: upsert {len(upserts)} คู่, ลบข้อมูลนอก {dates}")

if __name__ == "__main__":
    main()
