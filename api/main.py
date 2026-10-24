#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootyGuru API (Render) — อ่านจาก Supabase แล้วเสิร์ฟ JSON ให้แอป

Endpoints:
  GET /health
  GET /api/days              → 3 วัน จัดกลุ่มลีก (ข้อมูลการ์ด เบา)
  GET /api/match/{id}        → รายละเอียดเต็ม (วิเคราะห์/H2H/ฟอร์ม/ตัวเจ็บ)

ความปลอดภัย: ต้องส่ง header  X-API-Key: <APP_API_KEY>  (ถ้าตั้ง ENV ไว้)
ENV: SUPABASE_URL, SUPABASE_SERVICE_KEY, APP_API_KEY (optional), ALLOWED_ORIGINS (optional)
"""
import os, json, datetime, urllib.request, urllib.parse
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.middleware.cors import CORSMiddleware

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
APP_API_KEY  = os.environ.get("APP_API_KEY", "")           # ว่าง = ไม่บังคับ key
ORIGINS      = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
TZ = ZoneInfo("Asia/Bangkok")
CACHE = "public, max-age=1800, stale-while-revalidate=3600"   # cache 30 นาที

app = FastAPI(title="FootyGuru API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET"], allow_headers=["*"])

def sb_get(path, params):
    url = f"{SUPABASE_URL}/rest/v1/{path}?{params}"
    req = urllib.request.Request(url, headers={
        "apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def check_key(x_api_key):
    if APP_API_KEY and x_api_key != APP_API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")

def three_dates():
    today = datetime.datetime.now(TZ).date()
    return [(today + datetime.timedelta(days=o)).isoformat() for o in (-1, 0, 1)]

CARD_COLS = "id,match_date,league,home,away,home_logo,away_logo,kickoff,pred,pick,conf,hr,ar"

@app.get("/health")
def health():
    try:
        sb_get("matches", "select=id&limit=1")
        return {"ok": True, "time": datetime.datetime.now(TZ).isoformat(timespec="seconds")}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unreachable: {e}")

@app.get("/api/days")
def days(response: Response, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    dates = three_dates()
    q = f"match_date=in.({','.join(dates)})&select={CARD_COLS}&order=match_date,league,kickoff"
    try:
        rows = sb_get("matches", q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    days_map = {d: {} for d in dates}
    for r in rows:
        d, lg = r["match_date"], r.get("league") or "อื่นๆ"
        days_map.setdefault(d, {}).setdefault(lg, []).append(r)
    out = {d: [{"league": lg, "matches": ms} for lg, ms in days_map.get(d, {}).items()] for d in dates}
    response.headers["Cache-Control"] = CACHE
    return {"source": "baan.football", "tz": "Asia/Bangkok", "dates": dates, "days": out}

@app.get("/api/match/{match_id}")
def match(match_id: str, response: Response, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    q = f"id=eq.{urllib.parse.quote(match_id)}&select=*&limit=1"
    try:
        rows = sb_get("matches", q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    if not rows:
        raise HTTPException(status_code=404, detail="match not found")
    response.headers["Cache-Control"] = CACHE
    return rows[0]

@app.get("/api/standings")
def standings(response: Response, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    try:
        rows = sb_get("standings", "select=league,data,updated_at&order=league")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    response.headers["Cache-Control"] = CACHE
    return {"leagues": [r["league"] for r in rows], "standings": {r["league"]: r["data"] for r in rows}}

@app.get("/api/standings/{league}")
def standings_one(league: str, response: Response, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    q = f"league=eq.{urllib.parse.quote(league)}&select=league,data,updated_at&limit=1"
    try:
        rows = sb_get("standings", q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    if not rows:
        raise HTTPException(status_code=404, detail="league not found")
    response.headers["Cache-Control"] = CACHE
    return rows[0]

@app.get("/api/news")
def news(response: Response, limit: int = 40, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    limit = max(1, min(limit, 100))
    # list แบบเบา (ไม่ส่ง body) เรียงล่าสุดก่อน
    q = f"category=eq.football&select=id,title,summary,image,published,flag,url&order=id.desc&limit={limit}"
    try:
        rows = sb_get("news", q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    response.headers["Cache-Control"] = CACHE
    return {"category": "football", "count": len(rows), "news": rows}

@app.get("/api/news/{news_id}")
def news_one(news_id: str, response: Response, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    q = f"id=eq.{urllib.parse.quote(news_id)}&select=*&limit=1"
    try:
        rows = sb_get("news", q)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"supabase error: {e}")
    if not rows:
        raise HTTPException(status_code=404, detail="news not found")
    response.headers["Cache-Control"] = CACHE
    return rows[0]
