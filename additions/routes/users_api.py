"""
backend-additions/routes/users_api.py
======================================
User management and RBAC endpoints for the Arteq Hospital Voice Agent.

Router prefix : /admin
Tags          : users
Auth          : Bearer JWT via `require_auth` / `require_role` (see deps.py)

Endpoints
---------
POST /admin/auth/login        Email + password → JWT access token
GET  /admin/auth/me           Decode current token → user info
GET  /admin/users             List all users (super_admin only)
POST /admin/users             Create a user (super_admin only)
PUT  /admin/users/{id}        Update a user (super_admin only)
DELETE /admin/users/{id}      Delete a user (super_admin only)

Requirements (net-new vs the repo's requirements.txt)
-----------------------------------------------------
    bcrypt>=4.0.0            # password hashing
    email-validator>=2.1.0   # pydantic EmailStr
    # JWT uses python-jose, already pinned in requirements.txt (python-jose[cryptography])

Notes
-----
- Passwords are hashed with bcrypt (the `bcrypt` package directly).
- JWT tokens carry `sub` (email) and `role` claims.
- The existing DASHBOARD_ADMIN_PASSWORD single-password flow remains intact;
  this module adds per-user accounts alongside it (JWT via python-jose).
- Tenant scoping: a user's accessible hospitals are recorded in `user_tenants`.
  `require_tenant_access(hospital_id)` can be used in other routers to enforce
  per-tenant authorization for tenant_admin and viewer roles.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional

import asyncpg
import bcrypt
from jose import jwt
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, EmailStr, Field

from ..deps import AuthDep, PoolDep, require_auth, require_role

router = APIRouter(prefix="/admin", tags=["users"])

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly — matches the $2b$ hash seeded in 006b)
# ---------------------------------------------------------------------------

# bcrypt operates on bytes and silently truncates at 72 bytes; encode + slice.
def _pw_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_pw_bytes(plain), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT settings (must match deps.py and existing auth.py)
# ---------------------------------------------------------------------------

from ..deps import JWT_SECRET  # same secret the rest of the dashboard verifies with

JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.environ.get("DASHBOARD_JWT_EXPIRE_MINUTES", "720"))


def _issue_token(email: str, role: str) -> str:
    """Issue a signed JWT with sub and role claims."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "admin",
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    email: str
    role: str


class UserOut(BaseModel):
    """Public representation of a user (no password hash)."""

    id: str
    email: str
    role: str
    active: bool
    created_at: Optional[str] = None
    tenant_slugs: List[str] = Field(default_factory=list)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Plain-text; will be bcrypt-hashed")
    role: str = Field(..., description="One of: super_admin, tenant_admin, viewer")
    active: bool = True
    tenant_slugs: List[str] = Field(
        default_factory=list,
        description="Hospital slugs this user can access (for tenant_admin / viewer)",
    )


class UpdateUserRequest(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)
    role: Optional[str] = None
    active: Optional[bool] = None
    tenant_slugs: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

VALID_ROLES = {"super_admin", "tenant_admin", "viewer"}


# ---------------------------------------------------------------------------
# Per-tenant access helper
# ---------------------------------------------------------------------------

