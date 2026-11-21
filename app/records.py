"""Spray records ("evidence postřiků"): the farm's identification and one record per product application.

Stored in the same SQLite file as the accounts (data/users.sqlite), separately for each user.

The fields follow § 11 odst. 1 vyhlášky č. 132/2018 Sb. (ve znění vyhlášky č. 200/2023 Sb.), which
implements § 60 zákona č. 326/2004 Sb., plus what Implementing Regulation (EU) 2023/564 adds
(registration number of the product, BBCH stage and time of application where relevant).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timezone

from .config import APP_NAME
from .prices import price_unit_for, record_cost

SCHEMA = """
CREATE TABLE IF NOT EXISTS farm_profile (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    ico TEXT NOT NULL DEFAULT '',
    birth_date TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS spray_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    app_date TEXT NOT NULL,
    app_time TEXT NOT NULL DEFAULT '',
    square TEXT NOT NULL DEFAULT '',
    field_code TEXT NOT NULL DEFAULT '',
    parcel TEXT NOT NULL DEFAULT '',
    cadastral TEXT NOT NULL DEFAULT '',
    crop TEXT NOT NULL,
    crop_area_ha REAL,
    sowing_date TEXT NOT NULL DEFAULT '',
    crop_end_date TEXT NOT NULL DEFAULT '',
    treated_area_ha REAL NOT NULL,
    product TEXT NOT NULL,
    reg_number TEXT NOT NULL DEFAULT '',
    dose REAL NOT NULL,
    dose_unit TEXT NOT NULL,
    target TEXT NOT NULL,
    tank_mix TEXT NOT NULL DEFAULT 'S',
    mix_with TEXT NOT NULL DEFAULT '',
    bbch TEXT NOT NULL DEFAULT '',
    efficacy TEXT NOT NULL DEFAULT '',
    water_l_ha REAL,
    nozzle INTEGER,
    applicator TEXT NOT NULL DEFAULT '',
    sprayer TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS spray_records_user ON spray_records(user_id, app_date);
"""

DOSE_UNITS = ["l/ha", "ml/ha", "kg/ha", "g/ha", "l/t", "ml/t", "kg/t", "g/t", "l/m3", "ml/m3", "kg/m3", "g/m3"]
TEXT_FIELDS = ["app_date", "app_time", "square", "field_code", "parcel", "cadastral", "crop", "sowing_date",
               "crop_end_date", "product", "reg_number", "dose_unit", "target", "tank_mix", "mix_with", "bbch",
               "efficacy", "applicator", "sprayer", "note"]
NUM_FIELDS = ["crop_area_ha", "treated_area_ha", "dose", "water_l_ha", "nozzle", "price"]
FIELDS = TEXT_FIELDS + NUM_FIELDS
PROFILE_FIELDS = ["name", "ico", "birth_date", "address"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RecordError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(v: str, label: str, required: bool = False) -> str:
    v = (v or "").strip()
    if not v:
        if required:
            raise RecordError(f"Vyplňte {label}.")
        return ""
    if not DATE_RE.match(v):
        raise RecordError(f"{label[0].upper() + label[1:]}: neplatné datum.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise RecordError(f"{label[0].upper() + label[1:]}: neplatné datum.")
    return v


def _number(v, label: str, required: bool = False, positive: bool = True) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise RecordError(f"Vyplňte {label}.")
        return None
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
    except ValueError:
        raise RecordError(f"{label[0].upper() + label[1:]}: zadejte číslo.")
    if positive and x <= 0:
        raise RecordError(f"{label[0].upper() + label[1:]} musí být větší než 0.")
    return x


def clean(data: dict) -> dict:
    """Validate a record sent by the browser; return the values to store."""
    r = {k: str(data.get(k) or "").strip()[:500] for k in TEXT_FIELDS}
    r["app_date"] = _date(r["app_date"], "datum aplikace", required=True)
    if r["app_date"] > date.today().isoformat():
        raise RecordError("Datum aplikace nemůže být v budoucnosti.")
    if r["app_time"] and not re.match(r"^\d{1,2}:\d{2}$", r["app_time"]):
        raise RecordError("Čas aplikace zadejte jako HH:MM.")
    r["sowing_date"] = _date(r["sowing_date"], "datum výsevu")
    r["crop_end_date"] = _date(r["crop_end_date"], "datum ukončení pěstování")
    if not ((r["square"] and r["field_code"]) or r["parcel"]):
        raise RecordError("Zadejte pozemek: čtverec a kód dílu z LPIS, nebo parcelní číslo.")
    for key, label in [("crop", "plodinu"), ("product", "přípravek"), ("target", "škodlivý organismus nebo účel")]:
        if not r[key]:
            raise RecordError(f"Vyplňte {label}.")
    if r["dose_unit"] not in DOSE_UNITS:
        raise RecordError("Vyberte jednotku dávky.")
    if r["tank_mix"] not in ("S", "TM"):
        r["tank_mix"] = "S"
    if r["efficacy"] not in ("", "1", "0"):
        r["efficacy"] = ""
    r["treated_area_ha"] = round(_number(data.get("treated_area_ha"), "ošetřenou výměru", required=True), 2)
    crop_area = _number(data.get("crop_area_ha"), "výměru plodiny")
    r["crop_area_ha"] = round(crop_area, 2) if crop_area is not None else None
    r["dose"] = round(_number(data.get("dose"), "dávku", required=True), 3)
    r["water_l_ha"] = _number(data.get("water_l_ha"), "množství vody")
    price = _number(data.get("price"), "cenu přípravku")
    r["price"] = round(price, 2) if price is not None else None
    nozzle = data.get("nozzle")
    r["nozzle"] = int(nozzle) if str(nozzle) in ("0", "50", "75", "90") else None
    return r


def total_amount(rec: dict) -> str:
    """Total amount of product applied: dose x treated area (only for per-hectare units)."""
    unit = rec["dose_unit"]
    if not unit.endswith("/ha"):
        return ""
    total = rec["dose"] * rec["treated_area_ha"]
    base = unit.split("/")[0]
    if base == "ml" and total >= 1000:
        total, base = total / 1000, "l"
    elif base == "g" and total >= 1000:
        total, base = total / 1000, "kg"
    return f"{cz(round(total, 3))} {base}"


def cz(x) -> str:
    """Number with a decimal comma, without trailing zeros."""
    if x is None or x == "":
        return ""
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def cz_date(v: str) -> str:
    return f"{int(v[8:10])}. {int(v[5:7])}. {v[:4]}" if v and DATE_RE.match(v) else ""


def missing(rec: dict, profile: dict) -> list[str]:
    """What still has to be filled in for a complete legal record."""
    out = []
    if not (profile.get("name") and (profile.get("ico") or profile.get("birth_date")) and profile.get("address")):
        out.append("údaje o podniku")
    if not rec["cadastral"]:
        out.append("katastrální území")
    if rec["crop_area_ha"] is None:
        out.append("výměra plodiny")
    if not rec["sowing_date"]:
        out.append("datum výsevu")
    if not rec["crop_end_date"]:
        out.append("datum sklizně")
    if not rec["efficacy"]:
        out.append("účinnost")
    return out


class Records:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.con.executescript(SCHEMA)
            cols = {r[1] for r in store.con.execute("PRAGMA table_info(spray_records)")}
            if "price" not in cols:  # Kč per l or kg of the product; added after the first version
                store.con.execute("ALTER TABLE spray_records ADD COLUMN price REAL")

    def _exec(self, sql: str, args: tuple = ()):
        return self.store._exec(sql, args)

    # --- farm identification -----------------------------------------------------------------
    def profile(self, user_id: int) -> dict:
        rows = self._exec(f"SELECT {', '.join(PROFILE_FIELDS)} FROM farm_profile WHERE user_id=?", (user_id,))
        return dict(zip(PROFILE_FIELDS, rows[0])) if rows else {k: "" for k in PROFILE_FIELDS}

    def save_profile(self, user_id: int, data: dict) -> dict:
        p = {k: str(data.get(k) or "").strip()[:300] for k in PROFILE_FIELDS}
        p["birth_date"] = _date(p["birth_date"], "datum narození")
        if p["ico"] and not re.match(r"^\d{8}$", p["ico"].replace(" ", "")):
            raise RecordError("IČO má 8 číslic.")
        p["ico"] = p["ico"].replace(" ", "")
        self._exec("INSERT OR REPLACE INTO farm_profile (user_id, name, ico, birth_date, address) VALUES (?, ?, ?, ?, ?)",
                   (user_id, p["name"], p["ico"], p["birth_date"], p["address"]))
        return p

    # --- records -------------------------------------------------------------------------------
    def _rows(self, where: str, args: tuple) -> list[dict]:
        rows = self._exec(f"SELECT id, created_at, updated_at, {', '.join(FIELDS)} FROM spray_records WHERE {where} "
                          "ORDER BY app_date DESC, app_time DESC, id DESC", args)
        keys = ["id", "created_at", "updated_at", *FIELDS]
        return [dict(zip(keys, r)) for r in rows]

    def list(self, user_id: int, date_from: str = "", date_to: str = "") -> list[dict]:
        where, args = "user_id=?", [user_id]
        if date_from:
            where += " AND app_date>=?"
            args.append(date_from)
        if date_to:
            where += " AND app_date<=?"
            args.append(date_to)
        profile = self.profile(user_id)
        out = self._rows(where, tuple(args))
        for r in out:
            r["total_amount"] = total_amount(r)
            r["price_unit"] = price_unit_for(r["dose_unit"])
            r["cost"] = record_cost(r["dose"], r["dose_unit"], r["treated_area_ha"], r["price"])
            r["missing"] = missing(r, profile)
        return out

    def get(self, user_id: int, rec_id: int) -> dict:
        rows = self._rows("user_id=? AND id=?", (user_id, rec_id))
        if not rows:
            raise KeyError(rec_id)
        return rows[0]

    def add(self, user_id: int, data: dict) -> dict:
        r = clean(data)
        now = _now()
        with self.store.lock:
            cur = self.store.con.execute(
                f"INSERT INTO spray_records (user_id, created_at, updated_at, {', '.join(FIELDS)}) "
                f"VALUES (?, ?, ?, {', '.join('?' * len(FIELDS))})",
                (user_id, now, now, *[r[k] for k in FIELDS]))
            rec_id = cur.lastrowid
        return self.get(user_id, rec_id)

    def update(self, user_id: int, rec_id: int, data: dict) -> dict:
        self.get(user_id, rec_id)
        r = clean(data)
        self._exec(f"UPDATE spray_records SET updated_at=?, {', '.join(k + '=?' for k in FIELDS)} WHERE user_id=? AND id=?",
                   (_now(), *[r[k] for k in FIELDS], user_id, rec_id))
        return self.get(user_id, rec_id)

    def delete(self, user_id: int, rec_id: int) -> None:
        self._exec("DELETE FROM spray_records WHERE user_id=? AND id=?", (user_id, rec_id))

    def last_for_field(self, user_id: int, square: str, field_code: str, crop: str) -> dict:
        """Values to prefill from the latest record on the same field (cadastral area, sowing date...)."""
        rows = self._rows("user_id=? AND square=? AND field_code=?", (user_id, square, field_code))
        if not rows:
            return {}
        r = rows[0]
        out = {"cadastral": r["cadastral"]}
        same_crop = [x for x in rows if x["crop"] == crop]
        if same_crop:
            out.update(crop_area_ha=same_crop[0]["crop_area_ha"], sowing_date=same_crop[0]["sowing_date"])
        return out


# --- export -----------------------------------------------------------------------------------
CSV_COLUMNS = [
    ("Datum aplikace", lambda r: cz_date(r["app_date"])),
    ("Čas", lambda r: r["app_time"]),
    ("Katastrální území", lambda r: r["cadastral"]),
    ("Čtverec DPB", lambda r: r["square"]),
    ("Zkrácený kód DPB", lambda r: r["field_code"]),
    ("Parcelní číslo", lambda r: r["parcel"]),
    ("Plodina", lambda r: r["crop"]),
    ("Výměra plodiny (ha)", lambda r: cz(r["crop_area_ha"])),
    ("Datum výsevu", lambda r: cz_date(r["sowing_date"])),
    ("Ošetřená výměra (ha)", lambda r: cz(r["treated_area_ha"])),
    ("Přípravek", lambda r: r["product"]),
    ("Evidenční číslo", lambda r: r["reg_number"]),
    ("Dávka na jednotku", lambda r: cz(r["dose"])),
    ("Jednotka", lambda r: r["dose_unit"]),
    ("Celkové množství", lambda r: total_amount(r)),
    ("Škodlivý organismus / účel", lambda r: r["target"]),
    ("Směs (S/TM)", lambda r: r["tank_mix"]),
    ("Ve směsi s", lambda r: r["mix_with"]),
    ("BBCH", lambda r: r["bbch"]),
    ("Účinnost (1/0)", lambda r: r["efficacy"]),
    ("Datum ukončení pěstování", lambda r: cz_date(r["crop_end_date"])),
    ("Voda (l/ha)", lambda r: cz(r["water_l_ha"])),
    ("Redukce úletu trysek (%)", lambda r: "" if r["nozzle"] is None else str(r["nozzle"])),
    ("Aplikoval", lambda r: r["applicator"]),
    ("Postřikovač", lambda r: r["sprayer"]),
    ("Poznámka", lambda r: r["note"]),
    ("Cena přípravku (Kč za l nebo kg)", lambda r: cz(r["price"])),
    ("Náklady na přípravek (Kč)", lambda r: cz(record_cost(r["dose"], r["dose_unit"], r["treated_area_ha"], r["price"]))),
]


def to_csv(records: list[dict], profile: dict) -> str:
    """CSV for Excel (semicolons, decimal commas). The farm identification is in the first lines."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Záznamy o použití přípravků na ochranu rostlin"])
    w.writerow(["Subjekt", profile.get("name", ""), "IČO", profile.get("ico", ""),
                "Datum narození", cz_date(profile.get("birth_date", "")), "Adresa", profile.get("address", "")])
    w.writerow([])
    w.writerow([c for c, _ in CSV_COLUMNS])
    for r in sorted(records, key=lambda r: (r["app_date"], r["app_time"], r["id"])):
        w.writerow([f(r) for _, f in CSV_COLUMNS])
    return "﻿" + buf.getvalue()


