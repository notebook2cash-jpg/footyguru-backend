#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standings builder → Supabase

- PL/PD/SA/FL1/BL1 -> football-data.org  (GET /v4/competitions/{code}/standings, type=TOTAL)
- Bundesliga 2     -> OpenLigaDB         (GET /getbltable/bl2/{year})  (ไม่ต้อง token)

เก็บลงตาราง Supabase `standings` (1 แถว/ลีก, คอลัมน์ data = jsonb ตาม StandingRow schema)
schema StandingRow: position, team, crest, played, win, draw, loss, goalsFor, goalsAgainst, goalDiff, points

ENV: SUPABASE_URL, SUPABASE_SERVICE_KEY, FOOTBALL_DATA_TOKEN
"""
import os, sys, re, json, time, datetime, urllib.request, urllib.error
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Bangkok")

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
        except FileNotFoundError:
            pass
_load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
FD_TOKEN     = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()

LEAGUES = [
    {"key": "premier-league", "code": "PL",  "name": "Premier League", "name_th": "พรีเมียร์ลีก", "country": "England"},
    {"key": "laliga",         "code": "PD",  "name": "La Liga",        "name_th": "ลาลีกา",      "country": "Spain"},
    {"key": "serie-a",        "code": "SA",  "name": "Serie A",        "name_th": "เซเรีย อา",   "country": "Italy"},
    {"key": "ligue-1",        "code": "FL1", "name": "Ligue 1",        "name_th": "ลีกเอิง",     "country": "France"},
    {"key": "bundesliga",     "code": "BL1", "name": "Bundesliga",     "name_th": "บุนเดสลีกา",  "country": "Germany"},
]
OPENLIGA = [
    {"key": "bundesliga-2", "shortcut": "bl2", "name": "Bundesliga 2", "name_th": "บุนเดสลีกา 2", "country": "Germany"},
]

def log(*a): print(*a, file=sys.stderr, flush=True)
def now_iso(): return datetime.datetime.now(TZ).isoformat(timespec="seconds")
def season_start():
    n = datetime.datetime.now(TZ)
    return n.year if n.month >= 8 else n.year - 1
def season_str(): y = season_start(); return f"{y}-{y+1}"
def _to_int(v, default=0):
    try: return int(v)
    except (TypeError, ValueError): return default

def _get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

# ---------- football-data.org (rate-limited 10/min) ----------
_last_fd = [0.0]
def fd_get(path, tries=4):
    url = "https://api.football-data.org/v4" + path
    for i in range(tries):
        wait = 7.0 - (time.time() - _last_fd[0])   # เว้น ~7s/req (free tier)
        if wait > 0: time.sleep(wait)
        _last_fd[0] = time.time()
        try:
            return _get_json(url, {"X-Auth-Token": FD_TOKEN})
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = int(e.headers.get("Retry-After", "60")); log(f"    429 → รอ {retry}s"); time.sleep(retry); continue
            if i == tries - 1: raise
            time.sleep(2 ** i)
        except Exception:
            if i == tries - 1: raise
            time.sleep(2 ** i)
    raise RuntimeError("football-data GET failed: " + path)

def openliga_get(path, tries=3):
    for i in range(tries):
        try: return _get_json("https://api.openligadb.de" + path)
        except Exception as e:
            log(f"    OpenLigaDB error {e}; retry {i+1}"); time.sleep(2 ** i)
    raise RuntimeError("OpenLigaDB GET failed: " + path)

# ---------- row mappers (StandingRow schema) ----------
def row_fd(r):
    t = r.get("team") or {}; gf = _to_int(r.get("goalsFor")); ga = _to_int(r.get("goalsAgainst"))
    return {"position": _to_int(r.get("position")), "team": t.get("shortName") or t.get("name") or "-",
            "crest": t.get("crest"), "played": _to_int(r.get("playedGames")), "win": _to_int(r.get("won")),
            "draw": _to_int(r.get("draw")), "loss": _to_int(r.get("lost")), "goalsFor": gf, "goalsAgainst": ga,
            "goalDiff": _to_int(r.get("goalDifference"), gf - ga), "points": _to_int(r.get("points"))}

def row_openliga(r, i):
    gf = _to_int(r.get("goals")); ga = _to_int(r.get("goalsAgainst")) or _to_int(r.get("opponentGoals"))
    return {"position": _to_int(r.get("rank")) or i + 1, "team": r.get("teamName") or r.get("shortName") or "-",
            "crest": r.get("teamIconUrl"), "played": _to_int(r.get("matches")), "win": _to_int(r.get("won")),
            "draw": _to_int(r.get("draw")), "loss": _to_int(r.get("lost")), "goalsFor": gf, "goalsAgainst": ga,
            "goalDiff": _to_int(r.get("goalDiff"), gf - ga), "points": _to_int(r.get("points"))}

# ---------- Supabase ----------
def sb_upsert(rows):
    if not rows: return
    url = f"{SUPABASE_URL}/rest/v1/standings"
    req = urllib.request.Request(url, data=json.dumps(rows).encode(), method="POST", headers={
        "apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY, "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal"})
    with urllib.request.urlopen(req, timeout=30) as r: r.read()

# ---------- build ----------
def build():
    if not SUPABASE_URL or not SERVICE_KEY: log("!! ต้องตั้ง SUPABASE_URL, SUPABASE_SERVICE_KEY"); sys.exit(1)
    if not FD_TOKEN: log("!! ต้องตั้ง FOOTBALL_DATA_TOKEN (สมัครฟรีที่ football-data.org)"); sys.exit(1)
    rows = []
    for lg in LEAGUES:
        try:
            data = fd_get(f"/competitions/{lg['code']}/standings")
            total = next((s for s in data.get("standings", []) if s.get("type") == "TOTAL"), None)
            table = [row_fd(r) for r in (total or {}).get("table", [])]
            if not table: log(f"  {lg['code']}: ว่าง ข้าม"); continue
            rows.append({"league": lg["key"], "data": {"generated_at": now_iso(), "season": season_str(),
                        "key": lg["key"], "league_name": lg["name"], "league_name_th": lg["name_th"], "table": table},
                        "updated_at": now_iso()})
            log(f"  {lg['key']}: {len(table)} ทีม")
        except Exception as e:
            log(f"  {lg['code']}: error {e}")
    for lg in OPENLIGA:
        try:
            raw = openliga_get(f"/getbltable/{lg['shortcut']}/{season_start()}")
            table = [row_openliga(r, i) for i, r in enumerate(raw or [])]
            if not table: log(f"  {lg['shortcut']}: ว่าง ข้าม"); continue
            rows.append({"league": lg["key"], "data": {"generated_at": now_iso(), "season": season_str(),
                        "key": lg["key"], "league_name": lg["name"], "league_name_th": lg["name_th"], "table": table},
                        "updated_at": now_iso()})
            log(f"  {lg['key']}: {len(table)} ทีม (OpenLigaDB)")
        except Exception as e:
            log(f"  {lg['shortcut']}: error {e}")
    sb_upsert(rows)
    log(f"✓ อัปเดตตารางคะแนน {len(rows)} ลีก")

if __name__ == "__main__":
    build()
