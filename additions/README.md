# backend-additions — Integration Guide

Additive backend code for the Arteq Hospital Voice Agent.
Nothing in the existing codebase is modified; these files are dropped in and wired up.

---

## 0. Quick start (TL;DR)

```bash
# 1. add deps
pip install -r backend-additions/requirements-additions.txt   # bcrypt, email-validator, redis (JWT uses the repo's python-jose)

# 2. copy the package into the app, e.g. as src/additions/, then run migrations:
psql "$DATABASE_URL" -f migrations/versions/006_users_rbac.sql
psql "$DATABASE_URL" -f migrations/versions/006b_seed_superadmin.sql   # bootstrap super-admin
# (both also run automatically on app startup — see src/main.py)
```

```python
# 3. in src/main.py, after the asyncpg pool is on app.state.pool:
from additions.wiring import register_additions
register_additions(app)
# or reuse the existing bearer check on the analytics/QA routers:
# register_additions(app, auth_dependency=existing_require_auth)
```

`register_additions()` mounts four routers (all under the existing `/admin`
prefix), giving the dashboard's planned endpoints:

| Endpoint | Router | Frontend `api.ts` |
|----------|--------|-------------------|
| `GET /admin/hospitals/{id}/analytics` | analytics_api | `api.analytics` |
| `GET /admin/hospitals/{id}/analytics/summary` | analytics_api | `api.analyticsSummary` |
| `GET /admin/hospitals/{id}/calls/{call_id}` | qa_api | `api.getCall` |
| `GET /admin/hospitals/{id}/feedback` | qa_api | `api.listFeedback` |
| `GET /admin/hospitals/{id}/missed-questions` | qa_api | `api.listMissedQuestions` |
| `GET /admin/hospitals/{id}/active-calls` | monitoring_api | `api.activeCalls` (polling fallback) |
| `WS  /admin/ws/live?hospital_id=&token=` | live_ws | `use-live-calls.ts` (real-time) |
| `POST /admin/auth/login`, `GET /admin/auth/me` | users_api | `api.me` |
| `GET/POST/PUT/DELETE /admin/users` | users_api | `api.listUsers` … |

**Bootstrap login:** `006b` seeds `mohammedhayyan@arteqai.com` as `super_admin`
with a bcrypt hash of the current `DASHBOARD_ADMIN_PASSWORD`. Log in via
`POST /admin/auth/login`, then **rotate the password**.

**Live monitoring** has two layers: a polling endpoint (`monitoring_api.py`,
always works) and a real-time **WebSocket** (`live_ws.py` + `live_events.py`).
The dashboard prefers the WebSocket and automatically falls back to polling.

### Wiring live events from the agent

The WebSocket only streams what the voice loop publishes. In the agent/voice
loop, call the emitters from `live_events.py` at the call lifecycle points:

```python
from additions.live_events import emit_call_started, emit_call_updated, emit_call_ended

# at call start (after the call_logs row is created):
await emit_call_started(hospital_id, call_row_dict)
# on each turn / status change (optional):
await emit_call_updated(hospital_id, call_row_dict)
# at hang-up:
await emit_call_ended(hospital_id, call_id)
```

