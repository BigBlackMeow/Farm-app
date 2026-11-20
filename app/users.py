"""User accounts, login sessions and each user's list of purchased products ("můj sklad").

Stored in the SQLite file data/users.sqlite. Passwords are hashed with scrypt (Python standard library); the login cookie holds a random token.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.sqlite"
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_products (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, product)
);
"""


class AuthError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, sqlite_path: Path = DB_PATH):
        self.lock = threading.Lock()
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(sqlite_path, check_same_thread=False, isolation_level=None)
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    # --- accounts -----------------------------------------------------------------------------
    @staticmethod
    def _hash(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${h.hex()}"

    @classmethod
    def _verify(cls, password: str, stored: str) -> bool:
        try:
            _, salt, _ = stored.split("$")
        except ValueError:
            return False
        return hmac.compare_digest(cls._hash(password, bytes.fromhex(salt)), stored)

    def register(self, email: str, password: str, name: str = "") -> dict:
        email = email.strip().lower()
        if not EMAIL_RE.match(email):
            raise AuthError("Zadejte platný e-mail.")
        if len(password) < 8:
            raise AuthError("Heslo musí mít aspoň 8 znaků.")
        if self._exec("SELECT id FROM users WHERE email=?", (email,)):
            raise AuthError("Účet s tímto e-mailem už existuje. Přihlaste se.")
        self._exec("INSERT INTO users (email, name, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                   (email, name.strip()[:80], self._hash(password), _now().isoformat()))
        return self.login(email, password)

    def login(self, email: str, password: str) -> dict:
        rows = self._exec("SELECT id, email, name, pw_hash FROM users WHERE email=?", (email.strip().lower(),))
        if not rows or not self._verify(password, rows[0][3]):
            raise AuthError("Nesprávný e-mail nebo heslo.")
        uid, em, name, _ = rows[0]
        token = secrets.token_urlsafe(32)
        self._exec("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                   (token, uid, (_now() + timedelta(days=SESSION_DAYS)).isoformat()))
        return {"token": token, "user": {"id": uid, "email": em, "name": name}}

    def user_for_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        rows = self._exec(
            "SELECT u.id, u.email, u.name, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,))
        if not rows or rows[0][3] < _now().isoformat():
            return None
        uid, em, name, _ = rows[0]
        return {"id": uid, "email": em, "name": name}

    def logout(self, token: str | None) -> None:
        if token:
            self._exec("DELETE FROM sessions WHERE token=?", (token,))

    # --- purchased products -------------------------------------------------------------------
    def products(self, user_id: int) -> list[str]:
        rows = self._exec("SELECT product FROM user_products WHERE user_id=? ORDER BY product", (user_id,))
        return [r[0] for r in rows]

    def add_product(self, user_id: int, product: str) -> None:
        if not self._exec("SELECT 1 FROM user_products WHERE user_id=? AND product=?", (user_id, product)):
            self._exec("INSERT INTO user_products (user_id, product, added_at) VALUES (?, ?, ?)",
                       (user_id, product, _now().isoformat()))

    def remove_product(self, user_id: int, product: str) -> None:
        self._exec("DELETE FROM user_products WHERE user_id=? AND product=?", (user_id, product))
