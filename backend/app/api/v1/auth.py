"""Admin sign-in for the Admin panel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import auth
from app.core.db import get_db
from app.core.ratelimit import limiter
from app.core.security import Principal, require_role
from app.ingestion.models import AuditLog

router = APIRouter(prefix="/auth", tags=["Admin sign-in"])


class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class NewPassword(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/login", summary="Sign in as admin; returns a session token (send as Authorization: Bearer)")
@limiter.limit("10/minute")
def login(request: Request, body: Login, s: Session = Depends(get_db)):
    if not auth.verify_login(s, body.email, body.password):
        raise HTTPException(401, "Wrong email or password")
    s.add(AuditLog(actor=body.email.strip().lower(), action="admin_login", target="auth", details={}))
    s.commit()
    return {"token": auth.issue_token(s, body.email), "email": auth.admin_email(),
            "expires_in_seconds": auth.TOKEN_TTL_SECONDS}


@router.get("/me", summary="Who is signed in")
def me(p: Principal = Depends(require_role("admin"))):
    return {"email": p.name, "role": p.role}


@router.post("/change-password", summary="Change the admin password")
def change_password(body: NewPassword, s: Session = Depends(get_db), p: Principal = Depends(require_role("admin"))):
    if not auth.verify_login(s, auth.admin_email(), body.current_password):
        raise HTTPException(400, "Current password is wrong")
    auth.change_password(s, body.new_password)
    s.add(AuditLog(actor=p.name, action="admin_password_changed", target="auth", details={}))
    s.commit()
    return {"ok": True, "message": "Password changed. Please sign in again."}
