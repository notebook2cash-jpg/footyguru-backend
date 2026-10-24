#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
News scraper (ข่าวฟุตบอลต่างประเทศ) → Supabase
แหล่ง: thsport.app  /sport-news/football/?page=N  (list) + /sport-news/football/news-{id}.html (detail)

- list: div.item → เวลา, หัวข้อ, id, ธงลีก
- detail: og:title, og:image, og:description (สรุป), div.detail (เนื้อหา)
- Incremental: ข่าว id ที่มีใน DB แล้ว = ข้าม (ไม่ดึง detail ซ้ำ)
- เก็บล่าสุด ~100 ข่าว (ลบเก่าออก)
- ยิงผ่าน ScraperAPI ถ้ามี key (เผื่อ Cloudflare บล็อก IP cloud)

ENV: SUPABASE_URL, SUPABASE_SERVICE_KEY, SCRAPERAPI_KEY (optional)
"""
import os, sys, re, json, time, html, random, datetime, urllib.request, urllib.error, urllib.parse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Bangkok")
BASE = "https://www.thsport.app"
CATEGORY = "football"            # ข่าวฟุตบอลต่างประเทศ
KEEP = 100                        # เก็บข่าวล่าสุดกี่ชิ้น
PAGES = 3                         # ดึง list กี่หน้าต่อรอบ
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

def _load_env():
    for p in (os.path.join(os.path.dirname(__file__), "..", ".env"), ".env"):
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line: continue
                    k, v = line.split("=", 1)
                    v = re.split(r'\s+#', v.strip())[0].strip().strip('"').strip("'")
                    os.environ.setdefault(k.strip(), v)
        except FileNotFoundError: pass
_load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

def log(*a): print(*a, file=sys.stderr, flush=True)
def nap(): time.sleep(random.uniform(0.8, 2.0))

def _target(url):
    if not SCRAPERAPI_KEY: return url
    return "https://api.scraperapi.com/?" + urllib.parse.urlencode({"api_key": SCRAPERAPI_KEY, "url": url})

def fetch(url, tries=4):
    target = _target(url); timeout = 120 if SCRAPERAPI_KEY else 30
    for i in range(tries):
        try:
            req = urllib.request.Request(target, headers={"User-Agent": UA, "Accept-Language": "th,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                w = int(e.headers.get("Retry-After", "30")); log(f"    429 → รอ {w}s"); time.sleep(w); continue
            if i == tries - 1: raise
            time.sleep(2 ** i)
        except Exception:
            if i == tries - 1: raise
            time.sleep(2 ** i)
    raise RuntimeError("fetch failed: " + url)

# ---------- Supabase REST ----------
def sb(method, path, params="", body=None, headers=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}" + (("?" + params) if params else "")
    h = {"apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY, "Content-Type": "application/json"}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read(); return json.loads(raw) if raw else None

def existing_ids():
    try:
        rows = sb("GET", "news", f"category=eq.{CATEGORY}&select=id") or []
        return {r["id"] for r in rows}
    except Exception as e:
        log("  (select error)", e); return set()

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

# ---------- parse ----------
def parse_list(h):
    """คืน list ของ (id, title, time, flag) — bound ทีละ div.item ให้แม่น"""
    out = []
    for blk in re.finditer(r'<div class="item">([\s\S]*?<a href="/sport-news/football/news-\d+\.html">[\s\S]*?</a>)', h):
        b = blk.group(1)
        a = re.search(r'<a href="/sport-news/football/news-(\d+)\.html">(.*?)</a>', b, re.S)
        if not a: continue
        nid, title = a.group(1), clean(a.group(2))
        tm = re.search(r'<span class="time">(.*?)</span>', b, re.S)
        fm = re.search(r'<span class="flag ([^"]*)"></span>', b)
        out.append({"id": nid, "title": title,
                    "published": clean(tm.group(1)) if tm else "",
                    "flag": (fm.group(1).strip() if fm else "")})
    return out

def parse_detail(h):
    def og(p):
        m = re.search(r'property="og:' + p + r'" content="([^"]*)"', h) or \
            re.search(r'name="og:' + p + r'" content="([^"]*)"', h)
        return html.unescape(m.group(1)).strip() if m else ""
    image = og("image"); summary = og("description"); title = clean(og("title")).split(" | ")[0]
    # เนื้อหาใน div.detail
    body = ""
    mi = re.search(r'class="[^"]*\bdetail\b[^"]*"[^>]*>([\s\S]*?)(?:<div[^>]+class="[^"]*(?:related|comment|tag|share|social)|<footer|</body)', h, re.I)
    if mi:
        body = clean(mi.group(1))
        for cut in ["ข่าวที่เกี่ยวข้อง", "ข่าวอื่น", "แสดงความคิดเห็น", "อ่านข่าว"]:
            j = body.find(cut)
            if j > 200: body = body[:j]
        body = body[:2500]
    return {"title": title, "image": image, "summary": summary, "body": body}

# ---------- main ----------
def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        log("!! ต้องตั้ง SUPABASE_URL, SUPABASE_SERVICE_KEY"); sys.exit(1)
    have = existing_ids(); log(f"มีข่าวใน DB แล้ว {len(have)} ชิ้น")
    items = []
    for p in range(1, PAGES + 1):
        url = f"{BASE}/sport-news/{CATEGORY}/" + (f"?page={p}" if p > 1 else "")
        try:
            lst = parse_list(fetch(url))
            log(f"  หน้า {p}: {len(lst)} ข่าว"); items += lst; nap()
        except Exception as e:
            log(f"  หน้า {p}: error {e}")
    # unique by id
    uniq = {}; [uniq.setdefault(it["id"], it) for it in items]
    new = [it for it in uniq.values() if it["id"] not in have]
    log(f"  รวม {len(uniq)} ข่าว · ใหม่ {len(new)} ชิ้น (ดึง detail)")
    rows = []
    for it in new:
        url = f"{BASE}/sport-news/{CATEGORY}/news-{it['id']}.html"
        try:
            d = parse_detail(fetch(url))
            rows.append({"id": it["id"], "category": CATEGORY,
                "title": d["title"] or it["title"], "summary": d["summary"],
                "image": d["image"], "body": d["body"], "published": it["published"],
                "flag": it["flag"], "url": url,
                "scraped_at": datetime.datetime.now(TZ).isoformat(timespec="seconds")})
            log(f"    + {it['id']} {(d['title'] or it['title'])[:40]}")
            nap()
        except Exception as e:
            log(f"    {it['id']}: error {e}"); nap()
    for i in range(0, len(rows), 50):
        sb("POST", "news", "", body=rows[i:i+50], headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
    # trim เก็บล่าสุด KEEP ชิ้น (id เรียงจากมากไปน้อย)
    try:
        allids = sorted((int(r["id"]) for r in (sb("GET", "news", f"category=eq.{CATEGORY}&select=id") or [])), reverse=True)
        if len(allids) > KEEP:
            drop = allids[KEEP:]
            sb("DELETE", "news", f"category=eq.{CATEGORY}&id=in.({','.join(str(x) for x in drop)})", headers={"Prefer": "return=minimal"})
            log(f"  ลบข่าวเก่า {len(drop)} ชิ้น (เก็บล่าสุด {KEEP})")
    except Exception as e:
        log("  (trim error)", e)
    log(f"✓ อัปเดตข่าว: เพิ่ม {len(rows)} ชิ้น")

if __name__ == "__main__":
    main()
