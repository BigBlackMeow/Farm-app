"""Web app: pick a field (LPIS) and a crop, get the plant protection products allowed there.

Run:  uvicorn app.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import evaluate as ev
from . import lpis
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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