def require_tenant_access(hospital_id_param: str = "hospital_id"):
    """
    Dependency factory: allow super_admin through unconditionally; for
    tenant_admin / viewer check that the user has a row in user_tenants
    linking them to the relevant hospital's slug.

    Usage in another router:
        @router.get("/hospitals/{hospital_id}/...")
        async def some_endpoint(
            hospital_id: str,
            pool: PoolDep,
            _access: Annotated[None, Depends(require_tenant_access())],
        ): ...

    TODO: This implementation requires the hospitals table to be joined to
    resolve slug from hospital_id UUID.  Wire accordingly once integrated.
    """

    async def _check(
        hospital_id: str,
        pool: PoolDep,
        payload: AuthDep,
    ) -> None:
        role: str = payload.get("role", "")
        if role == "super_admin":
            return  # Unrestricted access

        email: str = payload.get("sub", "")

        # Resolve slug from hospital_id
        async with pool.acquire() as conn:
            slug = await conn.fetchval(
                "SELECT slug FROM hospitals WHERE id = $1 LIMIT 1",
                hospital_id,
            )
            if slug is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Hospital '{hospital_id}' not found.",
                )

            allowed = await conn.fetchval(
                """
                SELECT 1 FROM user_tenants ut
                JOIN users u ON u.id = ut.user_id
                WHERE u.email = $1 AND ut.tenant_slug = $2
                LIMIT 1
                """,
                email,
                slug,
            )

        if allowed is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this hospital.",
            )

    return _check


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    summary="Email + password login → JWT",
)
async def login(
    body: LoginRequest,
    pool: PoolDep,
) -> TokenResponse:
    """
    Authenticate with email and bcrypt-hashed password.
    Returns a JWT access token carrying `sub` (email) and `role` claims.

    Note: The existing single-password login (DASHBOARD_ADMIN_PASSWORD) lives
    in dashboard/routes/auth.py and is unaffected by this endpoint.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text, email, password_hash, role, active
            FROM users
            WHERE email = $1
            LIMIT 1
            """,
            body.email,
        )

    if row is None or not row["active"]:
        # Return a generic message to avoid user enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _issue_token(email=row["email"], role=row["role"])
    return TokenResponse(access_token=token)


@router.get(
    "/auth/me",
    response_model=MeResponse,
    summary="Return current user info from JWT",
)
async def me(payload: AuthDep) -> MeResponse:
    """
    Decode the current Bearer token and return the authenticated user's
    email and role without a DB round-trip.
    """
    email: Optional[str] = payload.get("sub")
    role: Optional[str] = payload.get("role")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim.",
        )
    return MeResponse(email=email, role=role or "unknown")


# ---------------------------------------------------------------------------
# User CRUD (super_admin only)
# ---------------------------------------------------------------------------

_super_admin_dep = Depends(require_role("super_admin"))


