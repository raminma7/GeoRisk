"""API-key authentication with roles (viewer < analyst < admin).

* Keys are configured via ``API_KEYS="admin:<key>,analyst:<key>"`` and sent
  as ``X-API-Key``. They are compared in constant time and never logged.
* Read endpoints are public when ``PUBLIC_READ=true`` (default).
* If no keys are configured, write access is only allowed when
  ``ENVIRONMENT=development`` (local convenience); otherwise refused.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

ROLE_RANK = {"viewer": 0, "analyst": 1, "admin": 2}
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    role: str
    name: str


def _lookup(key: str | None) -> Principal | None:
    if not key:
        return None
    for known, role in get_settings().parsed_api_keys().items():
        if hmac.compare_digest(known.encode(), key.encode()):
            return Principal(role=role if role in ROLE_RANK else "viewer", name=f"{role}-key")
    return None


def current_principal(key: str | None = Security(api_key_header),
                      cred: HTTPAuthorizationCredentials | None = Security(bearer)) -> Principal | None:
    if cred is not None:
        from app.core import auth
        from app.core.db import SessionLocal

        with SessionLocal() as s:
            email = auth.token_email(s, cred.credentials)
        if email is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
        return Principal(role="admin", name=email)
    p = _lookup(key)
    if key and p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return p


def require_read(p: Principal | None = Depends(current_principal)) -> Principal:
    if p:
        return p
    if get_settings().public_read:
        return Principal(role="viewer", name="anonymous")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key required")


def require_role(role: str):
    def dep(p: Principal | None = Depends(current_principal)) -> Principal:
        settings = get_settings()
        if p is None:
            # Local convenience for analyst actions only; admin actions always need the admin sign-in.
            if role != "admin" and not settings.parsed_api_keys() and settings.environment == "development":
                return Principal(role="admin", name="local-dev")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin sign-in required" if role == "admin" else "API key required")
        if ROLE_RANK.get(p.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{role}'")
        return p

    return dep
