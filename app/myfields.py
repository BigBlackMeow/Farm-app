"""The user's own fields ("moje pole"): every DPB that LPIS lists under the user's ID JI, with their own names.

ID JI (identifikátor jednotné identity) is the farmer's public identifier in LPIS. The public LPIS WFS
returns it for every DPB, so the fields can be loaded without signing in to eAGRI.

Stored in data/users.sqlite. Names are kept by square + short code, so they survive reloading from LPIS.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import lpis

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_farm_lpis (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    ji TEXT NOT NULL,
    farmer TEXT NOT NULL DEFAULT '',
    loaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_fields (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    square TEXT NOT NULL,
    short_code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    area_ha REAL,
    land_use TEXT NOT NULL DEFAULT '',
    in_lpis INTEGER NOT NULL DEFAULT 1,
    outline TEXT,
    PRIMARY KEY (user_id, square, short_code)
);
"""
JI_RE = re.compile(r"^\d{6,12}$")


class MyFieldsError(Exception):
    pass


class MyFields:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.con.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()):
        return self.store._exec(sql, args)

    def get(self, user_id: int) -> dict:
        farm = self._exec("SELECT ji, farmer, loaded_at FROM user_farm_lpis WHERE user_id=?", (user_id,))
        rows = self._exec("SELECT square, short_code, name, area_ha, land_use, in_lpis, outline FROM user_fields "
                          "WHERE user_id=? ORDER BY name = '', name COLLATE NOCASE, square, short_code", (user_id,))
        fields = [{"square": r[0], "short_code": r[1], "code": f"{r[1]} {r[0]}", "name": r[2], "area_ha": r[3],
                   "land_use": r[4], "in_lpis": bool(r[5]), "geometry": json.loads(r[6]) if r[6] else None}
                  for r in rows]
        ji, farmer, loaded = farm[0] if farm else ("", "", "")
        return {"ji": ji, "farmer": farmer, "loaded_at": loaded, "fields": fields}

    def load(self, user_id: int, ji: str) -> dict:
        """(Re)load all fields of this ID JI from LPIS. Keeps the user's names; fields gone from LPIS stay, marked."""
        ji = re.sub(r"\s", "", ji or "")
        if not JI_RE.match(ji):
            raise MyFieldsError("ID JI je číslo, obvykle desetimístné (např. 1000123456).")
        found = lpis.fields_by_ji(ji)
        if not found:
            raise MyFieldsError("Pod tímto ID JI nemá LPIS žádné účinné díly. Zkontrolujte číslo.")
        farmer = next((f["user"] for f in found if f["user"]), "")
        old = self._exec("SELECT ji FROM user_farm_lpis WHERE user_id=?", (user_id,))
        if old and old[0][0] != ji:  # a different farm: start over
            self._exec("DELETE FROM user_fields WHERE user_id=?", (user_id,))
        now = datetime.now(timezone.utc).isoformat()
        self._exec("INSERT INTO user_farm_lpis (user_id, ji, farmer, loaded_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(user_id) DO UPDATE SET ji=excluded.ji, farmer=excluded.farmer, loaded_at=excluded.loaded_at",
                   (user_id, ji, farmer, now))
        self._exec("UPDATE user_fields SET in_lpis=0 WHERE user_id=?", (user_id,))
        for f in found:
            self._exec(
                "INSERT INTO user_fields (user_id, square, short_code, area_ha, land_use, in_lpis, outline) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(user_id, square, short_code) DO UPDATE SET "
                "area_ha=excluded.area_ha, land_use=excluded.land_use, in_lpis=1, outline=excluded.outline",
                (user_id, f["square"], f["short_code"], f["area_ha"], f["land_use"],
                 json.dumps(lpis.field_outline(f), separators=(",", ":"))))
        # fields no longer in LPIS: keep only those the user named (to keep their names), drop the rest
        self._exec("DELETE FROM user_fields WHERE user_id=? AND in_lpis=0 AND name=''", (user_id,))
        return self.get(user_id)

    def rename(self, user_id: int, square: str, short_code: str, name: str) -> dict:
        name = " ".join((name or "").split())[:60]
        n = self._exec("SELECT 1 FROM user_fields WHERE user_id=? AND square=? AND short_code=?",
                       (user_id, square, short_code))
        if not n:
            raise MyFieldsError("Tento díl mezi vašimi poli není.")
        self._exec("UPDATE user_fields SET name=? WHERE user_id=? AND square=? AND short_code=?",
                   (name, user_id, square, short_code))
        return self.get(user_id)

    def disconnect(self, user_id: int) -> dict:
        self._exec("DELETE FROM user_fields WHERE user_id=?", (user_id,))
        self._exec("DELETE FROM user_farm_lpis WHERE user_id=?", (user_id,))
        return self.get(user_id)
