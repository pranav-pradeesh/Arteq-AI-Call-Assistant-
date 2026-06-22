"""
backend-additions/wiring.py
===========================
One-call integration helper. Copy this folder's contents into the real repo
(e.g. as `src/additions/`) and call `register_additions(app)` once, right
after the FastAPI app and its asyncpg pool are created.

Example — in src/main.py:

    from additions.wiring import register_additions

    @app.on_event("startup")
    async def _startup():
        app.state.pool = await asyncpg.create_pool(settings.DATABASE_URL, ...)
        # ... existing startup ...

    register_additions(app)   # mounts analytics + QA + users routers

If you prefer to reuse the EXISTING auth dependency instead of the JWT check
in deps.py, pass it in and it will be applied to the analytics + QA routers:

    from dashboard.routes.auth import _require_auth as existing_require_auth
    register_additions(app, auth_dependency=existing_require_auth)

Notes
-----
* `deps.get_pool` reads `app.state.pool` — make sure the pool is assigned
  before the first request (the startup event above does this).
* The users/RBAC router (`/admin/auth/login`, `/admin/users`, ...) brings its
  own email+password auth and does NOT touch the existing single-password
  `/admin/login`. Both can coexist during migration.
* Run migration `migrations/006_users_rbac.sql` (and optionally
  `migrations/006b_seed_superadmin.sql`) before using the users endpoints.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, FastAPI

from .routes import (
    analytics_api,
    doctor_api,
    live_ws,
    monitoring_api,
    qa_api,
    users_api,
)


def register_additions(
    app: FastAPI,
    auth_dependency: Optional[Callable] = None,
) -> None:
    """Mount the analytics, QA, and users routers onto `app`.

    Parameters
    ----------
    app:
        The FastAPI application.
    auth_dependency:
        Optional existing auth dependency (e.g. the project's `_require_auth`).
        When provided it is applied to the analytics and QA routers so they use
        the same Bearer check as the rest of `/admin/*`. The users router keeps
        its own role-aware auth regardless.
    """
    extra = [Depends(auth_dependency)] if auth_dependency else []

    app.include_router(analytics_api.router, dependencies=extra)
    app.include_router(qa_api.router, dependencies=extra)
    app.include_router(monitoring_api.router, dependencies=extra)
    app.include_router(live_ws.router)  # WebSocket; authenticates via ?token= query
    app.include_router(users_api.router)  # self-contained RBAC auth
    app.include_router(doctor_api.router)        # /admin/doctor/* — doctor self-service (role=doctor)
    app.include_router(doctor_api.admin_router)  # /admin/doctor-logins (admin provisioning)

    # Frontend (dashboard-next/src/lib/api.ts) expects these to live under the
    # existing "/admin" prefix — each router already sets prefix="/admin".
