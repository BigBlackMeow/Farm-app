"""Import the Czech plant protection product registry into SQLite.

Source: public MZe web service EPH_GCP01H ("Číselník přípravků - verze H"),
free, no login. Run:  python -m app.registry_import
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import httpx

ENDPOINT = "https://epomze.gov.cz/ssl/nosso-app/EPO/WS/v2Online/vOKOsrv.ashx"
NS = "{http://www.tsoft.cz/EPH_GCP01H}"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "registry.sqlite"

# Registry regimes we treat as plant protection products (01 = POR povolené ÚKZÚZ, 03 = základní látky)
INCLUDED_RPRK = {"01", "03"}

REQUEST_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<Request xmlns="http://www.pds.eu/vOKO/v0200" vOKOid="EPH_GCP01H">
<RequestHeader><Subject subjectID="0"/><RequestType>EPH_GCP01H</RequestType><RequestID>farm-app</RequestID></RequestHeader>
<RequestContent><REQUEST xmlns="http://www.tsoft.cz/EPH_GCP01H"><GETDATA>{getdata}</GETDATA><IDLAST>{idlast}</IDLAST><PLATNE_A_ZASOBY>1</PLATNE_A_ZASOBY></REQUEST></RequestContent>
</Request></soap:Body></soap:Envelope>"""


def _t(e, path: str) -> str:
    if e is None:
        return ""
    return (e.findtext(path) or "").strip()


def _strip_ns(e):
    for x in e.iter():
        if isinstance(x.tag, str):
            x.tag = x.tag.replace(NS, "")
    return e


def fetch_pages(client: httpx.Client):
    """Yield POLOZKA elements from all pages of the service."""
    idlast = 0
    while True:
        body = REQUEST_TMPL.format(getdata=1, idlast=idlast)
        r = client.post(ENDPOINT, content=body.encode("utf-8"),
                        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "process"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        resp = root.find(f".//{NS}RESPONSE")
        if resp is None:
            raise RuntimeError("Unexpected response: " + r.text[:500])
        items = resp.findall(f"{NS}POLOZKA")
        for it in items:
            yield _strip_ns(it)
        new_last = resp.findtext(f"{NS}IDLAST")
        print(f"  page from IDLAST={idlast}: {len(items)} items", file=sys.stderr)
        if not items or not new_last or int(new_last) == idlast:
            return
        idlast = int(new_last)


def fetch_last_change(client: httpx.Client) -> str:
    r = client.post(ENDPOINT, content=REQUEST_TMPL.format(getdata=0, idlast=0).encode(),
                    headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "process"})
    r.raise_for_status()
    return (ET.fromstring(r.content).findtext(f".//{NS}DATZMENYCIS") or "").strip()


SCHEMA = """
DROP TABLE IF EXISTS meta; DROP TABLE IF EXISTS decisions; DROP TABLE IF EXISTS uses;
DROP TABLE IF EXISTS use_crops; DROP TABLE IF EXISTS buffers; DROP TABLE IF EXISTS crops;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product TEXT, trade_names TEXT, reg_number TEXT, function TEXT, holder TEXT,
  authorized_until TEXT, usable_until TEXT, professional TEXT,
  active_substances TEXT,          -- JSON list of {name, amount, unit}
  water_protection_zone TEXT,      -- VA02 note (OP II. st.), '' if none
  bees TEXT,                       -- JSON {code, note}
  groundwater TEXT,                -- VA48 note (SPe1/SPe2), '' if none
  aquatic TEXT,                    -- VA35 note
  nontarget_plants TEXT,           -- VA34 note
  documents TEXT                   -- JSON list of {type, url}
);
CREATE TABLE uses(
  id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER,
  crop_text TEXT, target TEXT, dose_text TEXT, dose_min REAL, dose_max REAL, dose_unit TEXT,
  water_min REAL, water_max REAL, water_unit TEXT,
  phi TEXT, note TEXT, bbch_from INTEGER, bbch_to INTEGER, max_applications INTEGER, minor_use INTEGER
);
CREATE TABLE use_crops(use_id INTEGER, code TEXT, kind TEXT);
CREATE TABLE buffers(
  decision_id INTEGER, domain TEXT, crops_text TEXT, crop_codes TEXT,
  no_reduction TEXT, nozzle50 TEXT, nozzle75 TEXT, nozzle90 TEXT,
  slope_buffer TEXT, slope_vegetation_strip TEXT
);
CREATE TABLE crops(name TEXT PRIMARY KEY, codes TEXT, n_uses INTEGER);
CREATE INDEX uc_code ON use_crops(code); CREATE INDEX uc_use ON use_crops(use_id);
CREATE INDEX uses_dec ON uses(decision_id); CREATE INDEX buf_dec ON buffers(decision_id);
"""

