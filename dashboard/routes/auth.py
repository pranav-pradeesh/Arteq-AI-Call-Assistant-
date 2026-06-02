"""
Auth API — JWT endpoints at /api/v1/auth/

Provides:
  POST /api/v1/auth/login  — password → JWT
  GET  /api/v1/auth/me     — validate token, return subject

The admin dashboard also exposes POST /admin/login (same logic).
This router is for external clients (N8N, webhooks, mobile apps).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from src.config.settings import settings

router = APIRouter()
_security = HTTPBearer(auto_error=False)
_ALGORITHM = "HS256"


class LoginIn(BaseModel):
    password: str


@router.post("/login")
async def auth_login(body: LoginIn):
    """Login with admin password and receive a JWT."""
    from dashboard.routes.admin_api import _create_token
    admin_pw = getattr(settings, "DASHBOARD_ADMIN_PASSWORD", "admin")
    if body.password != admin_pw:
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"access_token": _create_token(), "token_type": "bearer"}


@router.get("/me")
async def auth_me(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
):
    """Validate a JWT. Returns 401 if invalid/expired."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=[_ALGORITHM])
        return {"sub": payload.get("sub"), "valid": True}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