@router.get(
    "/users",
    response_model=List[UserOut],
    summary="List all users (super_admin only)",
    dependencies=[_super_admin_dep],
)
async def list_users(pool: PoolDep, _auth: AuthDep) -> List[UserOut]:
    """Return all users with their tenant slug assignments."""
    async with pool.acquire() as conn:
        users = await conn.fetch(
            """
            SELECT id::text, email, role, active, created_at
            FROM users
            ORDER BY created_at DESC
            """
        )
        tenants = await conn.fetch(
            """
            SELECT user_id::text, tenant_slug
            FROM user_tenants
            """
        )

    # Build slug lists per user_id
    slug_map: dict[str, List[str]] = {}
    for t in tenants:
        slug_map.setdefault(t["user_id"], []).append(t["tenant_slug"])

    return [
        UserOut(
            id=u["id"],
            email=u["email"],
            role=u["role"],
            active=u["active"],
            created_at=u["created_at"].isoformat() if u["created_at"] else None,
            tenant_slugs=slug_map.get(u["id"], []),
        )
        for u in users
    ]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (super_admin only)",
    dependencies=[_super_admin_dep],
)
async def create_user(body: CreateUserRequest, pool: PoolDep, _auth: AuthDep) -> UserOut:
    """
    Create a new user account.  Password is bcrypt-hashed before storage.
    Optionally assign the user to one or more hospital slugs via `tenant_slugs`.
    """
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{body.role}'. Must be one of: {sorted(VALID_ROLES)}",
        )

    pw_hash = hash_password(body.password)

    async with pool.acquire() as conn:
        # Check for duplicate email
        existing = await conn.fetchval(
            "SELECT 1 FROM users WHERE email = $1 LIMIT 1", body.email
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A user with email '{body.email}' already exists.",
            )

        async with conn.transaction():
            user_row = await conn.fetchrow(
                """
                INSERT INTO users (email, password_hash, role, active)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text, email, role, active, created_at
                """,
                body.email,
                pw_hash,
                body.role,
                body.active,
            )

            # Insert tenant associations
            for slug in body.tenant_slugs:
                await conn.execute(
                    """
                    INSERT INTO user_tenants (user_id, tenant_slug)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    user_row["id"],
                    slug,
                )

    return UserOut(
        id=user_row["id"],
        email=user_row["email"],
        role=user_row["role"],
        active=user_row["active"],
        created_at=user_row["created_at"].isoformat() if user_row["created_at"] else None,
        tenant_slugs=body.tenant_slugs,
    )


@router.put(
    "/users/{user_id}",
    response_model=UserOut,
    summary="Update a user (super_admin only)",
    dependencies=[_super_admin_dep],
)
async def update_user(
    user_id: Annotated[str, Path(description="User UUID")],
    body: UpdateUserRequest,
    pool: PoolDep,
    _auth: AuthDep,
) -> UserOut:
    """
    Partially update a user.  Only supplied fields are modified.
    Password is re-hashed if provided.  Tenant slugs are fully replaced
    (not merged) when `tenant_slugs` is present in the request body.
    """
    if body.role is not None and body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{body.role}'. Must be one of: {sorted(VALID_ROLES)}",
        )

    async with pool.acquire() as conn:
        # Verify user exists
        existing = await conn.fetchrow(
            "SELECT id::text, email, role, active FROM users WHERE id = $1 LIMIT 1",
            user_id,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{user_id}' not found.",
            )

        # Build SET clause dynamically to only touch provided fields
        set_clauses: list[str] = []
        params: list = []

        if body.email is not None:
            params.append(body.email)
            set_clauses.append(f"email = ${len(params)}")
        if body.password is not None:
            params.append(hash_password(body.password))
            set_clauses.append(f"password_hash = ${len(params)}")
        if body.role is not None:
            params.append(body.role)
            set_clauses.append(f"role = ${len(params)}")
        if body.active is not None:
            params.append(body.active)
            set_clauses.append(f"active = ${len(params)}")

        async with conn.transaction():
            if set_clauses:
                params.append(user_id)
                update_sql = (
                    f"UPDATE users SET {', '.join(set_clauses)} "
                    f"WHERE id = ${len(params)} "
                    f"RETURNING id::text, email, role, active, created_at"
                )
                updated = await conn.fetchrow(update_sql, *params)
            else:
                updated = await conn.fetchrow(
                    "SELECT id::text, email, role, active, created_at FROM users WHERE id = $1",
                    user_id,
                )

            # Replace tenant slugs if provided
            if body.tenant_slugs is not None:
                await conn.execute(
                    "DELETE FROM user_tenants WHERE user_id = $1", user_id
                )
                for slug in body.tenant_slugs:
                    await conn.execute(
                        "INSERT INTO user_tenants (user_id, tenant_slug) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        user_id,
                        slug,
                    )

        # Fetch final tenant slugs
        slugs = await conn.fetch(
            "SELECT tenant_slug FROM user_tenants WHERE user_id = $1", user_id
        )

    return UserOut(
        id=updated["id"],
        email=updated["email"],
        role=updated["role"],
        active=updated["active"],
        created_at=updated["created_at"].isoformat() if updated["created_at"] else None,
        tenant_slugs=[s["tenant_slug"] for s in slugs],
    )


@router.delete(
    "/users/{user_id}",
    
    summary="Delete a user (super_admin only)",
    dependencies=[_super_admin_dep],
)
async def delete_user(
    user_id: Annotated[str, Path(description="User UUID")],
    pool: PoolDep,
    _auth: AuthDep,
) -> None:
    """
    Permanently delete a user account and all their tenant associations
    (CASCADE is defined in the migration).
    """
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM users WHERE id = $1 RETURNING id", user_id
        )
    if deleted is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )
