"""Dashboard authentication routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select

from src.config.settings import settings
from src.db.connection import get_db_session
from src.db.models import DashboardUser

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

ALGORITHM = "HS256"


class Token(BaseModel):
    access_token: str
    token_type: str
    tenant_id: Optional[str] = None


class UserOut(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    is_superadmin: bool
    tenant_id: Optional[str]


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.DASHBOARD_JWT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.DASHBOARD_JWT_SECRET, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> DashboardUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.DASHBOARD_JWT_SECRET, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    async with get_db_session() as session:
        result = await session.execute(
            select(DashboardUser).where(
                DashboardUser.email == email, DashboardUser.is_active == True
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            raise credentials_exception
        return user


@router.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    async with get_db_session() as session:
        result = await session.execute(
            select(DashboardUser).where(DashboardUser.email == form_data.username)
        )
        user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token({"sub": user.email})
    return Token(
        access_token=token,
        token_type="bearer",
        tenant_id=str(user.tenant_id) if user.tenant_id else None,
    )


@router.get("/me", response_model=UserOut)
async def get_me(current_user: DashboardUser = Depends(get_current_user)):
    return UserOut(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        is_superadmin=current_user.is_superadmin,
        tenant_id=str(current_user.tenant_id) if current_user.tenant_id else None,
    )