BBCH_FROM = re.compile(r"od:\s*(\d{1,2})\s*BBCH")
BBCH_TO = re.compile(r"do:\s*(\d{1,2})\s*BBCH")
BBCH_AT = re.compile(r"ve f\.\s*(\d{1,2})\s*BBCH")
MAX_N = re.compile(r"max\.\s*(\d+)\s*x")


def _num(s: str):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def parse_note(note: str):
    """Extract BBCH window and max number of applications from the semi-structured use note."""
    bf = BBCH_FROM.search(note)
    bt = BBCH_TO.search(note)
    at = BBCH_AT.search(note)
    mx = MAX_N.search(note)
    b_from = int(bf.group(1)) if bf else (int(at.group(1)) if at else None)
    b_to = int(bt.group(1)) if bt else (int(at.group(1)) if at else None)
    return b_from, b_to, (int(mx.group(1)) if mx else None)


def split_crop_names(text: str) -> set[str]:
    text = re.sub(r"\s+", " ", text)
    return {n.split(" - ")[0].strip(" .") for n in re.split(r",|;", text) if n.strip(" .")}


def import_registry(db_path: Path = DB_PATH) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    crop_names_per_use: list[tuple[set[str], set[str]]] = []
    n_dec = 0
    with httpx.Client(timeout=300) as client:
        last_change = fetch_last_change(client)
        for it in fetch_pages(client):
            product = _t(it, "OJP")
            usable_until = _t(it, "D_POUZITELNY_DO")[:10]
            for r in it.findall("ROZHODNUTI"):
                if _t(r, "RPRK") not in INCLUDED_RPRK:
                    continue
                udaje = r.findall("UDAJE")

                def note_of(domain):
                    return " ".join(_t(u, "POZNAMKA") for u in udaje
                                    if u.get("domena") == domain and _t(u, "POZNAMKA")).strip()

                bees = [(_t(u, "ZKR"), _t(u, "POZNAMKA")) for u in udaje if u.get("domena") == "VA04"]
                bees_code = ", ".join(c for c, _ in bees if c and c not in ("- -", "-"))
                ozv = note_of("VA02") if any(_t(u, "ZKR").startswith("OP II") for u in udaje
                                               if u.get("domena") == "VA02") else ""
                cur = con.execute(
                    "INSERT INTO decisions(product,trade_names,reg_number,function,holder,authorized_until,"
                    "usable_until,professional,active_substances,water_protection_zone,bees,groundwater,aquatic,"
                    "nontarget_plants,documents) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (product,
                     ", ".join(sorted({_t(o, "OJP_NAZEV") for o in r.iter("OJP") if _t(o, "OJP_NAZEV")})),
                     _t(r, "REG_CISLO"), _t(r, "B_FCE"), _t(r, "DRZ_ROZH"),
                     _t(r, "D_PLATNE_DO")[:10], usable_until, _t(r, "RPRA_PROF"),
                     json.dumps([{"name": _t(u, "NAZEV"), "amount": _t(u, "MNOZSTVI"), "unit": _t(u, "MJ")}
                                 for u in r.findall("UL")], ensure_ascii=False),
                     ozv,
                     json.dumps({"code": bees_code, "note": " ".join(n for _, n in bees if n)}, ensure_ascii=False),
                     note_of("VA48"), note_of("VA35"), note_of("VA34"),
                     json.dumps([{"type": _t(p, "TYP_POPIS"), "url": _t(p, "URL")} for p in r.findall("PRILOHY")],
                                ensure_ascii=False)))
                dec_id = cur.lastrowid
                n_dec += 1
                for u in udaje:
                    for op in u.findall("OCHRANNA_PASMA"):
                        sv = op.find("SVAH")
                        con.execute(
                            "INSERT INTO buffers VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (dec_id, u.get("domena"), _t(op, "PLODINY_TEXT"),
                             json.dumps(sorted({_t(p, "KOD") for p in op.findall("PLODINY")})),
                             _t(op, "BEZ_REDUKCE"), _t(op, "TRYSKA_50"), _t(op, "TRYSKA_75"), _t(op, "TRYSKA_90"),
                             _t(sv, "OP_SVAH") if sv is not None else None,
                             _t(sv, "VEG_PAS_SVAH") if sv is not None else None))
                for p in r.findall("POUZITI"):
                    note = _t(p, "POZNAMKA")
                    b_from, b_to, mx = parse_note(note)
                    d = p.find("DAVKOVANI")
                    cur = con.execute(
                        "INSERT INTO uses(decision_id,crop_text,target,dose_text,dose_min,dose_max,dose_unit,"
                        "water_min,water_max,water_unit,phi,note,bbch_from,bbch_to,max_applications,minor_use)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (dec_id, _t(p, "PLODINA"), _t(p, "SO"), _t(p, "DAVKA"),
                         _num(_t(d, "MIN_DAVKA")), _num(_t(d, "MAX_DAVKA")), _t(d, "MJ"),
                         _num(_t(d, "MIN_DAVKA_VODA")), _num(_t(d, "MAX_DAVKA_VODA")), _t(d, "MJ_VODY"),
                         _t(p, "OL"), note, b_from, b_to, mx, 1 if _t(p, "LA") == "true" else 0))
                    codes = set()
                    for pl in p.findall("PLODINY"):
                        if _t(pl, "SHODA") == "false":
                            continue
                        con.execute("INSERT INTO use_crops VALUES (?,?,?)", (cur.lastrowid, _t(pl, "KOD"), _t(pl, "TYP")))
                        if _t(pl, "TYP") == "PL":
                            codes.add(_t(pl, "KOD"))
                    crop_names_per_use.append((codes, split_crop_names(_t(p, "PLODINA"))))
    n_crops = build_crops(con, crop_names_per_use)
    con.executemany("INSERT INTO meta VALUES (?,?)", [("registry_last_change", last_change)])
    con.commit()
    con.close()
    tmp.replace(db_path)
    return {"decisions": n_dec, "crops": n_crops, "registry_last_change": last_change}


