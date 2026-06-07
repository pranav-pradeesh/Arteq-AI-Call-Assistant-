# Arteq / Arya — Dashboard API Reference

For building a **new hospital dashboard frontend** against the existing backend.
The backend is done — you only build UI. It is a FastAPI app; every endpoint
below already works.

> **Fastest start:** run the backend and open `GET /docs` (Swagger UI) and
> `GET /openapi.json`. Every route, request body, and response is documented
> live there and is always up to date. This file is the hand summary.

---

## 1. Base URL & auth

- **Base URL:** the running backend (e.g. `https://arteq-voice-agent.onrender.com`
  or your VPS host). All admin routes are under the `/admin` prefix.
- **Auth model:** single admin password → JWT (HS256). Send the JWT as a Bearer
  token on every `/admin/*` request.

### Login

```
POST /admin/login
Content-Type: application/json

{ "password": "<DASHBOARD_ADMIN_PASSWORD>" }
```

Response:

```json
{ "access_token": "eyJhbGc...", "token_type": "bearer" }
```

Token lifetime: 720 minutes (12h) by default (`DASHBOARD_JWT_EXPIRE_MINUTES`).

### Authenticated calls

```
Authorization: Bearer <access_token>
```

Missing/invalid/expired token → `401`. There is also a mirror at
`POST /api/v1/auth/login` and `GET /api/v1/auth/me` (validate a token).

### Example (fetch)

```js
const BASE = "https://YOUR_BACKEND";

async function login(password) {
  const r = await fetch(`${BASE}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!r.ok) throw new Error("login failed");
  return (await r.json()).access_token;
}

