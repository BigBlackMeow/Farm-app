"""Evaluate which plant protection products may be used on a field for a crop.

Result levels per product:
  ok    - authorized for the crop; no field-specific conflict found
  warn  - may be usable, but a condition applies on this field (shown to the user)
  block - may not be used on this field
Decision support only: the product label (etiketa) and the registry remain binding.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import lpis
from . import prices

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "registry.sqlite"
NOZZLE_COL = {0: "no_reduction", 50: "nozzle50", 75: "nozzle75", 90: "nozzle90"}
BEE_DANGER = ("ZNV", "NV", "SPe8")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def connect_if_ready(db_path: Path = DB_PATH) -> sqlite3.Connection | None:
    """Open the registry read-only; None if it has not been downloaded (python -m app.registry_import)."""
    if not db_path.exists():
        return None
    con = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM decisions LIMIT 1")
    except sqlite3.Error:
        con.close()
        return None
    return con


def registry_date(con) -> str:
    row = con.execute("SELECT value FROM meta WHERE key='registry_last_change'").fetchone()
    return row[0] if row else ""


def list_crops(con, q: str = "", limit: int = 30) -> list[dict]:
    rows = con.execute(
        "SELECT name, n_uses FROM crops WHERE name LIKE ? ORDER BY n_uses DESC LIMIT ?",
        (f"%{q}%", limit)).fetchall()
    return [dict(r) for r in rows]


def search_products(con, q: str = "", limit: int = 30, today: date | None = None) -> list[dict]:
    """Products still usable today whose name or active substance matches `q` (for "můj sklad")."""
    iso = (today or date.today()).isoformat()
    rows = con.execute(
        """SELECT product, MIN(function) AS function, MIN(active_substances) AS subs, MAX(usable_until) AS until
           FROM decisions WHERE usable_until >= ? AND (product LIKE ? OR active_substances LIKE ?)
           GROUP BY product ORDER BY (product LIKE ?) DESC, product LIMIT ?""",
        (iso, f"%{q}%", f"%{q}%", f"{q}%", limit)).fetchall()
    return [{"product": r["product"], "function": r["function"],
             "active_substances": [a["name"] for a in json.loads(r["subs"])], "usable_until": r["until"]}
            for r in rows]


def product_info(con, names: list[str]) -> list[dict]:
    """Function, active substances and usability for the given product names (missing ones flagged)."""
    out = []
    for n in names:
        r = con.execute(
            """SELECT MIN(function) AS function, MIN(active_substances) AS subs, MAX(usable_until) AS until
               FROM decisions WHERE product=?""", (n,)).fetchone()
        if r is None or r["subs"] is None:
            out.append({"product": n, "function": "", "active_substances": [], "usable_until": None, "known": False})
        else:
            out.append({"product": n, "function": r["function"], "known": True, "usable_until": r["until"],
                        "active_substances": [a["name"] for a in json.loads(r["subs"])]})
    return out


# Product types for the result filter: registry function text (B_FCE) -> friendly group name.
# Checked in this order; anything unknown keeps its own name.
TYPE_GROUPS = (
    ("herbicid", "Herbicid"), ("fungicid", "Fungicid"), ("insekticid", "Insekticid"),
    ("akaricid", "Akaricid"), ("moluskocid", "Moluskocid"), ("nematocid", "Nematocid"),
    ("rodenticid", "Rodenticid"), ("baktericid", "Baktericid"), ("regulátor", "Regulátor růstu"),
    ("růst", "Regulátor růstu"), ("desikant", "Desikant"), ("defoliant", "Desikant"),
    ("repelent", "Repelent"), ("feromon", "Feromon"), ("smáčed", "Pomocný prostředek"),
    ("pomocn", "Pomocný prostředek"), ("adjuvant", "Pomocný prostředek"), ("mořidl", "Mořidlo"),
)


def product_types(function: str) -> list[str]:
    """'Fungicid, insekticid' -> ['Fungicid', 'Insekticid']; used to filter results by type."""
    out: list[str] = []
    for part in re.split(r"[,;/+]|\s+a\s+", function or ""):
        part = part.strip(" .").lower()
        if not part:
            continue
        hits = [name for key, name in TYPE_GROUPS if key in part] or [part[:1].upper() + part[1:]]
        for h in hits:
            if h not in out:
                out.append(h)
    return out or ["Neuvedeno"]


def split_targets(text: str) -> list[str]:
    """Split a registry target text ('plíseň bramborová, alternáriová skvrnitost') into
    organisms, ignoring commas inside brackets."""
    parts, depth, cur = [], 0, ""
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch in ",;" and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [t for t in (re.sub(r"\s+", " ", x).strip(" .") for x in parts) if t]


def _pick_buffer(rows, codes: set[str]):
    """Choose the buffer row that applies to the crop; fall back to the strictest one."""
    if not rows:
        return None
    match = [r for r in rows if set(json.loads(r["crop_codes"])) & codes]
    if match:
        return match[0]
    generic = [r for r in rows if not json.loads(r["crop_codes"])]
    if generic:
        return generic[0]

    def strictness(r):
        v = r["no_reduction"]
        return 10_000 if v == "NELZE" else (float(v) if v.replace(".", "").isdigit() else 0)
    return max(rows, key=strictness)


def _distance(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    return "nelze" if v.upper() == "NELZE" else f"{v} m"


def _cz(x: float) -> str:
    """Czech number format: 0.35 -> 0,35."""
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _metres(v: str | None) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _water_area(field_ctx: dict, distance: float) -> float:
    """Field area (ha) closer than `distance` to surface water; cached per distance."""
    cache = field_ctx.setdefault("_area_cache", {})
    if distance not in cache:
        w = field_ctx.get("water") or {}
        g = field_ctx.get("_field_geom")
        cache[distance] = 0.0 if g is None else lpis.area_within(g, w.get("_union"), distance)
    return cache[distance]


def _water_checks(add, water, field_ctx: dict, nozzle: int, col: str, slope_zone: bool) -> None:
    """Compare the product's buffer distance to surface water with the real distance on this field."""
    w = field_ctx.get("water") or {}
    known = "found" in w  # False only if water data were not loaded
    nearest = w.get("min_distance_m")
    tech = f"trysky {nozzle} %" if nozzle else "bez redukce úletu"
    raw = water[col]
    need = _metres(raw)

    if raw and raw.upper() == "NELZE":
        if known and not w.get("found"):
            add("info", f"S technikou {tech} nelze použít v blízkosti vody. Do {lpis.WATER_SEARCH_M} m od pozemku voda není.")
        else:
            better = [f"{n} %: {water[c]} m" for n, c in ((50, "nozzle50"), (75, "nozzle75"), (90, "nozzle90"))
                      if _metres(water[c]) is not None and n > nozzle]
            add("warn", f"S technikou {tech} nelze použít v blízkosti vody (nejbližší voda {_cz(nearest)} m)."
                + (f" S lepšími tryskami: {', '.join(better)}." if better else ""))
    elif need is not None and need > 0:
        if not known:
            add("info", f"Ochranná vzdálenost od povrchové vody: {need:g} m ({tech}).")
        elif not w.get("found") or nearest >= need:
            add("info", f"Ochranná vzdálenost od vody {need:g} m ({tech}) je splněna"
                + (f", nejbližší voda je {_cz(nearest)} m od pozemku." if nearest is not None else "."))
        else:
            area = _water_area(field_ctx, need)
            add("warn", f"Ochranná vzdálenost od vody {need:g} m ({tech}): neaplikujte na pásu podél vody, "
                f"to je {_cz(area)} ha pozemku (nejbližší voda {_cz(nearest)} m).")

    if slope_zone and water["slope_buffer"]:
        sb, vs = water["slope_buffer"], water["slope_vegetation_strip"]
        area = _cz(field_ctx["slope_near_water"]["area_ha"])
        if sb.upper() == "NELZE":
            whole = field_ctx["slope_near_water"].get("share", 0) >= 0.95
            add("block" if whole else "warn", f"Na svažité části pozemku u vody ({area} ha) přípravek použít nelze.")
        else:
            sbm = _metres(sb)
            if known and sbm is not None and (not w.get("found") or nearest >= sbm):
                add("info", f"Svažitá část pozemku u vody: ochranná vzdálenost {sb} m je splněna.")
            else:
                txt = f"Svažitá část pozemku u vody ({area} ha): ochranná vzdálenost {sb} m"
                if sbm is not None and known:
                    txt += f" (týká se {_cz(_water_area(field_ctx, sbm))} ha)"
                if vs and vs.upper() != "NELZE":
                    txt += f", nebo vegetační pás {vs} m"
                add("warn", txt + ".")


