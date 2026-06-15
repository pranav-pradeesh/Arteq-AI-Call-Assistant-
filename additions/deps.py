"""
backend-additions/deps.py
=========================
Shared FastAPI dependencies for the analytics, QA, and users routers.

Import in routers as:
    from ..deps import get_pool, require_auth, require_role

When dropping these routers into the real application, wire the two
TODO stubs below to the existing implementations (see inline comments).
"""

from __future__ import annotations

import os
from typing import Annotated, Optional

import asyncpg
# Use python-jose (already in the repo's requirements.txt as
# python-jose[cryptography]) so dashboard tokens are signed/verified with the
# same library as the existing auth.py — no new JWT dependency needed.
from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ---------------------------------------------------------------------------
# JWT settings — sourced from the app settings so tokens are verified with the
# SAME secret the dashboard signs with (settings reads .env; a bare
# os.environ.get would silently fall back to a guessable default when the
# secret lives only in the .env file).
# ---------------------------------------------------------------------------
def _jwt_secret() -> str:
    try:
        from src.config.settings import settings
        return settings.DASHBOARD_JWT_SECRET
    except Exception:
        return os.environ.get("DASHBOARD_JWT_SECRET", "")


JWT_SECRET: str = _jwt_secret()
JWT_ALGORITHM: str = "HS256"

_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database pool dependency
# ---------------------------------------------------------------------------

async def get_pool(request: Request) -> asyncpg.Pool:
    """
    Return the shared asyncpg connection pool.

    TODO: In the real app, the pool is stored on app.state after startup:

        # In src/main.py (startup event):
        app.state.pool = await asyncpg.create_pool(settings.DATABASE_URL, ...)

        # Then this function just does:
        return request.app.state.pool

    For now this is a placeholder that raises clearly if the pool is absent.
    """
    pool: Optional[asyncpg.Pool] = getattr(request.app.state, "pool", None)
    if pool is not None:
        return pool
    # Fall back to the app's existing control pool (src/db/queries.py). This is
    # what main.py's lifespan uses, so no startup changes are needed.
    try:
        from src.db.queries import get_control_pool
        return await get_control_pool()
    except Exception as exc:
        raise RuntimeError(
            "No asyncpg pool available: app.state.pool is unset and importing "
            "src.db.queries.get_control_pool failed. Either set app.state.pool "
            "in your startup event, or adjust this import to your pool factory."
        ) from exc


# ---------------------------------------------------------------------------
# Auth / RBAC dependencies
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> dict:
    """
    Decode and validate a JWT bearer token.

    TODO: If the existing auth.py uses a different secret / algorithm / claim
    structure, align this function to match — it is intentionally minimal so
    there is a single change-point.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_auth(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_bearer_scheme)
    ],
) -> dict:
    """
    FastAPI dependency: require a valid Bearer JWT.

    Returns the decoded token payload (dict) so downstream dependencies
    (e.g. require_role) can inspect claims without re-decoding.

    TODO: To delegate to the existing _require_auth in dashboard/routes/auth.py,
    replace this function body with a call to that implementation, or simply
    use it as the dependency in include_router() via the `dependencies=` kwarg:

        app.include_router(
            analytics_api.router,
            dependencies=[Depends(existing_require_auth)],
        )
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(credentials.credentials)


def require_role(*allowed_roles: str):
    """
    Dependency factory: require the JWT to carry a `role` claim matching one
    of the supplied roles.

    Usage in a route:
        @router.get("/users")
        async def list_users(
            _: Annotated[dict, Depends(require_role("super_admin"))],
        ): ...

    The factory returns an async dependency function each time it is called,
    so you can parameterise it per-route without boilerplate.
    """

    async def _check(
        payload: Annotated[dict, Depends(require_auth)],
    ) -> dict:
        role: Optional[str] = payload.get("role")
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{role}' is not authorised for this operation. "
                    f"Required: {list(allowed_roles)}"
                ),
            )
        return payload

    return _check


# ---------------------------------------------------------------------------
# Convenience type aliases for annotated injection
# ---------------------------------------------------------------------------

PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]
AuthDep = Annotated[dict, Depends(require_auth)]


# ---------------------------------------------------------------------------
# Per-hospital access guard
# ---------------------------------------------------------------------------

def _is_super(payload: dict) -> bool:
    """True for both the legacy single-password admin and super_admin role tokens."""
    return payload.get("sub") == "admin" or payload.get("role") == "super_admin"


async def require_hospital_access(
    hospital_id: str,   # injected from the route's path parameter by FastAPI
    pool: PoolDep,
    payload: AuthDep,
) -> None:
    """Dependency: 403 unless the authenticated user may access this hospital.

    super_admin (and the legacy single-password 'admin' sub) pass unconditionally.
    tenant_admin / viewer must have a user_tenants row linking their email to
    the hospital's slug — mirrors _assert_hospital_access in admin_api.py.
    """
    if _is_super(payload):
        return
    email = payload.get("sub", "")
    async with pool.acquire() as conn:
        allowed = await conn.fetchval(
            """SELECT 1 FROM user_tenants ut
               JOIN users u ON u.id = ut.user_id
               JOIN hospitals h ON h.slug = ut.tenant_slug
               WHERE u.email = $1 AND h.id = $2 AND u.active
               LIMIT 1""",
            email, hospital_id,
        )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this hospital")
