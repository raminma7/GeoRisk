"""Admin login: email + password -> signed session token.

* The admin email and default password come from the environment (``ADMIN_EMAIL``, ``ADMIN_PASSWORD``).
* A password changed in the admin panel is stored only as a PBKDF2 hash in ``app_settings`` and replaces the default.
* Tokens are ``<email>|<expiry>|<HMAC>``; the signing secret is generated once and kept in ``app_settings``,
  so tokens survive restarts. Changing the password rotates the secret (logs out other sessions).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.ingestion.models import AppSetting

TOKEN_TTL_SECONDS = 7 * 24 * 3600
_HASH_KEY = "auth_admin_password_hash"
_SECRET_KEY = "auth_token_secret"
_ITERATIONS = 200_000


def _get(session: Session, key: str):
    row = session.get(AppSetting, key)
    return row.value if row else None


def _put(session: Session, key: str, value) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        session.add(row)
    row.value = value
    row.updated_at = utcnow()
    row.updated_by = "auth"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def _check_hash(password: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def admin_email() -> str:
    return get_settings().admin_email.strip().lower()


def verify_login(session: Session, email: str, password: str) -> bool:
    if not hmac.compare_digest(email.strip().lower().encode(), admin_email().encode()):
        return False
    stored = _get(session, _HASH_KEY)
    if stored:
        return _check_hash(password, stored)
    return hmac.compare_digest(password.encode(), get_settings().admin_password.get_secret_value().encode())


def _secret(session: Session) -> bytes:
    s = _get(session, _SECRET_KEY)
    if not s:
        s = secrets.token_hex(32)
        _put(session, _SECRET_KEY, s)
        session.commit()
    return s.encode()


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def issue_token(session: Session, email: str) -> str:
    payload = f"{email.strip().lower()}|{int(time.time()) + TOKEN_TTL_SECONDS}"
    return f"{payload}|{_sign(_secret(session), payload)}"


def token_email(session: Session, token: str | None) -> str | None:
    """Email of a valid, unexpired admin token, else None."""
    if not token or token.count("|") != 2:
        return None
    email, exp, sig = token.split("|")
    if not exp.isdigit() or int(exp) < time.time():
        return None
    if not hmac.compare_digest(sig, _sign(_secret(session), f"{email}|{exp}")):
        return None
    return email if email == admin_email() else None


def change_password(session: Session, new_password: str) -> None:
    _put(session, _HASH_KEY, hash_password(new_password))
    _put(session, _SECRET_KEY, secrets.token_hex(32))  # invalidate existing sessions
    session.commit()
