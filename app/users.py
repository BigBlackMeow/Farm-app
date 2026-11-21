"""User accounts, login sessions and each user's list of purchased products ("můj sklad").

Stored in the SQLite file data/users.sqlite.

How login data is kept safe:
- Passwords are never stored. Only a salted scrypt hash is kept (Python standard library), so even someone
  with a copy of the database cannot read the passwords.
- The login cookie holds a random token. The database keeps only its SHA-256 hash, so a copy of the
  database cannot be used to log in as anyone.
- Repeated wrong passwords are slowed down (see LoginGuard).
- Users can change their password, log out on all devices and delete their account with all their data.
  The administrator (see admin_emails in config.py) can delete any account.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "users.sqlite"
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD, MAX_PASSWORD = 8, 200
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SCHEMA_VERSION = 2

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


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str, salt: bytes | None = None, n: int = SCRYPT_N, r: int = SCRYPT_R,
                  p: int = SCRYPT_P) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Checks a password against a stored hash. Understands the old format scrypt$salt$hash too."""
    parts = stored.split("$")
    try:
        if len(parts) == 3:
            _, salt, want = parts
            n, r, p = 2**14, 8, 1
        elif len(parts) == 6:
            _, n, r, p, salt, want = parts
            n, r, p = int(n), int(r), int(p)
        else:
            return False
        got = hash_password(password, bytes.fromhex(salt), n, r, p).split("$")[-1]
    except ValueError:
        return False
    return hmac.compare_digest(got, want)


def _needs_rehash(stored: str) -> bool:
    return stored.split("$")[1:4] != [str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P)]


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Heslo musí mít aspoň {MIN_PASSWORD} znaků.")
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"Heslo může mít nejvýš {MAX_PASSWORD} znaků.")


