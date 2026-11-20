"""Support messages ("Podpora"): bug reports, feature ideas and questions from users.

Every message is saved in data/users.sqlite first, so nothing is lost when the e-mail service is not set
up or the internet is down. The browser then sends it by e-mail (Web3Forms) and reports back whether that
worked; unsent messages can be sent again later.
"""
from __future__ import annotations

from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    tech TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    send_error TEXT NOT NULL DEFAULT ''
);
"""
KINDS = {"chyba": "Chyba", "napad": "Nová funkce / nápad", "dotaz": "Dotaz"}
MAX_LEN = {"subject": 200, "message": 10000, "contact": 200, "tech": 4000}


class SupportError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Support:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.con.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()):
        return self.store._exec(sql, args)

    def add(self, user_id: int | None, body: dict) -> dict:
        kind = str(body.get("kind") or "")
        if kind not in KINDS:
            raise SupportError("Vyberte, o co jde.")
        vals = {k: str(body.get(k) or "").strip()[:n] for k, n in MAX_LEN.items()}
        if not vals["message"]:
            raise SupportError("Napište prosím, co se stalo nebo co byste chtěli.")
        if not vals["subject"]:
            first = vals["message"].splitlines()[0]
            vals["subject"] = first[:80] + ("…" if len(first) > 80 else "")
        self._exec("INSERT INTO support_messages (user_id, kind, subject, message, contact, tech, created_at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (user_id, kind, vals["subject"], vals["message"], vals["contact"], vals["tech"], _now()))
        new_id = self._exec("SELECT last_insert_rowid()")[0][0]
        return self.get(new_id)

    def get(self, msg_id: int, user_id: int | None = None) -> dict:
        rows = self._exec("SELECT id, user_id, kind, subject, message, contact, tech, created_at, sent_at, send_error "
                          "FROM support_messages WHERE id=?", (msg_id,))
        if not rows or (user_id is not None and rows[0][1] != user_id):
            raise KeyError(msg_id)
        r = rows[0]
        return {"id": r[0], "kind": r[2], "kind_label": KINDS.get(r[2], r[2]), "subject": r[3], "message": r[4],
                "contact": r[5], "tech": r[6], "created_at": r[7], "sent_at": r[8], "send_error": r[9]}

    def list(self, user_id: int) -> list[dict]:
        ids = self._exec("SELECT id FROM support_messages WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
        return [self.get(i[0]) for i in ids]

    def mark(self, msg_id: int, user_id: int, ok: bool, error: str = "") -> dict:
        self.get(msg_id, user_id)
        if ok:
            self._exec("UPDATE support_messages SET sent_at=?, send_error='' WHERE id=?", (_now(), msg_id))
        else:
            self._exec("UPDATE support_messages SET send_error=? WHERE id=? AND sent_at IS NULL",
                       (error[:300] or "Odeslání se nepovedlo.", msg_id))
        return self.get(msg_id)
