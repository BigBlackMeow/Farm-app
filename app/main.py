"""Web app: pick a field (LPIS) and a crop, get the plant protection products allowed there.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import evaluate as ev
from . import lpis

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Farm App – přípravky na pozemek")
con = ev.connect()
_fields: dict[str, dict] = {}  # field id -> {"field", "ctx"}; in-memory cache

# Optional demo protection: when APP_PASSWORD is set, the browser asks for a
# password (any user name works). /healthz stays open for the hosting health check.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


@app.middleware("http")
async def password_gate(request: Request, call_next):
    if not APP_PASSWORD or request.url.path == "/healthz":
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
        except Exception:
            pw = ""
        if secrets.compare_digest(pw.encode(), APP_PASSWORD.encode()):
            return await call_next(request)
    return PlainTextResponse("Zadejte heslo.", status_code=401,
                             headers={"WWW-Authenticate": 'Basic realm="Farm App", charset="UTF-8"'})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    return {"registry_date": ev.registry_date(con)}


@app.get("/api/crops")
def crops(q: str = ""):
    return ev.list_crops(con, q)


@app.get("/api/field")
def field(square: str | None = None, code: str | None = None,
          lat: float | None = None, lon: float | None = None):
    try:
        if square and code:
            f = lpis.field_by_code(square, code)
        elif lat is not None and lon is not None:
            f = lpis.field_at(lat, lon)
        else:
            raise HTTPException(400, "Zadejte čtverec a kód dílu, nebo klikněte do mapy.")
    except lpis.FieldNotFound:
        raise HTTPException(404, "Díl půdního bloku nebyl v LPIS nalezen.")
    key = str(f["id"])
    if key not in _fields:
        _fields[key] = {"field": f, "ctx": lpis.field_context(f)}
    entry = _fields[key]
    return {"field": lpis.field_public(entry["field"]), "context": lpis.public_context(entry["ctx"])}


@app.get("/api/evaluate")
def evaluate(field_id: str, crop: str, nozzle: int = 0):
    if nozzle not in (0, 50, 75, 90):
        raise HTTPException(400, "Redukce úletu musí být 0, 50, 75 nebo 90 %.")
    entry = _fields.get(field_id)
    if entry is None:
        raise HTTPException(404, "Nejdřív načtěte pozemek.")
    try:
        return ev.evaluate(con, entry["ctx"], crop, nozzle)
    except ValueError as e:
        raise HTTPException(400, str(e))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