class LoginGuard:
    """Slows down password guessing: after 5 wrong attempts for one e-mail (or 20 from one address)
    within 15 minutes, further attempts are refused for the rest of those 15 minutes."""

    WINDOW = 15 * 60
    LIMITS = {"email": 5, "ip": 20}

    def __init__(self):
        self.lock = threading.Lock()
        self.fails: dict[str, list[float]] = {}

    def _keys(self, email: str, ip: str) -> list[tuple[str, int]]:
        keys = [("email:" + email, self.LIMITS["email"])]
        if ip:
            keys.append(("ip:" + ip, self.LIMITS["ip"]))
        return keys

    def check(self, email: str, ip: str) -> None:
        now = time.monotonic()
        with self.lock:
            for key, limit in self._keys(email, ip):
                recent = [t for t in self.fails.get(key, []) if now - t < self.WINDOW]
                self.fails[key] = recent
                if len(recent) >= limit:
                    wait = int((self.WINDOW - (now - recent[0])) // 60) + 1
                    raise AuthError(f"Příliš mnoho pokusů o přihlášení. Zkuste to znovu za {wait} min.")

    def failed(self, email: str, ip: str) -> None:
        with self.lock:
            for key, _ in self._keys(email, ip):
                self.fails.setdefault(key, []).append(time.monotonic())

    def succeeded(self, email: str) -> None:
        with self.lock:
            self.fails.pop("email:" + email, None)


class Store:
    def __init__(self, sqlite_path: Path = DB_PATH):
        self.lock = threading.Lock()
        self.guard = LoginGuard()
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(sqlite_path, check_same_thread=False, isolation_level=None)
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.executescript(SCHEMA)
        self._migrate()
        self.cleanup_sessions()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(users)")}
        if "last_login_at" not in cols:
            self.con.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        scols = {r[1] for r in self.con.execute("PRAGMA table_info(sessions)")}
        if "created_at" not in scols:
            self.con.execute("ALTER TABLE sessions ADD COLUMN created_at TEXT")
        version = self.con.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            # Older versions kept the login token itself in the database. Those sessions are dropped;
            # everyone simply logs in once more and from then on only token hashes are stored.
            self.con.execute("DELETE FROM sessions")
        self.con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _exec(self, sql: str, args: tuple = ()):
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    def cleanup_sessions(self) -> None:
        self._exec("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(),))

    # --- accounts -----------------------------------------------------------------------------
    def _new_session(self, uid: int) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self._exec("INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                   (_token_hash(token), uid, (now + timedelta(days=SESSION_DAYS)).isoformat(), now.isoformat()))
        self._exec("UPDATE users SET last_login_at=? WHERE id=?", (now.isoformat(), uid))
        return token

    def _public(self, uid: int, email: str, name: str) -> dict:
        return {"id": uid, "email": email, "name": name, "is_admin": self.is_admin(uid, email)}

    def register(self, email: str, password: str, name: str = "", ip: str = "") -> dict:
        email = email.strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 200:
            raise AuthError("Zadejte platný e-mail.")
        check_password_rules(password)
        if self._exec("SELECT id FROM users WHERE email=?", (email,)):
            raise AuthError("Účet s tímto e-mailem už existuje. Přihlaste se.")
        self._exec("INSERT INTO users (email, name, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                   (email, name.strip()[:80], hash_password(password), _now().isoformat()))
        return self.login(email, password, ip)

    def login(self, email: str, password: str, ip: str = "") -> dict:
        email = email.strip().lower()
        self.guard.check(email, ip)
        rows = self._exec("SELECT id, email, name, pw_hash FROM users WHERE email=?", (email,))
        if len(password) > MAX_PASSWORD or not rows:
            verify_password(password[:MAX_PASSWORD], _DUMMY_HASH)  # same time as a real check
            ok = False
        else:
            ok = verify_password(password, rows[0][3])
        if not ok:
            self.guard.failed(email, ip)
            raise AuthError("Nesprávný e-mail nebo heslo.")
        self.guard.succeeded(email)
        uid, em, name, stored = rows[0]
        if _needs_rehash(stored):
            self._exec("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), uid))
        self.cleanup_sessions()
        return {"token": self._new_session(uid), "user": self._public(uid, em, name)}

    def user_for_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        rows = self._exec(
            "SELECT u.id, u.email, u.name, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (_token_hash(token),))
        if not rows or rows[0][3] < _now().isoformat():
            return None
        uid, em, name, _ = rows[0]
        return self._public(uid, em, name)

    def logout(self, token: str | None) -> None:
        if token:
            self._exec("DELETE FROM sessions WHERE token=?", (_token_hash(token),))

    def logout_everywhere(self, user_id: int, keep_token: str | None = None) -> int:
        """Ends all login sessions of the user except the current one (if given). Returns how many ended."""
        keep = _token_hash(keep_token) if keep_token else ""
        n = self._exec("SELECT COUNT(*) FROM sessions WHERE user_id=? AND token<>?", (user_id, keep))[0][0]
        self._exec("DELETE FROM sessions WHERE user_id=? AND token<>?", (user_id, keep))
        return n

    def session_count(self, user_id: int) -> int:
        return self._exec("SELECT COUNT(*) FROM sessions WHERE user_id=? AND expires_at>=?",
                          (user_id, _now().isoformat()))[0][0]

    def _check_current_password(self, user_id: int, password: str) -> None:
        rows = self._exec("SELECT pw_hash FROM users WHERE id=?", (user_id,))
        if not rows or len(password) > MAX_PASSWORD or not verify_password(password, rows[0][0]):
            raise AuthError("Současné heslo nesouhlasí.")

    def change_password(self, user_id: int, current: str, new: str, keep_token: str | None) -> None:
        self._check_current_password(user_id, current)
        check_password_rules(new)
        self._exec("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(new), user_id))
        self.logout_everywhere(user_id, keep_token)

    def update_name(self, user_id: int, name: str) -> None:
        self._exec("UPDATE users SET name=? WHERE id=?", (name.strip()[:80], user_id))

    def delete_account(self, user_id: int, password: str) -> None:
        """The user deletes their own account; asks for the password so nobody at an unlocked computer can."""
        self._check_current_password(user_id, password)
        self.delete_user(user_id)

    def delete_user(self, user_id: int) -> None:
        """Deletes the account and all its data: sessions, sklad, fields, spray records and support messages."""
        with self.lock:
            tables = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.con.execute("BEGIN")
            try:
                if "support_messages" in tables:
                    self.con.execute("DELETE FROM support_messages WHERE user_id=?", (user_id,))
                self.con.execute("DELETE FROM users WHERE id=?", (user_id,))  # the rest goes by ON DELETE CASCADE
                self.con.execute("COMMIT")
            except Exception:
                self.con.execute("ROLLBACK")
                raise

    # --- administrator ------------------------------------------------------------------------
    def is_admin(self, user_id: int, email: str) -> bool:
        """Administrators are the e-mails in config.admin_emails(). If none are set, the oldest account
        (normally the owner who registered first) is the administrator."""
        admins = config.admin_emails()
        if admins:
            return email.lower() in admins
        first = self._exec("SELECT id FROM users ORDER BY id LIMIT 1")
        return bool(first) and first[0][0] == user_id

    def list_users(self) -> list[dict]:
        rows = self._exec("SELECT id, email, name, created_at, last_login_at FROM users ORDER BY id")
        out = []
        for uid, em, name, created, last in rows:
            out.append({"id": uid, "email": em, "name": name, "created_at": created, "last_login_at": last,
                        "is_admin": self.is_admin(uid, em), "sessions": self.session_count(uid)})
        return out

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
