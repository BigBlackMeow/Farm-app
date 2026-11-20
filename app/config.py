"""Settings that are easy to change in one place: the app's name and where support messages go.

Change APP_NAME here and the whole app (page title, header, login screen, printouts) uses the new name.
"""
from __future__ import annotations

import os

# The app's name, shown in the header, on the login screen, in the browser tab and on printouts.
APP_NAME = os.environ.get("APP_NAME", "Farm App")
APP_TAGLINE = "přípravky na pozemek"

# Support form ("Podpora"): messages are always saved in data/users.sqlite and also sent by e-mail
# through the free service Web3Forms (https://web3forms.com). Its access key is not a password: it
# only lets the form deliver messages to the e-mail address it was created for.
SUPPORT_WEB3FORMS_KEY = os.environ.get("SUPPORT_WEB3FORMS_KEY", "")
# Shown to users as a fallback when sending fails ("napište nám na …"). Leave empty to hide it.
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")