**Transport choice is automatic:** set `REDIS_URL` and events bridge across
processes (required in **sip** mode, where the LiveKit worker publishes and the
web server's socket subscribes). Without `REDIS_URL` it uses an in-process bus,
which is sufficient only in **stream** mode (voice loop runs inside the web
app). The browser authenticates the socket with its session JWT via the
`?token=` query param (WebSocket handshakes can't carry an Authorization
header).

---

## 1. Where to place the files

The existing admin API lives in `dashboard/routes/admin_api.py`.
Drop these additions alongside it:

```
dashboard/
├── routes/
│   ├── admin_api.py          ← existing (untouched)
│   ├── auth.py               ← existing (untouched)
│   ├── analytics_api.py      ← new
│   ├── qa_api.py             ← new
│   └── users_api.py          ← new
├── deps.py                   ← new (shared dependencies)
└── __init__.py               ← new (if not already present)

migrations/versions/
└── 006_users_rbac.sql        ← new
```

If you prefer a separate observability package (matching `src/observability/`),
copy `analytics_api.py` and `qa_api.py` to `src/observability/` and adjust
the relative import in the `from ..deps import ...` lines accordingly.

---

## 2. Including the routers in the FastAPI app

In `src/main.py` (or wherever `app = FastAPI(...)` lives), add:

```python
from dashboard.routes import analytics_api, qa_api, users_api

app.include_router(analytics_api.router)
app.include_router(qa_api.router)
app.include_router(users_api.router)
```

All three routers use `prefix="/admin"` and are protected by `require_auth`,
matching the convention in `admin_api.py`.

---

## 3. Binding `get_pool` to the real pool

`deps.py` reads `request.app.state.pool`.  Make sure the startup event in
`src/main.py` assigns the pool there:

```python
@app.on_event("startup")
async def startup():
    app.state.pool = await asyncpg.create_pool(settings.DATABASE_URL, ...)
```

If `app.state.pool` is already set by existing startup code, no change is needed.

---

## 4. Binding `require_auth` to the existing implementation

`deps.py` contains a self-contained JWT check that mirrors the existing one.
To use the *real* `_require_auth` from `dashboard/routes/auth.py` instead,
either:

**Option A — replace the import in each router file:**
```python
# In analytics_api.py / qa_api.py / users_api.py, change:
from ..deps import require_auth
# to:
from .auth import _require_auth as require_auth
```

**Option B — pass the real dependency at include_router time:**
```python
from dashboard.routes.auth import _require_auth

app.include_router(
    analytics_api.router,
    dependencies=[Depends(_require_auth)],
)
```

---

## 5. Running migration 006

The existing codebase runs migrations automatically on startup via
`migrations/versions/*.sql` files.  Copy `006_users_rbac.sql` into that folder
and it will be picked up on the next deploy.

To run it manually against Supabase (or any PostgreSQL):

```bash
psql "$DATABASE_URL" -f migrations/versions/006_users_rbac.sql
```

The migration is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`)
so re-running it is safe.

After running the migration, create the first `super_admin` user either via
the API (`POST /admin/users`) or by uncommenting and executing the seed INSERT
at the bottom of the SQL file (remember to bcrypt-hash the password first).

---

## 6. New Python requirements

Add to `requirements.txt` (net-new only):

```
bcrypt>=4.0.0
email-validator>=2.1.0
redis>=5.0.0            # optional — live-monitoring pub/sub
```

**JWT uses `python-jose`**, which is already pinned in the repo
(`python-jose[cryptography]`) and used by the existing auth — the additions
import it as `from jose import jwt`, so no new JWT package is required. Do **not**
add `PyJWT`/`import jwt`: that package isn't installed and was the cause of the
`ModuleNotFoundError`. Password hashing uses `bcrypt` directly (no `passlib`).

---

## 7. Endpoint reference (matches `src/lib/api.ts`)

| Method | Path | api.ts call | Auth |
|--------|------|-------------|------|
| GET | `/admin/hospitals/{id}/analytics?from&to&bucket` | `api.analytics(hid, from, to, bucket)` | Bearer JWT |
| GET | `/admin/hospitals/{id}/analytics/summary?from&to` | `api.analyticsSummary(hid, from, to)` | Bearer JWT |
| GET | `/admin/hospitals/{id}/calls/{call_id}` | `api.getCall(hid, callId)` | Bearer JWT |
| GET | `/admin/hospitals/{id}/feedback?min_rating&max_rating` | `api.listFeedback(hid, minRating, maxRating)` | Bearer JWT |
| GET | `/admin/hospitals/{id}/missed-questions?language` | `api.listMissedQuestions(hid, language)` | Bearer JWT |
| POST | `/admin/auth/login` | `api.me()` triggers this via login page | Public |
| GET | `/admin/auth/me` | `api.me()` | Bearer JWT |
| GET | `/admin/users` | `api.listUsers()` | Bearer JWT (super_admin) |
| POST | `/admin/users` | `api.createUser(body)` | Bearer JWT (super_admin) |
| PUT | `/admin/users/{id}` | `api.updateUser(id, body)` | Bearer JWT (super_admin) |
| DELETE | `/admin/users/{id}` | `api.deleteUser(id)` | Bearer JWT (super_admin) |

The existing single-password login remains at `POST /admin/login`
(in `dashboard/routes/auth.py`) and is unaffected.

---

## 8. Environment variables (no new variables required)

These new routes re-use the existing variables already in `.env`:

| Variable | Used by |
|----------|---------|
| `DASHBOARD_JWT_SECRET` | `deps.py`, `users_api.py` — must match existing value |
| `DASHBOARD_JWT_EXPIRE_MINUTES` | `users_api.py` — optional, defaults to 720 |
| `DATABASE_URL` | `get_pool` in `deps.py` — same pool as the rest of the app |
