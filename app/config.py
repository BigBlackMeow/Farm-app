"""Settings that are easy to change in one place: the app's name and where support messages go.

Change APP_NAME here and the whole app (page title, header, login screen, printouts) uses the new name.

The Web3Forms key for the support form is NOT stored here (the repository is public). It lives in
data/nastaveni.json on the user's own computer, which is not in git and is carried over with the data
folder on every update. The app writes that file itself when the key is pasted on the "Podpora" page.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# The app's name, shown in the header, on the login screen, in the browser tab and on printouts.
APP_NAME = os.environ.get("APP_NAME", "Farm App")
APP_TAGLINE = "přípravky na pozemek"

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "nastaveni.json"


def _load() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def support_key() -> str:
    """Web3Forms access key (https://web3forms.com). Empty = support messages are only saved locally."""
    return os.environ.get("SUPPORT_WEB3FORMS_KEY") or str(_load().get("web3forms_klic", "")).strip()


def support_email() -> str:
    """Optional e-mail shown to users when sending fails ("napište nám na …")."""
    return os.environ.get("SUPPORT_EMAIL") or str(_load().get("email_podpory", "")).strip()


def admin_emails() -> set[str]:
    """E-mails of administrators, who can see and delete user accounts and set up the support form.

    Set ADMIN_EMAILS (comma separated) on the web server, or "spravci": ["vas@email.cz"] in
    data/nastaveni.json. If neither is set, the oldest account in the app is the administrator.
    """
    raw = os.environ.get("ADMIN_EMAILS")
    if raw is None:
        raw = _load().get("spravci", "")
    if isinstance(raw, list):
        raw = ",".join(str(x) for x in raw)
    return {e.strip().lower() for e in str(raw).split(",") if e.strip()}


def save(**values: str) -> None:
    data = _load()
    data.update(values)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