def _e(s) -> str:
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def to_print_html(records: list[dict], profile: dict, period: str) -> str:
    """A page for printing or saving as PDF (Ctrl+P -> Uložit jako PDF), A4 landscape."""
    ident = profile.get("name") or "(doplňte název nebo jméno v Evidenci → Údaje o podniku)"
    ident_line = " · ".join(x for x in [
        f"IČO {profile['ico']}" if profile.get("ico") else "",
        f"nar. {cz_date(profile['birth_date'])}" if profile.get("birth_date") else "",
        profile.get("address", "")] if x)
    rows = []
    for r in sorted(records, key=lambda r: (r["app_date"], r["app_time"], r["id"])):
        place = f"{_e(r['field_code'])} / {_e(r['square'])}" if r["square"] else f"p. č. {_e(r['parcel'])}"
        crop = _e(r["crop"])
        extra = ", ".join(x for x in [f"{cz(r['crop_area_ha'])} ha" if r["crop_area_ha"] is not None else "",
                                      f"výsev {cz_date(r['sowing_date'])}" if r["sowing_date"] else ""] if x)
        mix = "TM" + (f" ({_e(r['mix_with'])})" if r["mix_with"] else "") if r["tank_mix"] == "TM" else "S"
        rows.append(f"""<tr>
<td>{cz_date(r['app_date'])}{('<br>' + _e(r['app_time'])) if r['app_time'] else ''}</td>
<td>{_e(r['cadastral'])}</td><td>{place}</td>
<td>{crop}{('<br><small>' + extra + '</small>') if extra else ''}</td>
<td class="n">{cz(r['treated_area_ha'])}</td>
<td><b>{_e(r['product'])}</b>{('<br><small>ev. č. ' + _e(r['reg_number']) + '</small>') if r['reg_number'] else ''}</td>
<td class="n">{cz(r['dose'])} {_e(r['dose_unit'])}</td><td class="n">{_e(total_amount(r))}</td>
<td>{_e(r['target'])}</td><td>{mix}</td><td>{_e(r['bbch'])}</td><td>{_e(r['efficacy'])}</td>
<td>{cz_date(r['crop_end_date'])}</td>
<td><small>{_e(", ".join(x for x in [r['applicator'], r['sprayer'], f"trysky {r['nozzle']} %" if r['nozzle'] else "", r['note']] if x))}</small></td>
</tr>""")
    body = "\n".join(rows) or '<tr><td colspan="14">V tomto období nejsou žádné záznamy.</td></tr>'
    printed = cz_date(date.today().isoformat())
    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><title>Evidence postřiků {_e(period)}</title>
