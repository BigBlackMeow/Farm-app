"""Web app: pick a field (LPIS) and a crop, get the plant protection products allowed there.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import evaluate as ev
from . import lpis
from . import records as rec
from . import users

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Farm App – přípravky na pozemek")
_registry = {"con": None}
NO_REGISTRY = ("Registr přípravků není stažený. Zastavte aplikaci (Ctrl+C), spusťte "
               "python -m app.registry_import a pak aplikaci spusťte znovu.")


def registry():
    """Connection to the product registry; 503 with instructions if it has not been downloaded yet."""
    if _registry["con"] is None:
        _registry["con"] = ev.connect_if_ready()
    if _registry["con"] is None:
        raise HTTPException(503, NO_REGISTRY)
    return _registry["con"]


_fields: dict[str, dict] = {}  # field id -> {"field", "ctx"}; in-memory cache
store = users.Store()
records = rec.Records(store)
COOKIE = "farm_session"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# --- login -------------------------------------------------------------------------------------
class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


def current_user(request: Request) -> dict:
    user = store.user_for_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, "Přihlaste se.")
    return user


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=users.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")


@app.post("/api/register")
def register(body: Credentials, request: Request, response: Response):
    try:
        s = store.register(body.email, body.password, body.name)
    except users.AuthError as e:
        raise HTTPException(400, str(e))
    _set_cookie(request, response, s["token"])
    return s["user"]


@app.post("/api/login")
def login(body: Credentials, request: Request, response: Response):
    try:
        s = store.login(body.email, body.password)
    except users.AuthError as e:
        raise HTTPException(400, str(e))
    _set_cookie(request, response, s["token"])
    return s["user"]


@app.post("/api/logout")
def logout(request: Request, response: Response):
    store.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


# --- my purchased products ("můj sklad") --------------------------------------------------------
class ProductName(BaseModel):
    product: str


@app.get("/api/products")
def products(q: str = "", user: dict = Depends(current_user), con=Depends(registry)):
    return ev.search_products(con, q.strip())


@app.get("/api/my-products")
def my_products(user: dict = Depends(current_user), con=Depends(registry)):
    return ev.product_info(con, store.products(user["id"]))


@app.post("/api/my-products")
def add_my_product(body: ProductName, user: dict = Depends(current_user), con=Depends(registry)):
    if not ev.product_info(con, [body.product])[0]["known"]:
        raise HTTPException(400, "Přípravek v registru není.")
    store.add_product(user["id"], body.product)
    return ev.product_info(con, store.products(user["id"]))


@app.delete("/api/my-products")
def remove_my_product(product: str, user: dict = Depends(current_user), con=Depends(registry)):
    store.remove_product(user["id"], product)
    return ev.product_info(con, store.products(user["id"]))


# --- fields and evaluation ----------------------------------------------------------------------
@app.get("/api/meta")
def meta():
    try:
        con = registry()
    except HTTPException:
        return {"registry_date": "", "registry_ready": False, "message": NO_REGISTRY}
    return {"registry_date": ev.registry_date(con), "registry_ready": True}


@app.get("/api/crops")
def crops(q: str = "", user: dict = Depends(current_user), con=Depends(registry)):
    return ev.list_crops(con, q)


@app.get("/api/field")
def field(square: str | None = None, code: str | None = None,
          lat: float | None = None, lon: float | None = None, user: dict = Depends(current_user)):
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
def evaluate(field_id: str, crop: str, nozzle: int = 0, user: dict = Depends(current_user),
             con=Depends(registry)):
    if nozzle not in (0, 50, 75, 90):
        raise HTTPException(400, "Redukce úletu musí být 0, 50, 75 nebo 90 %.")
    entry = _fields.get(field_id)
    if entry is None:
        raise HTTPException(404, "Nejdřív načtěte pozemek.")
    try:
        return ev.evaluate(con, entry["ctx"], crop, nozzle, owned=set(store.products(user["id"])))
    except ValueError as e:
        raise HTTPException(400, str(e))



# --- spray records ("evidence postřiků") --------------------------------------------------------
def _period(date_from: str, date_to: str) -> str:
    if date_from and date_to:
        return f"{rec.cz_date(date_from)} – {rec.cz_date(date_to)}"
    if date_from:
        return f"od {rec.cz_date(date_from)}"
    if date_to:
        return f"do {rec.cz_date(date_to)}"
    return "všechny záznamy"


@app.get("/api/farm")
def farm_profile(user: dict = Depends(current_user)):
    return records.profile(user["id"])


@app.put("/api/farm")
def save_farm_profile(body: dict, user: dict = Depends(current_user)):
    try:
        return records.save_profile(user["id"], body)
    except rec.RecordError as e:
        raise HTTPException(400, str(e))


@app.get("/api/records")
def list_records(date_from: str = "", date_to: str = "", user: dict = Depends(current_user)):
    return {"records": records.list(user["id"], date_from, date_to), "profile": records.profile(user["id"]),
            "units": rec.DOSE_UNITS}


@app.get("/api/records/prefill")
def record_prefill(square: str, code: str, crop: str = "", user: dict = Depends(current_user)):
    return records.last_for_field(user["id"], square, code, crop)


@app.post("/api/records")
def add_record(body: dict, user: dict = Depends(current_user)):
    try:
        return records.add(user["id"], body)
    except rec.RecordError as e:
        raise HTTPException(400, str(e))


@app.put("/api/records/{rec_id}")
def update_record(rec_id: int, body: dict, user: dict = Depends(current_user)):
    try:
        return records.update(user["id"], rec_id, body)
    except rec.RecordError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "Záznam nenalezen.")


@app.delete("/api/records/{rec_id}")
def delete_record(rec_id: int, user: dict = Depends(current_user)):
    records.delete(user["id"], rec_id)
    return {"ok": True}


@app.get("/api/records/export.csv")
def export_records_csv(date_from: str = "", date_to: str = "", user: dict = Depends(current_user)):
    data = rec.to_csv(records.list(user["id"], date_from, date_to), records.profile(user["id"]))
    name = "evidence-postriku" + (f"-{date_from}" if date_from else "") + (f"-{date_to}" if date_to else "") + ".csv"
    return RawResponse(data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                       headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/records/print", response_class=HTMLResponse)
def print_records(request: Request, date_from: str = "", date_to: str = ""):
    user = store.user_for_token(request.cookies.get(COOKIE))
    if user is None:
        return HTMLResponse('<p>Přihlaste se. <a href="/">Zpět do aplikace</a></p>', status_code=401)
    return rec.to_print_html(records.list(user["id"], date_from, date_to), records.profile(user["id"]),
                             _period(date_from, date_to))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