async function api(token, path, opts = {}) {
  const r = await fetch(`${BASE}/admin${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(opts.headers || {}),
    },
  });
  if (r.status === 401) throw new Error("unauthorized — re-login");
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? null : r.json();
}

// usage
const token = await login("...");
const hospitals = await api(token, "/hospitals");
```

---

## 2. Core concept: multi-tenant

One backend serves many hospitals/clinics. Almost every route is scoped by a
`hospital_id` (UUID). Typical flow:

1. `GET /admin/hospitals` → list, pick an `id`.
2. All further reads/writes use that `hospital_id` in the path.

**Day-of-week convention everywhere: `0 = Sunday … 6 = Saturday`.**

---

## 3. Endpoints

All paths below are prefixed with `/admin`. All require the Bearer token.

### Hospitals
| Method | Path | Purpose |
|---|---|---|
| GET | `/hospitals` | List all hospitals |
| GET | `/hospitals/{hospital_id}` | One hospital (full detail) |
| POST | `/hospitals` | Create (body = HospitalUpdate) |
| PUT | `/hospitals/{hospital_id}` | Update (partial) |

`HospitalUpdate` body (all optional on PUT):
```json
{
  "name": "City Hospital",
  "name_ml": "സിറ്റി ഹോസ്പിറ്റൽ",
  "address": "...",
  "phone": "+91...",
  "hours": { "mon": "9-17", "...": "..." },
  "active": true,
  "slug": "city-hospital",
  "knowledge_base": "free text"
}
```
List item shape:
```json
{
  "id": "uuid", "name": "...", "name_ml": "...", "address": "...",
  "phone": "...", "hours": {}, "active": true, "slug": "...",
  "plivo_number": "...", "tier": "hospital", "agent_name": "Arya",
  "agent_language": "ml-IN", "knowledge_base": ""
}
```

### Departments
| Method | Path |
|---|---|
| GET | `/hospitals/{hospital_id}/departments` |
| POST | `/hospitals/{hospital_id}/departments` |
| PUT | `/hospitals/{hospital_id}/departments/{dept_id}` |
| DELETE | `/hospitals/{hospital_id}/departments/{dept_id}` |

`DeptBody`:
```json
{ "name": "Cardiology", "name_ml": "", "floor": "2",
  "location_hint": "near lift", "phone_ext": "204", "active": true }
```

### Doctors
| Method | Path |
|---|---|
| GET | `/hospitals/{hospital_id}/doctors` |
| POST | `/hospitals/{hospital_id}/doctors` |
| PUT | `/hospitals/{hospital_id}/doctors/{doctor_id}` |
| DELETE | `/hospitals/{hospital_id}/doctors/{doctor_id}` |

`DoctorBody`:
```json
{ "name": "Dr A Nair", "name_ml": "", "specialty": "Cardiology",
  "qualifications": "MD DM", "dept_id": "uuid", "active": true }
```

### Schedules (per doctor)
| Method | Path |
|---|---|
| POST | `/doctors/{doctor_id}/schedules` |
| DELETE | `/schedules/{schedule_id}` |

`ScheduleBody`:
```json
{ "day_of_week": 1, "start_time": "09:00", "end_time": "13:00",
  "room": "204", "active": true }
```

### Appointments
| Method | Path | Notes |
|---|---|---|
| GET | `/hospitals/{hospital_id}/appointments?status=&limit=50` | filter by status |
| PUT | `/hospitals/{hospital_id}/appointments/{appt_id}/status` | body `{ "status": "confirmed" }` |
| POST | `/hospitals/{hospital_id}/appointments/{appt_id}/confirm-payment` | **marks paid + assigns queue token + notifies patient** |

Allowed status values: `requested, confirmed, cancelled, completed, no_show`.
`confirm-payment` is the action that turns an unpaid booking into an active
queue token (returns the assigned `token_number`).

### Calls, stats, callbacks
| Method | Path |
|---|---|
| GET | `/hospitals/{hospital_id}/calls?limit=50` |
| GET | `/hospitals/{hospital_id}/stats?days=7` |
| GET | `/hospitals/{hospital_id}/callbacks?status=&limit=50` |

### Billing items
| Method | Path |
|---|---|
| GET/POST | `/hospitals/{hospital_id}/billing` |
| PUT/DELETE | `/hospitals/{hospital_id}/billing/{item_id}` |

`BillingBody`:
```json
{ "item": "consultation:general", "item_ml": "", "price_min": 300,
  "price_max": 500, "notes": "", "active": true }
```

### FAQs
| Method | Path |
|---|---|
| GET/POST | `/hospitals/{hospital_id}/faqs` |
| PUT/DELETE | `/hospitals/{hospital_id}/faqs/{faq_id}` |

`FaqBody`:
```json
{ "category": "general", "question": "...", "answer": "...",
  "answer_ml": "", "tags": [], "priority": 0, "active": true }
```

### Emergency contacts
| Method | Path |
|---|---|
| GET/POST | `/hospitals/{hospital_id}/emergency` |
| PUT/DELETE | `/hospitals/{hospital_id}/emergency/{contact_id}` |

`EmergencyBody`:
```json
{ "label": "Ambulance", "label_ml": "", "phone": "+91...",
  "priority": 0, "active": true }
```

### Telephony setup
| Method | Path | Purpose |
|---|---|---|
| POST | `/hospitals/{hospital_id}/provision-number` | Buy/assign a Plivo number |
| GET | `/hospitals/{hospital_id}/setup-status` | Onboarding progress |
| GET | `/telephony/status?hospital_id=` | Trunk/line status |
| POST | `/sip/setup` | One-time SIP trunk creation |

### HIS integration (hospital info system)
| Method | Path |
|---|---|
| GET/PUT | `/hospitals/{hospital_id}/his-config` |
| GET | `/hospitals/{hospital_id}/his-status` |
| POST | `/hospitals/{hospital_id}/his-config/test` |

`HisConfigBody`:
```json
{ "enabled": false, "type": "generic_rest", "base_url": "",
  "auth": { "type": "bearer", "value": "..." },
  "endpoints": {}, "field_map": {}, "practitioner_map": {},
  "timeout_seconds": 8 }
```

### Tenant management (onboarding new hospitals)
| Method | Path | Purpose |
|---|---|---|
| GET | `/tenants?include_inactive=true` | List tenants |
| GET | `/tenants/{slug}` | One tenant |
| POST | `/tenants` | Onboard new tenant (TenantOnboardIn) |
| PUT | `/tenants/{slug}` | Update |
| PUT | `/tenants/{slug}/features` | Toggle features `{ "features": {...} }` |
| DELETE | `/tenants/{slug}` | Deactivate |
| POST | `/hospitals/wizard` | One-shot: hospital + depts + doctors + faqs |
| GET | `/features/catalog` | Available feature flags |

`HospitalWizardIn` (nested create in one call):
```json
{
  "name": "City Hospital", "name_ml": "", "address": "", "phone": "",
  "slug": null, "tier": "hospital", "hours": null,
  "departments": [
    { "name": "Cardiology", "floor": "2",
      "doctors": [
        { "name": "Dr A Nair", "specialty": "Cardiology",
          "schedules": [
            { "day_of_week": 1, "start_time": "09:00", "end_time": "13:00", "room": "204" }
          ] }
      ] }
  ]
}
```

### Misc
| Method | Path |
|---|---|
| POST | `/hospitals/{hospital_id}/cache/clear` | Force reload hospital config |

---

## 4. Notes for the frontend build

- **CORS:** already enabled. Controlled by the `CORS_ORIGINS` env var
  (comma-separated origins); defaults to `*` if unset, so a separate-origin
  dashboard works out of the box in dev. Auth is Bearer tokens (not cookies),
  so wildcard is safe. **In production, set `CORS_ORIGINS` to the dashboard's
  real origin(s)** to lock it down.
- **Token storage:** store the JWT in memory or `sessionStorage`; on `401`,
  redirect to login. Token expires in 12h.
- **Writes invalidate cache automatically** server-side — no extra call needed,
  except the explicit `/cache/clear` if you change data out of band.
- **All times/day-of-week use `0=Sun … 6=Sat`.** Render accordingly.
- **Source of truth is `/openapi.json`.** Generate a typed client from it
  (e.g. `openapi-typescript`, `orval`) instead of hand-writing types — the
  schema there always matches the live backend.