def build_crops(con, uses: list[tuple[set[str], set[str]]], min_uses: int = 5) -> int:
    """Infer which registry crop codes stand for each crop name.

    The public service has crop codes without a codebook. A code that appears in at least
    90 % of the uses whose text names a crop is a candidate for that crop. A candidate shared
    with a sibling crop (e.g. "pšenice jarní" for "pšenice ozimá") is a group code and is dropped,
    unless it is the crop's only candidate. A generic parent name ("pšenice") is not a sibling.
    """
    name_count = Counter(n for _, names in uses for n in names)
    candidates: dict[str, set[str]] = {}
    for name, cnt in name_count.items():
        if cnt < min_uses or "(" in name or ")" in name or len(name) > 40:
            continue
        code_count = Counter(c for codes, names in uses if name in names for c in codes)
        cand = {c for c, k in code_count.items() if k / cnt >= 0.9}
        if cand:
            candidates[name] = cand
    by_code: dict[str, set[str]] = {}
    for name, cand in candidates.items():
        for c in cand:
            by_code.setdefault(c, set()).add(name)

    def sibling(a: str, b: str) -> bool:
        # same crop genus (first word), but neither is the generic form of the other
        return a.split()[0] == b.split()[0] and not (a.startswith(b) or b.startswith(a))

    n = 0
    for name, cand in candidates.items():
        own = {c for c in cand if not any(sibling(name, m) for m in by_code[c] if m != name)}
        codes = sorted(own or cand)
        con.execute("INSERT INTO crops VALUES (?,?,?)", (name, json.dumps(codes), name_count[name]))
        n += 1
    return n


def rebuild_crops(db_path: Path = DB_PATH) -> int:
    """Recompute the crops table from an existing database (no download)."""
    con = sqlite3.connect(db_path)
    uses = {}
    for use_id, text in con.execute("SELECT id, crop_text FROM uses"):
        uses[use_id] = (set(), split_crop_names(text))
    for use_id, code in con.execute("SELECT use_id, code FROM use_crops WHERE kind='PL'"):
        uses[use_id][0].add(code)
    con.execute("DELETE FROM crops")
    n = build_crops(con, list(uses.values()))
    con.commit()
    con.close()
    return n


if __name__ == "__main__":
    if "--crops-only" in sys.argv:
        print({"crops": rebuild_crops()})
    else:
        try:
            print(import_registry())
        except (httpx.HTTPError, RuntimeError, ET.ParseError) as e:
            print("\nNepodařilo se stáhnout registr přípravků z MZe (eAGRI).", file=sys.stderr)
            print("Služba je asi dočasně nedostupná (výpadek nebo plánovaná údržba). Zkuste to později.", file=sys.stderr)
            print("Vaše dosavadní data zůstala beze změny, aplikace s nimi dál funguje.", file=sys.stderr)
            print(f"(technický detail: {type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
            sys.exit(1)