def _merge_by_product(decisions: list[dict], order: dict) -> list[dict]:
    """One product can have several registry decisions (e.g. extensions of use); show it once."""
    merged: dict[str, dict] = {}
    for d in decisions:
        m = merged.get(d["product"])
        if m is None:
            merged[d["product"]] = {**d, "reg_number": d["reg_number"], "checks": list(d["checks"]),
                                    "uses": list(d["uses"]), "types": list(d["types"]), "documents": list(d["documents"])}
            continue
        if d["reg_number"] not in m["reg_number"].split(", "):
            m["reg_number"] += ", " + d["reg_number"]
        if order[d["level"]] > order[m["level"]]:
            m["level"] = d["level"]
        for c in d["checks"]:
            if c not in m["checks"]:
                m["checks"].append(c)
        for u in d["uses"]:
            if u not in m["uses"]:
                m["uses"].append(u)
        for t in d["types"]:
            if t not in m["types"]:
                m["types"].append(t)
        for x in d["documents"]:
            if x not in m["documents"]:
                m["documents"].append(x)
        m["authorized_until"] = max(m["authorized_until"] or "", d["authorized_until"] or "")
        m["usable_until"] = max(m["usable_until"] or "", d["usable_until"] or "")
    return list(merged.values())


def evaluate(con, field_ctx: dict, crop: str, nozzle: int = 0, today: date | None = None,
             owned: dict | set | None = None, area_ha: float | None = None) -> dict:
    """`owned`: products the user has in stock ({name: {"price", "price_unit"}} or a set of names);
    these are marked and listed first. With a price, each use gets its cost per hectare and,
    with `area_ha`, for the whole field."""
    today = today or date.today()
    iso = today.isoformat()
    row = con.execute("SELECT codes FROM crops WHERE name=?", (crop,)).fetchone()
    if row is None:
        raise ValueError(f"Neznámá plodina: {crop}")
    codes = set(json.loads(row["codes"]))
    ph = ",".join("?" * len(codes))
    uses = con.execute(
        f"""SELECT DISTINCT u.*, d.product, d.reg_number FROM uses u JOIN decisions d ON d.id=u.decision_id
            WHERE d.usable_until >= ? AND (u.id IN (SELECT use_id FROM use_crops WHERE code IN ({ph}))
                                         OR (',' || u.crop_text || ',') LIKE ?)
            ORDER BY d.product""",
        (iso, *codes, f"%{crop}%")).fetchall()

    by_dec: dict[int, list] = {}
    for u in uses:
        by_dec.setdefault(u["decision_id"], []).append(u)

    slope_zone = field_ctx["slope_near_water"]["intersects"]
    opvz1 = field_ctx["opvz1_buffer"]["intersects"]
    apiaries = field_ctx["apiaries"]["count"]
    col = NOZZLE_COL.get(nozzle, "no_reduction")

    products = []
    for dec_id, dec_uses in by_dec.items():
        d = con.execute("SELECT * FROM decisions WHERE id=?", (dec_id,)).fetchone()
        bufs = con.execute("SELECT * FROM buffers WHERE decision_id=?", (dec_id,)).fetchall()
        water = _pick_buffer([b for b in bufs if b["domain"] == "VA35"], codes)
        plants = _pick_buffer([b for b in bufs if b["domain"] == "VA34"], codes)
        arthropods = _pick_buffer([b for b in bufs if b["domain"] == "VA21"], codes)
        checks = []

        def add(level, text):
            checks.append({"level": level, "text": text})

        if d["authorized_until"] and d["authorized_until"] < iso:
            add("warn", f"Povolení skončilo {d['authorized_until']}, zásoby lze spotřebovat do {d['usable_until']}.")

        if water is not None:
            _water_checks(add, water, field_ctx, nozzle, col, slope_zone)
        if plants is not None and _distance(plants[col]) not in (None, "0 m"):
            add("info", f"Ochranná vzdálenost od okraje pozemku (necílové rostliny): {_distance(plants[col])}.")
        if arthropods is not None and _distance(arthropods[col]) not in (None, "0 m"):
            add("info", f"Ochranná vzdálenost (necíloví členovci): {_distance(arthropods[col])}.")

        if opvz1:
            add("warn", "Pozemek zasahuje do 100m okolí ochranného pásma vodního zdroje I. stupně. Ověřte hranici pásma.")
        if d["water_protection_zone"]:
            add("info", d["water_protection_zone"].rstrip(".") + ". (OPVZ II. stupně zatím neověřujeme.)")

        bees = json.loads(d["bees"])
        if any(code in bees["code"] for code in BEE_DANGER):
            lvl = "warn" if apiaries else "info"
            txt = f"Nebezpečný pro včely ({bees['code']})."
            if apiaries:
                txt += f" V okruhu {field_ctx['apiaries']['radius_m'] // 1000} km je {apiaries} registrovaných stanovišť včelstev."
            add(lvl, txt)
        if d["groundwater"]:
            add("info", d["groundwater"])

        level = "block" if any(c["level"] == "block" for c in checks) else (
            "warn" if any(c["level"] == "warn" for c in checks) else "ok")
        products.append({
            "decision_id": dec_id,
            "product": d["product"],
            "reg_number": d["reg_number"],
            "function": d["function"],
            "types": product_types(d["function"]),
            "active_substances": json.loads(d["active_substances"]),
            "authorized_until": d["authorized_until"],
            "usable_until": d["usable_until"],
            "level": level,
            "checks": checks,
            "uses": [{
                "crop": u["crop_text"], "target": u["target"], "targets": split_targets(u["target"]), "dose": u["dose_text"], "phi": u["phi"],
                "bbch_from": u["bbch_from"], "bbch_to": u["bbch_to"],
                "max_applications": u["max_applications"], "note": u["note"],
                "dose_min": u["dose_min"], "dose_max": u["dose_max"], "dose_unit": u["dose_unit"],
            } for u in dec_uses],
            "documents": [x for x in json.loads(d["documents"]) if x["type"].startswith("Další informace")],
        })

    order = {"ok": 0, "warn": 1, "block": 2}
    products = _merge_by_product(products, order)
    owned = owned or set()
    for p in products:
        p["owned"] = p["product"] in owned
        info = owned.get(p["product"]) if isinstance(owned, dict) else None
        p["price"] = info["price"] if info else None
        p["price_unit"] = info["price_unit"] if info else ""
        for u in p["uses"]:
            c = prices.use_cost(u, p["price"], p["price_unit"])
            if c and "min" in c and area_ha:
                c["field_min"], c["field_max"] = round(c["min"] * area_ha), round(c["max"] * area_ha)
            u["cost"] = c
    products.sort(key=lambda p: (not p["owned"], order[p["level"]], p["product"].lower()))
    return {
        "crop": crop,
        "nozzle": nozzle,
        "date": iso,
        "registry_date": registry_date(con),
        "counts": {k: sum(1 for p in products if p["level"] == k) for k in order},
        "area_ha": area_ha,
        "owned_counts": {k: sum(1 for p in products if p["level"] == k and p["owned"]) for k in order},
        "products": products,
        "types": _type_counts(products),
    }


def _type_counts(products: list[dict]) -> list[dict]:
    """Types present in the result with product counts, most common first."""
    n: dict[str, int] = {}
    for p in products:
        for t in p["types"]:
            n[t] = n.get(t, 0) + 1
    return [{"name": k, "count": v} for k, v in sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))]