<style>
  @page {{ size: A4 landscape; margin: 10mm; }}
  body {{ font: 11px/1.35 system-ui, "Segoe UI", Arial, sans-serif; color: #000; margin: 16px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .ident {{ margin: 0 0 10px; font-size: 12px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ border: 1px solid #999; padding: 3px 4px; vertical-align: top; text-align: left; }}
  th {{ background: #eee; font-weight: 600; font-size: 10px; }}
  td.n {{ text-align: right; white-space: nowrap; }}
  small {{ color: #444; }}
  thead {{ display: table-header-group; }} tr {{ page-break-inside: avoid; }}
  .foot {{ margin-top: 10px; font-size: 10px; color: #444; }}
  .bar {{ margin-bottom: 12px; }} .bar button {{ font: inherit; font-size: 14px; padding: 6px 14px; cursor: pointer; }}
  @media print {{ .bar {{ display: none; }} body {{ margin: 0; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">Vytisknout / uložit jako PDF</button></div>
<h1>Záznamy o použití přípravků na ochranu rostlin</h1>
<p class="ident"><b>{_e(ident)}</b>{(' · ' + _e(ident_line)) if ident_line else ''}<br>Období: {_e(period)} · počet záznamů: {len(records)}</p>
<table><thead><tr>
<th>Datum aplikace</th><th>Katastrální území</th><th>DPB / čtverec</th><th>Plodina (výměra, výsev)</th><th>Ošetřeno ha</th>
<th>Přípravek</th><th>Dávka na jednotku</th><th>Celkem</th><th>Škodlivý organismus / účel</th><th>S / TM</th><th>BBCH</th>
<th>Účinnost (1/0)</th><th>Ukončení pěstování</th><th>Aplikoval, technika, poznámka</th>
</tr></thead><tbody>
{body}
</tbody></table>
<p class="foot">Údaje podle § 11 odst. 1 vyhlášky č. 132/2018 Sb. (ve znění vyhlášky č. 200/2023 Sb.) a nařízení (EU) 2023/564.
Vytištěno {printed} z aplikace {_e(APP_NAME)}.</p>
<script>if (location.search.includes("auto=1")) window.addEventListener("load", () => setTimeout(() => window.print(), 300));</script>
</body></html>"""
