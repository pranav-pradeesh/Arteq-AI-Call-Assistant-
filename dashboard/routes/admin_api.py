"""
Admin Dashboard API — asyncpg direct queries on the Supabase schema.

Auth: single-admin JWT (DASHBOARD_ADMIN_PASSWORD env var).
Multi-tenant: list all hospitals, CRUD per hospital_id.
Cache: invalidate hospital_cache on every write.

Day-of-week convention (matches DB): 0=Sunday … 6=Saturday.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from pydantic import BaseModel

from src.cache.store import hospital_cache
from src.config.settings import settings
from src.db.queries import get_pool
from src.observability.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()
security = HTTPBearer(auto_error=False)

try:
    templates = Jinja2Templates(directory="dashboard/templates")
except Exception:
    templates = None

ALGORITHM = "HS256"
DOW_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
DOW_FULL = {
    0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
    4: "Thursday", 5: "Friday", 6: "Saturday",
}


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    password: str


def _create_token() -> str:
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=getattr(settings, "DASHBOARD_JWT_EXPIRE_MINUTES", 720)
    )
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    # Legacy single-password admin token — no role claim; "admin" sub is the sentinel.
    return jwt.encode({"sub": "admin", "exp": exp}, secret, algorithm=ALGORITHM)


async def _resolve_scope(payload: dict) -> Optional[set[str]]:
    """Return the set of hospital slugs this token may access.

    ``None`` means *all hospitals* (super-admin or the legacy single-password
    admin). A concrete set means tenant_admin / viewer restricted to those slugs.
    """
    if payload.get("sub") == "admin" and not payload.get("role"):
        return None  # legacy single-password admin
    if payload.get("role") == "super_admin":
        return None
    email = payload.get("sub", "")
    if not email:
        return set()
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT ut.tenant_slug
                 FROM user_tenants ut
                 JOIN users u ON u.id = ut.user_id
                WHERE u.email = $1 AND u.active""",
            email,
        )
    return {r["tenant_slug"] for r in rows}


def _is_super(payload: dict) -> bool:
    return payload.get("sub") == "admin" or payload.get("role") == "super_admin"


async def _require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Validate the bearer token and (for hospital-scoped routes) the tenant scope.

    Accepts BOTH token shapes:
      * legacy single-password admin  → {"sub": "admin"}
      * RBAC email/password user      → {"sub": <email>, "role": <role>}

    If the matched route carries a ``hospital_id`` path/query parameter, a
    tenant_admin / viewer must be assigned to that hospital (via user_tenants)
    or the request is rejected with 403. super_admin and the legacy admin pass.
    """
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Valid token: legacy admin (sub == "admin") OR any RBAC user (has a role claim).
    if payload.get("sub") != "admin" and not payload.get("role"):
        raise HTTPException(status_code=401, detail="Invalid token")

    # Per-hospital authorization: enforce tenant scope on hospital-scoped routes.
    hospital_id = (
        request.path_params.get("hospital_id")
        or request.query_params.get("hospital_id")
    )
    if hospital_id:
        allowed = await _resolve_scope(payload)
        if allowed is not None:  # restricted user — must be assigned to this hospital
            pool = await _db()
            async with pool.acquire() as conn:
                slug = await conn.fetchval(
                    "SELECT slug FROM hospitals WHERE id = $1", hospital_id
                )
            if not slug or slug not in allowed:
                raise HTTPException(status_code=403, detail="No access to this hospital")
    return payload


async def _require_super(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    payload = await _require_auth(request, credentials)
    if not _is_super(payload):
        raise HTTPException(status_code=403, detail="super_admin role required")
    return payload


async def _assert_hospital_access(payload: dict, hospital_id: str) -> None:
    """Internal helper for routes that look up hospital_id dynamically (not in path).

    Uses _resolve_scope so the slug-set is computed once and cached when
    called from within a single request context.
    """
    if _is_super(payload):
        return
    allowed = await _resolve_scope(payload)
    if allowed is None:
        return
    pool = await _db()
    async with pool.acquire() as conn:
        slug = await conn.fetchval("SELECT slug FROM hospitals WHERE id = $1", hospital_id)
    if not slug or slug not in allowed:
        raise HTTPException(status_code=403, detail="No access to this hospital")


@router.post("/login")
async def login(body: LoginIn):
    admin_pw = getattr(settings, "DASHBOARD_ADMIN_PASSWORD", "admin")
    if body.password != admin_pw:
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"access_token": _create_token(), "token_type": "bearer"}


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_home(request: Request):
    if templates:
        return templates.TemplateResponse("index.html", {"request": request})
    return HTMLResponse("<h1>Dashboard templates not found</h1>", status_code=500)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _db():
    return await get_pool()


def _invalidate(hospital_id: str):
    """Drop the in-memory hospital cache so the next call reloads from DB."""
    hospital_cache.delete(hospital_id)


# ── Hospitals ─────────────────────────────────────────────────────────────────

@router.get("/hospitals")
async def list_hospitals(payload: dict = Depends(_require_auth)):
    allowed = await _resolve_scope(payload)
    pool = await _db()
    async with pool.acquire() as conn:
        if allowed is None:  # super-admin / legacy admin: all hospitals
            rows = await conn.fetch(
                "SELECT id, name, name_ml, address, phone, hours, active, "
                "slug, plivo_number, tier, agent_name, agent_language FROM hospitals ORDER BY name"
            )
        else:  # tenant_admin / viewer: only their assigned hospitals
            rows = await conn.fetch(
                "SELECT id, name, name_ml, address, phone, hours, active, "
                "slug, plivo_number, tier, agent_name, agent_language "
                "FROM hospitals WHERE slug = ANY($1) ORDER BY name",
                list(allowed),
            )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "name_ml": r["name_ml"] or "",
            "address": r["address"] or "",
            "phone": r["phone"] or "",
            "hours": _maybe_json(r["hours"]) or {},
            "active": r["active"],
            "slug": r["slug"] or "",
            "plivo_number": r["plivo_number"] or "",
            "tier": r["tier"] or "hospital",
            "agent_name": r["agent_name"] or "Arya",
            "agent_language": r["agent_language"] or "ml-IN",
            "knowledge_base": "",
        }
        for r in rows
    ]


@router.get("/hospitals/{hospital_id}", dependencies=[Depends(_require_auth)])
async def get_hospital(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, name_ml, address, phone, hours, active, "
            "slug, plivo_number, knowledge_base, tier, agent_name, agent_language "
            "FROM hospitals WHERE id=$1",
            hospital_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "name_ml": row["name_ml"] or "",
        "address": row["address"] or "",
        "phone": row["phone"] or "",
        "hours": _maybe_json(row["hours"]) or {},
        "active": row["active"],
        "slug": row["slug"] or "",
        "plivo_number": row["plivo_number"] or "",
        "knowledge_base": row["knowledge_base"] or "",
        "tier": row["tier"] or "hospital",
        "agent_name": row["agent_name"] or "Arya",
        "agent_language": row["agent_language"] or "ml-IN",
    }


def _derive_slug(name: str) -> str:
    """Convert hospital name to a URL-safe slug."""
    slug = name.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


class HospitalUpdate(BaseModel):
    name: Optional[str] = None
    name_ml: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[dict] = None
    active: Optional[bool] = None
    slug: Optional[str] = None
    knowledge_base: Optional[str] = None
    tier: Optional[str] = None           # "clinic" | "hospital"
    agent_name: Optional[str] = None     # AI persona name for this tenant
    agent_language: Optional[str] = None # BCP-47: ml-IN, hi-IN, ta-IN, kn-IN, en-IN


@router.post("/hospitals", dependencies=[Depends(_require_super)])
async def create_hospital(body: HospitalUpdate):
    if not body.name:
        raise HTTPException(status_code=400, detail="name is required")
    new_id = str(uuid.uuid4())
    slug = body.slug or _derive_slug(body.name)
    pool = await _db()
    async with pool.acquire() as conn:
        tier = body.tier if body.tier in ("clinic", "hospital") else "hospital"
        await conn.execute(
            """INSERT INTO hospitals
               (id, name, name_ml, address, phone, hours, active, slug, tier, agent_name, agent_language)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
            new_id,
            body.name,
            body.name_ml or "",
            body.address or "",
            body.phone or "",
            json.dumps(body.hours or {}),
            True,
            slug,
            tier,
            body.agent_name or "Arya",
            body.agent_language or "ml-IN",
        )
    return {"id": new_id, "slug": slug, "tier": tier, "status": "created"}


@router.put("/hospitals/{hospital_id}", dependencies=[Depends(_require_auth)])
async def update_hospital(hospital_id: str, body: HospitalUpdate):
    pool = await _db()
    async with pool.acquire() as conn:
        fields = []
        values = []
        i = 1
        for col, val in [
            ("name", body.name), ("name_ml", body.name_ml),
            ("address", body.address), ("phone", body.phone),
            ("active", body.active),
        ]:
            if val is not None:
                fields.append(f"{col}=${i}")
                values.append(val)
                i += 1
        if body.hours is not None:
            fields.append(f"hours=${i}")
            values.append(json.dumps(body.hours))
            i += 1
        if body.slug is not None:
            fields.append(f"slug=${i}")
            values.append(body.slug)
            i += 1
        if body.knowledge_base is not None:
            fields.append(f"knowledge_base=${i}")
            values.append(body.knowledge_base)
            i += 1
        if body.tier is not None:
            allowed_tiers = {"clinic", "hospital"}
            tier_val = body.tier if body.tier in allowed_tiers else "hospital"
            fields.append(f"tier=${i}")
            values.append(tier_val)
            i += 1
        if body.agent_name is not None:
            fields.append(f"agent_name=${i}")
            values.append(body.agent_name)
            i += 1
        if body.agent_language is not None:
            fields.append(f"agent_language=${i}")
            values.append(body.agent_language)
            i += 1
        if not fields:
            return {"status": "no_changes"}
        values.append(hospital_id)
        await conn.execute(
            f"UPDATE hospitals SET {', '.join(fields)} WHERE id=${i}",
            *values,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}", dependencies=[Depends(_require_super)])
async def delete_hospital(hospital_id: str):
    """Delete a hospital and its configuration (super_admin only).

    Refuses (409) when patient data exists — appointments, callbacks, or call
    logs — so a tenant with history can only be deactivated (PUT active=false),
    never silently erased.
    """
    pool = await _db()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM hospitals WHERE id=$1", hospital_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Hospital not found")
        for table in ("appointments", "callbacks", "call_logs"):
            if await conn.fetchval(
                f"SELECT 1 FROM {table} WHERE hospital_id=$1 LIMIT 1", hospital_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"Hospital has {table} records — deactivate it instead "
                           "(PUT with active=false)",
                )
        async with conn.transaction():
            for table in ("schedules", "doctors", "departments", "billing_info",
                          "faqs", "emergency_contacts", "missed_questions"):
                await conn.execute(f"DELETE FROM {table} WHERE hospital_id=$1", hospital_id)
            await conn.execute("DELETE FROM hospitals WHERE id=$1", hospital_id)
    _invalidate(hospital_id)
    return {"status": "deleted"}


# ── Departments ───────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/departments", dependencies=[Depends(_require_auth)])
async def list_departments(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, name_ml, floor, location_hint, phone_ext, active "
            "FROM departments WHERE hospital_id=$1 ORDER BY name",
            hospital_id,
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "name_ml": r["name_ml"] or "",
            "floor": r["floor"] or "",
            "location_hint": r["location_hint"] or "",
            "phone_ext": r["phone_ext"] or "",
            "active": r["active"],
        }
        for r in rows
    ]


class DeptBody(BaseModel):
    name: str
    name_ml: Optional[str] = ""
    floor: Optional[str] = ""
    location_hint: Optional[str] = ""
    phone_ext: Optional[str] = ""
    active: Optional[bool] = True


@router.post("/hospitals/{hospital_id}/departments", dependencies=[Depends(_require_auth)])
async def create_department(hospital_id: str, body: DeptBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO departments
               (id, hospital_id, name, name_ml, floor, location_hint, phone_ext, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            new_id, hospital_id, body.name, body.name_ml or "",
            body.floor or "", body.location_hint or "",
            body.phone_ext or "", body.active if body.active is not None else True,
        )
    _invalidate(hospital_id)
    return {"id": new_id, "status": "created"}


@router.put("/hospitals/{hospital_id}/departments/{dept_id}", dependencies=[Depends(_require_auth)])
async def update_department(hospital_id: str, dept_id: str, body: DeptBody):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE departments SET name=$1, name_ml=$2, floor=$3,
               location_hint=$4, phone_ext=$5, active=$6
               WHERE id=$7 AND hospital_id=$8""",
            body.name, body.name_ml or "", body.floor or "",
            body.location_hint or "", body.phone_ext or "",
            body.active if body.active is not None else True,
            dept_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}/departments/{dept_id}", dependencies=[Depends(_require_auth)])
async def delete_department(hospital_id: str, dept_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE departments SET active=false WHERE id=$1 AND hospital_id=$2",
            dept_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "deactivated"}


# ── Doctors ───────────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/doctors", dependencies=[Depends(_require_auth)])
async def list_doctors(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT d.id, d.name, d.name_ml, d.specialty, d.qualifications,
                      d.active, d.availability_status,
                      dep.name as dept_name, dep.id as dept_id,
                      json_agg(json_build_object(
                          'id', s.id,
                          'dow', s.day_of_week,
                          'start', to_char(s.start_time,'HH24:MI'),
                          'end', to_char(s.end_time,'HH24:MI'),
                          'room', s.room,
                          'active', s.active
                      ) ORDER BY s.day_of_week, s.start_time)
                      FILTER (WHERE s.id IS NOT NULL) as schedules
               FROM doctors d
               LEFT JOIN departments dep ON d.dept_id = dep.id
               LEFT JOIN schedules s ON s.doctor_id = d.id
               WHERE d.hospital_id=$1
               GROUP BY d.id, d.name, d.name_ml, d.specialty, d.qualifications,
                        d.active, d.availability_status, dep.name, dep.id
               ORDER BY d.name""",
            hospital_id,
        )
    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "name_ml": r["name_ml"] or "",
            "specialty": r["specialty"] or "",
            "qualifications": r["qualifications"] or "",
            "dept_name": r["dept_name"] or "",
            "dept_id": str(r["dept_id"]) if r["dept_id"] else "",
            "active": r["active"],
            "availability_status": r["availability_status"] or "available",
            "schedules": _maybe_json(r["schedules"]) or [],
        }
        for r in rows
    ]


class DoctorBody(BaseModel):
    name: str
    name_ml: Optional[str] = ""
    specialty: Optional[str] = ""
    qualifications: Optional[str] = ""
    dept_id: Optional[str] = None
    active: Optional[bool] = True


@router.post("/hospitals/{hospital_id}/doctors", dependencies=[Depends(_require_auth)])
async def create_doctor(hospital_id: str, body: DoctorBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO doctors
               (id, hospital_id, dept_id, name, name_ml, specialty, qualifications, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            new_id, hospital_id,
            body.dept_id or None,
            body.name, body.name_ml or "",
            body.specialty or "", body.qualifications or "",
            body.active if body.active is not None else True,
        )
    _invalidate(hospital_id)
    return {"id": new_id, "status": "created"}


@router.put("/hospitals/{hospital_id}/doctors/{doctor_id}", dependencies=[Depends(_require_auth)])
async def update_doctor(hospital_id: str, doctor_id: str, body: DoctorBody):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE doctors SET name=$1, name_ml=$2, specialty=$3,
               qualifications=$4, dept_id=$5, active=$6
               WHERE id=$7 AND hospital_id=$8""",
            body.name, body.name_ml or "",
            body.specialty or "", body.qualifications or "",
            body.dept_id or None,
            body.active if body.active is not None else True,
            doctor_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}/doctors/{doctor_id}", dependencies=[Depends(_require_auth)])
async def delete_doctor(hospital_id: str, doctor_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE doctors SET active=false WHERE id=$1 AND hospital_id=$2",
            doctor_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "deactivated"}


# ── Schedules ─────────────────────────────────────────────────────────────────

class ScheduleBody(BaseModel):
    day_of_week: int   # 0=Sun … 6=Sat
    start_time: str    # "HH:MM"
    end_time: str      # "HH:MM"
    room: Optional[str] = ""
    active: Optional[bool] = True


@router.post("/doctors/{doctor_id}/schedules")
async def add_schedule(doctor_id: str, body: ScheduleBody, payload: dict = Depends(_require_auth)):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        # Get hospital_id for tenant scoping + cache invalidation
        row = await conn.fetchrow("SELECT hospital_id FROM doctors WHERE id=$1", doctor_id)
        if not row:
            raise HTTPException(status_code=404, detail="Doctor not found")
        await _assert_hospital_access(payload, str(row["hospital_id"]))
        await conn.execute(
            """INSERT INTO schedules
               (id, doctor_id, hospital_id, day_of_week, start_time, end_time, room, active)
               VALUES ($1,$2,$3,$4,$5::time,$6::time,$7,$8)""",
            new_id, doctor_id, str(row["hospital_id"]),
            body.day_of_week, body.start_time, body.end_time,
            body.room or "", body.active if body.active is not None else True,
        )
        _invalidate(str(row["hospital_id"]))
    return {"id": new_id, "status": "created"}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, payload: dict = Depends(_require_auth)):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT hospital_id FROM schedules WHERE id=$1", schedule_id)
        if row:
            await _assert_hospital_access(payload, str(row["hospital_id"]))
            _invalidate(str(row["hospital_id"]))
        await conn.execute("DELETE FROM schedules WHERE id=$1", schedule_id)
    return {"status": "deleted"}


# ── Billing ───────────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/billing", dependencies=[Depends(_require_auth)])
async def list_billing(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, item, item_ml, price_min, price_max, notes, active "
            "FROM billing_info WHERE hospital_id=$1 ORDER BY item",
            hospital_id,
        )
    return [
        {
            "id": str(r["id"]),
            "item": r["item"],
            "item_ml": r["item_ml"] or "",
            "price_min": float(r["price_min"]) if r["price_min"] is not None else None,
            "price_max": float(r["price_max"]) if r["price_max"] is not None else None,
            "notes": r["notes"] or "",
            "active": r["active"],
        }
        for r in rows
    ]


class BillingBody(BaseModel):
    item: str           # e.g. "consultation:general"
    item_ml: Optional[str] = ""
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    notes: Optional[str] = ""
    active: Optional[bool] = True


@router.post("/hospitals/{hospital_id}/billing", dependencies=[Depends(_require_auth)])
async def create_billing(hospital_id: str, body: BillingBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO billing_info
               (id, hospital_id, item, item_ml, price_min, price_max, notes, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            new_id, hospital_id, body.item, body.item_ml or "",
            body.price_min, body.price_max, body.notes or "",
            body.active if body.active is not None else True,
        )
    _invalidate(hospital_id)
    return {"id": new_id, "status": "created"}


@router.put("/hospitals/{hospital_id}/billing/{item_id}", dependencies=[Depends(_require_auth)])
async def update_billing(hospital_id: str, item_id: str, body: BillingBody):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE billing_info SET item=$1, item_ml=$2, price_min=$3,
               price_max=$4, notes=$5, active=$6
               WHERE id=$7 AND hospital_id=$8""",
            body.item, body.item_ml or "",
            body.price_min, body.price_max, body.notes or "",
            body.active if body.active is not None else True,
            item_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}/billing/{item_id}", dependencies=[Depends(_require_auth)])
async def delete_billing(hospital_id: str, item_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE billing_info SET active=false WHERE id=$1 AND hospital_id=$2",
            item_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "deactivated"}


# ── Emergency Contacts ────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/emergency", dependencies=[Depends(_require_auth)])
async def list_emergency(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, label_ml, phone, priority, active "
            "FROM emergency_contacts WHERE hospital_id=$1 ORDER BY priority DESC",
            hospital_id,
        )
    return [
        {
            "id": str(r["id"]),
            "label": r["label"],
            "label_ml": r["label_ml"] or "",
            "phone": r["phone"],
            "priority": r["priority"] or 0,
            "active": r["active"],
        }
        for r in rows
    ]


class EmergencyBody(BaseModel):
    label: str
    label_ml: Optional[str] = ""
    phone: str
    priority: Optional[int] = 0
    active: Optional[bool] = True


@router.post("/hospitals/{hospital_id}/emergency", dependencies=[Depends(_require_auth)])
async def create_emergency(hospital_id: str, body: EmergencyBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO emergency_contacts
               (id, hospital_id, label, label_ml, phone, priority, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            new_id, hospital_id, body.label, body.label_ml or "",
            body.phone, body.priority or 0,
            body.active if body.active is not None else True,
        )
    _invalidate(hospital_id)
    return {"id": new_id, "status": "created"}


@router.put("/hospitals/{hospital_id}/emergency/{contact_id}", dependencies=[Depends(_require_auth)])
async def update_emergency(hospital_id: str, contact_id: str, body: EmergencyBody):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE emergency_contacts SET label=$1, label_ml=$2, phone=$3,
               priority=$4, active=$5 WHERE id=$6 AND hospital_id=$7""",
            body.label, body.label_ml or "", body.phone,
            body.priority or 0,
            body.active if body.active is not None else True,
            contact_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}/emergency/{contact_id}", dependencies=[Depends(_require_auth)])
async def delete_emergency(hospital_id: str, contact_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE emergency_contacts SET active=false WHERE id=$1 AND hospital_id=$2",
            contact_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "deactivated"}


# ── FAQs ──────────────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/faqs", dependencies=[Depends(_require_auth)])
async def list_faqs(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, category, question, answer, answer_ml, tags, priority, active "
            "FROM faqs WHERE hospital_id=$1 ORDER BY priority DESC, category",
            hospital_id,
        )
    return [
        {
            "id": str(r["id"]),
            "category": r["category"] or "",
            "question": r["question"],
            "answer": r["answer"],
            "answer_ml": r["answer_ml"] or "",
            "tags": _maybe_json(r["tags"]) or [],
            "priority": r["priority"] or 0,
            "active": r["active"],
        }
        for r in rows
    ]


class FaqBody(BaseModel):
    category: Optional[str] = ""
    question: str
    answer: str
    answer_ml: Optional[str] = ""
    tags: Optional[list] = []
    priority: Optional[int] = 0
    active: Optional[bool] = True


@router.post("/hospitals/{hospital_id}/faqs", dependencies=[Depends(_require_auth)])
async def create_faq(hospital_id: str, body: FaqBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO faqs
               (id, hospital_id, category, question, answer, answer_ml, tags, priority, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            new_id, hospital_id, body.category or "",
            body.question, body.answer, body.answer_ml or "",
            json.dumps(body.tags or []), body.priority or 0,
            body.active if body.active is not None else True,
        )
    _invalidate(hospital_id)
    return {"id": new_id, "status": "created"}


@router.put("/hospitals/{hospital_id}/faqs/{faq_id}", dependencies=[Depends(_require_auth)])
async def update_faq(hospital_id: str, faq_id: str, body: FaqBody):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE faqs SET category=$1, question=$2, answer=$3, answer_ml=$4,
               tags=$5, priority=$6, active=$7
               WHERE id=$8 AND hospital_id=$9""",
            body.category or "", body.question, body.answer, body.answer_ml or "",
            json.dumps(body.tags or []), body.priority or 0,
            body.active if body.active is not None else True,
            faq_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


@router.delete("/hospitals/{hospital_id}/faqs/{faq_id}", dependencies=[Depends(_require_auth)])
async def delete_faq(hospital_id: str, faq_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE faqs SET active=false WHERE id=$1 AND hospital_id=$2",
            faq_id, hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "deactivated"}


# ── Call Logs ─────────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/calls", dependencies=[Depends(_require_auth)])
async def list_calls(hospital_id: str, limit: int = 50):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT call_id, caller, started_at, ended_at, total_turns,
                      latency_avg_ms, intents, outcome, recording_url
               FROM call_logs WHERE hospital_id=$1
               ORDER BY started_at DESC LIMIT $2""",
            hospital_id, min(limit, 200),
        )
    return [
        {
            "call_id": r["call_id"],
            "caller": r["caller"] or "unknown",
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
            "total_turns": r["total_turns"] or 0,
            "latency_avg_ms": r["latency_avg_ms"] or 0,
            "intents": _maybe_json(r["intents"]) or [],
            "outcome": r["outcome"] or "unknown",
            "recording_url": r["recording_url"] or None,
        }
        for r in rows
    ]


@router.get("/hospitals/{hospital_id}/calls/{call_id}", dependencies=[Depends(_require_auth)])
async def get_call(hospital_id: str, call_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT call_id, caller, started_at, ended_at, total_turns,
                      latency_avg_ms, intents, outcome, transcript, recording_url
               FROM call_logs WHERE hospital_id=$1 AND call_id=$2""",
            hospital_id, call_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Call not found")
    return {
        "call_id": row["call_id"],
        "caller": row["caller"] or "unknown",
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "total_turns": row["total_turns"] or 0,
        "latency_avg_ms": row["latency_avg_ms"] or 0,
        "intents": _maybe_json(row["intents"]) or [],
        "outcome": row["outcome"] or "unknown",
        "transcript": _maybe_json(row["transcript"]) or [],
        "recording_url": row["recording_url"] or None,
    }


@router.get("/hospitals/{hospital_id}/stats", dependencies=[Depends(_require_auth)])
async def get_stats(hospital_id: str, days: int = 7):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT
                 COUNT(*)::int                          AS total_calls,
                 AVG(latency_avg_ms)::int               AS avg_latency_ms,
                 COUNT(*) FILTER (WHERE outcome='transferred')::int AS transfers,
                 AVG(total_turns)::float                AS avg_turns
               FROM call_logs
               WHERE hospital_id=$1
                 AND started_at > NOW() - ($2 || ' days')::interval""",
            hospital_id,
            str(int(days)),
        )
    return {
        "total_calls": row["total_calls"] or 0,
        "avg_latency_ms": row["avg_latency_ms"] or 0,
        "transfers": row["transfers"] or 0,
        "avg_turns": round(row["avg_turns"] or 0, 1),
        "days": days,
    }


# ── Appointments ─────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/appointments", dependencies=[Depends(_require_auth)])
async def list_appointments(hospital_id: str, status: str = "", limit: int = 50):
    pool = await _db()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT a.id, a.patient_name, a.patient_phone, a.slot_time,
                          a.status, a.workflow_status, a.notes, a.created_at,
                          a.reminder_sent, a.confirmation_sent, a.followup_sent,
                          a.doctor_id, a.dept_id,
                          d.name AS doctor_name, dep.name AS dept_name
                   FROM appointments a
                   LEFT JOIN doctors d ON a.doctor_id = d.id
                   LEFT JOIN departments dep ON a.dept_id = dep.id
                   WHERE a.hospital_id=$1 AND a.status=$2
                   ORDER BY a.slot_time DESC NULLS LAST
                   LIMIT $3""",
                hospital_id, status, min(limit, 200),
            )
        else:
            rows = await conn.fetch(
                """SELECT a.id, a.patient_name, a.patient_phone, a.slot_time,
                          a.status, a.workflow_status, a.notes, a.created_at,
                          a.reminder_sent, a.confirmation_sent, a.followup_sent,
                          a.doctor_id, a.dept_id,
                          d.name AS doctor_name, dep.name AS dept_name
                   FROM appointments a
                   LEFT JOIN doctors d ON a.doctor_id = d.id
                   LEFT JOIN departments dep ON a.dept_id = dep.id
                   WHERE a.hospital_id=$1
                   ORDER BY a.slot_time DESC NULLS LAST
                   LIMIT $2""",
                hospital_id, min(limit, 200),
            )
    return [
        {
            "id": str(r["id"]),
            "patient_name": r["patient_name"] or "",
            "patient_phone": r["patient_phone"] or "",
            "slot_time": r["slot_time"].isoformat() if r["slot_time"] else None,
            "appointment_date": r["slot_time"].date().isoformat() if r["slot_time"] else None,
            "appointment_time": r["slot_time"].strftime("%H:%M:%S") if r["slot_time"] else None,
            "status": r["status"] or "requested",
            "workflow_status": r["workflow_status"] or None,
            "reminder_sent": bool(r["reminder_sent"]),
            "confirmation_sent": bool(r["confirmation_sent"]),
            "followup_sent": bool(r["followup_sent"]),
            "doctor_id": str(r["doctor_id"]) if r["doctor_id"] else None,
            "dept_id": str(r["dept_id"]) if r["dept_id"] else None,
            "notes": r["notes"] or "",
            "doctor_name": r["doctor_name"] or "",
            "dept_name": r["dept_name"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


class ApptStatusBody(BaseModel):
    status: str


@router.put("/hospitals/{hospital_id}/appointments/{appt_id}/status", dependencies=[Depends(_require_auth)])
async def update_appointment_status(hospital_id: str, appt_id: str, body: ApptStatusBody):
    allowed = {"requested", "confirmed", "cancelled", "completed", "no_show"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(allowed))}")
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET status=$1 WHERE id=$2 AND hospital_id=$3",
            body.status, appt_id, hospital_id,
        )
    return {"status": "updated", "appointment_status": body.status}


@router.post(
    "/hospitals/{hospital_id}/appointments/{appt_id}/confirm-payment",
    dependencies=[Depends(_require_auth)],
)
async def confirm_payment(hospital_id: str, appt_id: str):
    """Staff confirms an offline payment: activates the queue token and notifies
    the patient over WhatsApp/SMS with their token number."""
    from src.db.queries import activate_appointment_token

    info = await activate_appointment_token(appt_id, hospital_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Appointment not found")

    # Notify the patient (best-effort, non-blocking on failure).
    try:
        slot = info["slot_time"]
        date_s = slot.date().isoformat() if slot else ""
        time_s = slot.strftime("%H:%M") if slot else ""
        hosp = await _hospital_name(hospital_id)
        from src.services.whatsapp_service import get_messenger
        if info["patient_phone"]:
            await get_messenger().send_token_active(
                phone=info["patient_phone"],
                hospital_name=hosp,
                patient_name=info["patient_name"],
                doctor_name=info["doctor_name"],
                date=date_s,
                time=time_s,
                token_number=info["token_number"],
            )
    except Exception as exc:
        logger.warning("confirm_payment_notify_failed", error=str(exc))

    return {
        "status": "confirmed",
        "payment_status": "paid",
        "token_number": info["token_number"],
        "token_active": True,
    }


async def _hospital_name(hospital_id: str) -> str:
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT name FROM hospitals WHERE id=$1", hospital_id)
    return (row["name"] if row else "") or "the hospital"


# ── Callbacks ─────────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/callbacks", dependencies=[Depends(_require_auth)])
async def list_callbacks(hospital_id: str, status: str = "", limit: int = 50):
    pool = await _db()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT id, patient_name, patient_phone, reason, status,
                          preferred_time, created_at, attempted_at
                   FROM callbacks
                   WHERE hospital_id=$1 AND status=$2
                   ORDER BY created_at DESC LIMIT $3""",
                hospital_id, status, min(limit, 200),
            )
        else:
            rows = await conn.fetch(
                """SELECT id, patient_name, patient_phone, reason, status,
                          preferred_time, created_at, attempted_at
                   FROM callbacks
                   WHERE hospital_id=$1
                   ORDER BY created_at DESC LIMIT $2""",
                hospital_id, min(limit, 200),
            )
    return [
        {
            "id": str(r["id"]),
            "patient_name": r["patient_name"] or "",
            "patient_phone": r["patient_phone"] or "",
            "reason": r["reason"] or "",
            "status": r["status"] or "pending",
            "preferred_time": r["preferred_time"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "attempted_at": r["attempted_at"].isoformat() if r["attempted_at"] else None,
        }
        for r in rows
    ]


# ── Telephony Status ──────────────────────────────────────────────────────────

@router.get("/telephony/status", dependencies=[Depends(_require_auth)])
async def telephony_status(hospital_id: str = ""):
    """
    Returns the configuration status of each telephony component.
    Missing env vars show as unconfigured — no errors are raised.
    """
    lk_url = bool(getattr(settings, "LIVEKIT_URL", ""))
    lk_key = bool(getattr(settings, "LIVEKIT_API_KEY", ""))
    lk_secret = bool(getattr(settings, "LIVEKIT_API_SECRET", ""))
    sip_outbound = bool(getattr(settings, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", ""))
    sip_host = bool(getattr(settings, "LIVEKIT_SIP_HOST", ""))
    plivo_id = bool(getattr(settings, "PLIVO_AUTH_ID", ""))
    plivo_token = bool(getattr(settings, "PLIVO_AUTH_TOKEN", ""))
    plivo_number = bool(getattr(settings, "PLIVO_PHONE_NUMBER", ""))
    exotel_key = bool(getattr(settings, "EXOTEL_API_KEY", ""))
    exotel_token = bool(getattr(settings, "EXOTEL_API_TOKEN", ""))
    exotel_phone = bool(getattr(settings, "EXOTEL_PHONE_NUMBER", ""))
    exotel_subdomain = bool(getattr(settings, "EXOTEL_SUBDOMAIN", ""))
    exotel_trunk_id = bool(getattr(settings, "LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID", ""))
    exotel_webhook_token = bool(getattr(settings, "EXOTEL_WEBHOOK_TOKEN", ""))
    vobiz_key = bool(getattr(settings, "VOBIZ_API_KEY", ""))
    vobiz_secret = bool(getattr(settings, "VOBIZ_API_SECRET", ""))
    vobiz_phone = bool(getattr(settings, "VOBIZ_PHONE_NUMBER", ""))
    vobiz_trunk = bool(getattr(settings, "LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID", ""))
    sarvam = bool(getattr(settings, "SARVAM_API_KEY", ""))
    groq = bool(getattr(settings, "GROQ_API_KEY", ""))

    hospital_did = ""
    if hospital_id:
        try:
            pool = await _db()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT plivo_number, vobiz_phone_number FROM hospitals WHERE id=$1", hospital_id
                )
            if row:
                hospital_did = row["vobiz_phone_number"] or row["plivo_number"] or ""
        except Exception:
            pass

    livekit_ok = lk_url and lk_key and lk_secret
    plivo_ok = plivo_id and plivo_token and plivo_number
    exotel_ok = exotel_key and exotel_token and exotel_phone and exotel_subdomain
    vobiz_ok = vobiz_key and vobiz_secret and vobiz_phone and vobiz_trunk
    carrier_ok = vobiz_ok or plivo_ok or exotel_ok
    sip_ready = livekit_ok and sip_host and (sip_outbound or vobiz_trunk)
    voice_ready = sarvam and groq

    return {
        "livekit": {
            "configured": livekit_ok,
            "url": lk_url,
            "api_key": lk_key,
            "api_secret": lk_secret,
            "sip_outbound_trunk": sip_outbound,
            "sip_host": sip_host,
        },
        "vobiz": {
            "configured": vobiz_ok,
            "api_key": vobiz_key,
            "api_secret": vobiz_secret,
            "phone_number": vobiz_phone,
            "sip_trunk_id": vobiz_trunk,
        },
        "plivo": {
            "configured": plivo_ok,
            "auth_id": plivo_id,
            "auth_token": plivo_token,
            "phone_number": plivo_number,
            "hospital_did": hospital_did or None,
        },
        "exotel": {
            "configured": exotel_ok,
            "api_key": exotel_key,
            "api_token": exotel_token,
            "phone_number": exotel_phone,
            "subdomain": exotel_subdomain,
            "sip_trunk_id": exotel_trunk_id,
            "webhook_token": exotel_webhook_token,
        },
        "voice_ai": {
            "configured": voice_ready,
            "sarvam_stt_tts": sarvam,
            "groq_llm": groq,
        },
        "overall": {
            "sip_calls_ready": sip_ready and carrier_ok,
            "voice_pipeline_ready": voice_ready,
            "inbound_ready": sip_ready and carrier_ok and bool(hospital_did),
            "outbound_ready": sip_ready and carrier_ok,
        },
        "missing": [
            k for k, v in {
                "LIVEKIT_URL": lk_url, "LIVEKIT_API_KEY": lk_key,
                "LIVEKIT_API_SECRET": lk_secret,
                "LIVEKIT_SIP_OUTBOUND_TRUNK_ID": sip_outbound,
                "LIVEKIT_SIP_HOST": sip_host,
                "PLIVO_AUTH_ID": plivo_id, "PLIVO_AUTH_TOKEN": plivo_token,
                "PLIVO_PHONE_NUMBER": plivo_number,
                "SARVAM_API_KEY": sarvam, "GROQ_API_KEY": groq,
            }.items() if not v
        ],
    }


# ── Cache Clear ───────────────────────────────────────────────────────────────

@router.post("/hospitals/{hospital_id}/cache/clear", dependencies=[Depends(_require_auth)])
async def clear_cache(hospital_id: str):
    _invalidate(hospital_id)
    return {"status": "cache_cleared", "hospital_id": hospital_id}


# ── Hospital Wizard ───────────────────────────────────────────────────────────
# Single API call creates a fully configured hospital: departments, doctors,
# schedules, FAQs, emergency contacts, and optionally provisions a Plivo number.
# This is an Arteq-internal tool — only Arteq staff use it to onboard clients.

class _ScheduleIn(BaseModel):
    day_of_week: int        # 0=Sun … 6=Sat
    start_time: str         # "HH:MM"
    end_time: str           # "HH:MM"
    room: Optional[str] = ""


class _DoctorIn(BaseModel):
    name: str
    name_ml: Optional[str] = ""
    specialty: Optional[str] = ""
    qualifications: Optional[str] = ""
    schedules: list[_ScheduleIn] = []


class _DeptIn(BaseModel):
    name: str
    name_ml: Optional[str] = ""
    floor: Optional[str] = ""
    location_hint: Optional[str] = ""
    phone_ext: Optional[str] = ""
    doctors: list[_DoctorIn] = []


class _FaqIn(BaseModel):
    category: Optional[str] = "general"
    question: str
    answer: str
    answer_ml: Optional[str] = ""
    tags: Optional[list] = []
    priority: Optional[int] = 0


class _EmergencyIn(BaseModel):
    label: str
    label_ml: Optional[str] = ""
    phone: str
    priority: Optional[int] = 0


class HospitalWizardIn(BaseModel):
    name: str
    name_ml: Optional[str] = ""
    address: Optional[str] = ""
    phone: Optional[str] = ""
    slug: Optional[str] = None          # auto-derived from name if omitted
    tier: Optional[str] = "hospital"    # "clinic" | "hospital"
    hours: Optional[dict] = None
    departments: list[_DeptIn] = []
    faqs: list[_FaqIn] = []
    emergency_contacts: list[_EmergencyIn] = []
    provision_plivo_number: bool = False


@router.post("/hospitals/wizard", dependencies=[Depends(_require_super)])
async def hospital_wizard(body: HospitalWizardIn):
    """
    One-shot hospital onboarding: creates the hospital and all its data,
    then optionally provisions a Plivo phone number.
    """
    hospital_id = str(uuid.uuid4())
    slug = body.slug or _derive_slug(body.name)
    pool = await _db()

    async with pool.acquire() as conn:
        # Hospital row
        tier = body.tier if body.tier in ("clinic", "hospital") else "hospital"
        await conn.execute(
            """INSERT INTO hospitals
               (id, name, name_ml, address, phone, hours, active, slug, tier)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            hospital_id, body.name, body.name_ml or "",
            body.address or "", body.phone or "",
            json.dumps(body.hours or {}), True, slug, tier,
        )

        # Departments + doctors + schedules
        for dept in body.departments:
            dept_id = str(uuid.uuid4())
            await conn.execute(
                """INSERT INTO departments
                   (id, hospital_id, name, name_ml, floor, location_hint, phone_ext, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                dept_id, hospital_id, dept.name, dept.name_ml or "",
                dept.floor or "", dept.location_hint or "", dept.phone_ext or "", True,
            )
            for doc in dept.doctors:
                doc_id = str(uuid.uuid4())
                await conn.execute(
                    """INSERT INTO doctors
                       (id, hospital_id, dept_id, name, name_ml, specialty, qualifications, active)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    doc_id, hospital_id, dept_id, doc.name, doc.name_ml or "",
                    doc.specialty or "", doc.qualifications or "", True,
                )
                for sched in doc.schedules:
                    await conn.execute(
                        """INSERT INTO schedules
                           (id, doctor_id, hospital_id, day_of_week, start_time, end_time, room, active)
                           VALUES ($1,$2,$3,$4,$5::time,$6::time,$7,$8)""",
                        str(uuid.uuid4()), doc_id, hospital_id,
                        sched.day_of_week, sched.start_time, sched.end_time,
                        sched.room or "", True,
                    )

        # FAQs
        for faq in body.faqs:
            await conn.execute(
                """INSERT INTO faqs
                   (id, hospital_id, category, question, answer, answer_ml, tags, priority, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                str(uuid.uuid4()), hospital_id, faq.category or "general",
                faq.question, faq.answer, faq.answer_ml or "",
                json.dumps(faq.tags or []), faq.priority or 0, True,
            )

        # Emergency contacts
        for ec in body.emergency_contacts:
            await conn.execute(
                """INSERT INTO emergency_contacts
                   (id, hospital_id, label, label_ml, phone, priority, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                str(uuid.uuid4()), hospital_id, ec.label, ec.label_ml or "",
                ec.phone, ec.priority or 0, True,
            )

    _invalidate(hospital_id)

    plivo_number = None
    if body.provision_plivo_number:
        try:
            from src.services.plivo_provisioning import provision_number_for_hospital
            plivo_number = await provision_number_for_hospital(slug)
            if plivo_number:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE hospitals SET plivo_number=$1 WHERE id=$2",
                        plivo_number, hospital_id,
                    )
                try:
                    from src.services.livekit_sip import setup_hospital_inbound
                    await setup_hospital_inbound(slug, plivo_number)
                except Exception as sip_exc:
                    logger.warning("wizard_sip_setup_failed", error=str(sip_exc))
        except Exception as e:
            logger.warning("wizard_plivo_provision_failed", error=str(e))

    return {
        "hospital_id": hospital_id,
        "slug": slug,
        "plivo_number": plivo_number or "",
        "departments": len(body.departments),
        "faqs": len(body.faqs),
        "emergency_contacts": len(body.emergency_contacts),
        "status": "created",
        "bsnl_forward_code": f"**21*{plivo_number}#" if plivo_number else "",
    }


@router.post("/hospitals/{hospital_id}/provision-number", dependencies=[Depends(_require_auth)])
async def provision_plivo_number(hospital_id: str):
    """Buy and configure a Plivo number for an existing hospital."""
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT slug, plivo_number FROM hospitals WHERE id=$1", hospital_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hospital not found")
    if row["plivo_number"]:
        return {"plivo_number": row["plivo_number"], "status": "already_provisioned"}

    slug = row["slug"]
    if not slug:
        raise HTTPException(status_code=400, detail="Hospital has no slug — update it first")

    from src.services.plivo_provisioning import provision_number_for_hospital
    plivo_number = await provision_number_for_hospital(slug)
    if not plivo_number:
        raise HTTPException(status_code=502, detail="Could not provision Plivo number")

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hospitals SET plivo_number=$1 WHERE id=$2", plivo_number, hospital_id
        )
    _invalidate(hospital_id)

    async def _setup_sip(s: str, num: str) -> None:
        try:
            from src.services.livekit_sip import setup_hospital_inbound
            await setup_hospital_inbound(s, num)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provision_sip_setup_failed", error=str(exc))

    asyncio.create_task(_setup_sip(slug, plivo_number))

    return {
        "plivo_number": plivo_number,
        "bsnl_forward_code": f"**21*{plivo_number}#",
        "status": "provisioned",
    }


@router.get("/hospitals/{hospital_id}/setup-status", dependencies=[Depends(_require_auth)])
async def setup_status(hospital_id: str):
    """Check how complete a hospital's setup is — useful after wizard onboarding."""
    pool = await _db()
    async with pool.acquire() as conn:
        hosp = await conn.fetchrow(
            "SELECT name, slug, plivo_number FROM hospitals WHERE id=$1", hospital_id
        )
        if not hosp:
            raise HTTPException(status_code=404, detail="Hospital not found")
        dept_count = await conn.fetchval(
            "SELECT COUNT(*) FROM departments WHERE hospital_id=$1 AND active=true", hospital_id
        )
        doctor_count = await conn.fetchval(
            "SELECT COUNT(*) FROM doctors WHERE hospital_id=$1 AND active=true", hospital_id
        )
        faq_count = await conn.fetchval(
            "SELECT COUNT(*) FROM faqs WHERE hospital_id=$1 AND active=true", hospital_id
        )
        ec_count = await conn.fetchval(
            "SELECT COUNT(*) FROM emergency_contacts WHERE hospital_id=$1 AND active=true",
            hospital_id,
        )

    checks = {
        "has_slug": bool(hosp["slug"]),
        "has_plivo_number": bool(hosp["plivo_number"]),
        "has_departments": dept_count > 0,
        "has_doctors": doctor_count > 0,
        "has_faqs": faq_count > 0,
        "has_emergency_contacts": ec_count > 0,
    }
    return {
        "hospital_id": hospital_id,
        "name": hosp["name"],
        "slug": hosp["slug"] or "",
        "plivo_number": hosp["plivo_number"] or "",
        "bsnl_forward_code": f"**21*{hosp['plivo_number']}#" if hosp["plivo_number"] else "",
        "checks": checks,
        "ready": all(checks.values()),
        "counts": {
            "departments": dept_count,
            "doctors": doctor_count,
            "faqs": faq_count,
            "emergency_contacts": ec_count,
        },
    }


# ── SIP provisioning ─────────────────────────────────────────────────────────

@router.post("/sip/setup", dependencies=[Depends(_require_super)])
async def sip_setup():
    """
    One-time SIP trunk provisioning. Run this once after first deployment.

    Creates:
    • One Plivo SIP outbound trunk in LiveKit (for reminder/confirmation calls)
    • One SIP inbound trunk + dispatch rule per hospital that has a Plivo DID

    Returns trunk IDs. Copy the outbound_trunk_id value and set it as
    LIVEKIT_SIP_OUTBOUND_TRUNK_ID in your Render environment variables,
    then redeploy both services.

    Requires LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, PLIVO_AUTH_ID,
    PLIVO_AUTH_TOKEN, PLIVO_PHONE_NUMBER to be set.
    """
    # Pre-flight: verify required env vars are set
    missing = [
        k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
                    "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER")
        if not getattr(settings, k, "")
    ]
    if missing:
        return {
            "status": "not_configured",
            "missing_env_vars": missing,
            "message": "Set the listed environment variables in your hosting platform, then call this endpoint again.",
        }

    try:
        from src.services.livekit_sip import setup_sip_outbound_trunk, setup_hospital_inbound
    except ImportError:
        raise HTTPException(status_code=501, detail="livekit package not installed")

    # Outbound trunk (one global, uses Plivo credentials)
    outbound_trunk_id = await setup_sip_outbound_trunk()

    # Inbound trunk per hospital with a provisioned DID
    pool = await _db()
    async with pool.acquire() as conn:
        hospitals = await conn.fetch(
            "SELECT id, slug, plivo_number FROM hospitals "
            "WHERE plivo_number IS NOT NULL AND plivo_number != '' "
            "ORDER BY created_at"
        )

    inbound = []
    for h in hospitals:
        slug = h["slug"] or str(h["id"])
        trunk_id, rule_id = await setup_hospital_inbound(slug, h["plivo_number"])
        inbound.append({
            "hospital_id": str(h["id"]),
            "slug": slug,
            "plivo_number": h["plivo_number"],
            "sip_trunk_id": trunk_id,
            "dispatch_rule_id": rule_id,
            "ok": bool(trunk_id),
        })

    return {
        "outbound_trunk_id": outbound_trunk_id,
        "inbound_trunks": inbound,
        "sip_host": settings.LIVEKIT_SIP_HOST or "(set LIVEKIT_SIP_HOST from LiveKit dashboard)",
        "next_steps": [
            f"1. Set LIVEKIT_SIP_OUTBOUND_TRUNK_ID={outbound_trunk_id} in Render env vars",
            "2. Get your LiveKit SIP host from the LiveKit Cloud dashboard → SIP → Inbound Trunks",
            "3. Set LIVEKIT_SIP_HOST=<your-sip-host> in Render env vars",
            "4. Redeploy both arteq-voice-agent and arteq-livekit-agent",
        ],
    }


@router.post("/sip/exotel/setup", dependencies=[Depends(_require_super)])
async def sip_exotel_setup():
    """
    One-time Exotel SIP trunk provisioning. Run after first deployment with Exotel.

    Creates:
    • One Exotel SIP outbound trunk in LiveKit (for reminder/confirmation calls)
    • One SIP inbound trunk + dispatch rule per hospital with an Exotel number

    Returns the outbound trunk ID. Set it as LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID
    in Render environment variables, then redeploy.

    Requires LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
    EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_PHONE_NUMBER.
    """
    missing = [
        k for k in (
            "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
            "EXOTEL_API_KEY", "EXOTEL_API_TOKEN", "EXOTEL_PHONE_NUMBER",
        )
        if not getattr(settings, k, "")
    ]
    if missing:
        return {
            "status": "not_configured",
            "missing_env_vars": missing,
            "message": "Set the listed environment variables, then call this endpoint again.",
        }

    try:
        from src.services.livekit_sip import (
            setup_sip_outbound_trunk_exotel,
            setup_hospital_inbound_exotel,
        )
    except ImportError:
        raise HTTPException(status_code=501, detail="livekit package not installed")

    outbound_trunk_id = await setup_sip_outbound_trunk_exotel()

    pool = await _db()
    async with pool.acquire() as conn:
        hospitals = await conn.fetch(
            "SELECT id, slug, exotel_number FROM hospitals "
            "WHERE exotel_number IS NOT NULL AND exotel_number != '' "
            "ORDER BY created_at"
        )

    inbound = []
    for h in hospitals:
        slug = h["slug"] or str(h["id"])
        trunk_id, rule_id = await setup_hospital_inbound_exotel(slug, h["exotel_number"])
        inbound.append({
            "hospital_id": str(h["id"]),
            "slug": slug,
            "exotel_number": h["exotel_number"],
            "sip_trunk_id": trunk_id,
            "dispatch_rule_id": rule_id,
            "ok": bool(trunk_id),
        })

    return {
        "outbound_trunk_id": outbound_trunk_id,
        "inbound_trunks": inbound,
        "sip_host": settings.LIVEKIT_SIP_HOST or "(set LIVEKIT_SIP_HOST from LiveKit dashboard)",
        "next_steps": [
            f"1. Set LIVEKIT_SIP_EXOTEL_OUTBOUND_TRUNK_ID={outbound_trunk_id} in Render env vars",
            "2. Configure each hospital's ExoPhone VoiceUrl to:",
            f"   POST {settings.PUBLIC_BASE_URL}/api/v1/call/inbound/exotel/<EXOTEL_WEBHOOK_TOKEN>/<slug>",
            "3. Redeploy both arteq-voice-agent and arteq-livekit-agent",
        ],
    }


# ── HIS Integration ──────────────────────────────────────────────────────────

class HisConfigBody(BaseModel):
    enabled: bool = False
    type: str = "generic_rest"       # "generic_rest" | "fhir"
    base_url: str = ""
    auth: Optional[dict] = None      # {"type": "bearer"|"api_key"|"basic", "value": "..."}
    endpoints: Optional[dict] = None  # for generic_rest
    field_map: Optional[dict] = None  # for generic_rest
    practitioner_map: Optional[dict] = None  # for fhir
    timeout_seconds: Optional[int] = 8


@router.get("/hospitals/{hospital_id}/his-config", dependencies=[Depends(_require_auth)])
async def get_his_config(hospital_id: str):
    """Return HIS config for the hospital. Auth value is masked for security."""
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT his_config FROM hospitals WHERE id=$1", hospital_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hospital not found")
    cfg = _maybe_json(row["his_config"]) or {}
    # Mask the auth value so it never leaves the server in plaintext
    if cfg.get("auth", {}).get("value"):
        cfg = {**cfg, "auth": {**cfg["auth"], "value": "••••••••"}}
    return cfg or {"enabled": False}


@router.put("/hospitals/{hospital_id}/his-config", dependencies=[Depends(_require_auth)])
async def update_his_config(hospital_id: str, body: HisConfigBody):
    """Save HIS config. Auth value of '••••••••' preserves the existing stored secret."""
    pool = await _db()

    # If the masked sentinel is sent back, keep the existing auth value
    existing_auth_value = None
    if body.auth and body.auth.get("value") == "••••••••":
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT his_config FROM hospitals WHERE id=$1", hospital_id
            )
        existing_cfg = _maybe_json(row["his_config"] if row else None) or {}
        existing_auth_value = existing_cfg.get("auth", {}).get("value", "")

    cfg: dict = {
        "enabled": body.enabled,
        "type": body.type,
        "base_url": body.base_url,
        "timeout_seconds": body.timeout_seconds or 8,
    }
    auth = body.auth or {}
    if existing_auth_value is not None:
        auth = {**auth, "value": existing_auth_value}
    cfg["auth"] = auth

    if body.endpoints is not None:
        cfg["endpoints"] = body.endpoints
    if body.field_map is not None:
        cfg["field_map"] = body.field_map
    if body.practitioner_map is not None:
        cfg["practitioner_map"] = body.practitioner_map

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE hospitals SET his_config=$1 WHERE id=$2",
            json.dumps(cfg), hospital_id,
        )

    # Bust in-process adapter cache
    try:
        from src.integrations.his.service import invalidate_his_cache
        invalidate_his_cache(hospital_id)
    except Exception:
        pass

    _invalidate(hospital_id)
    return {"status": "updated"}


@router.get("/hospitals/{hospital_id}/his-status", dependencies=[Depends(_require_auth)])
async def get_his_status(hospital_id: str):
    """Return HIS connectivity status (reachable / not configured / etc.)."""
    try:
        from src.integrations.his.service import his_status
        return await his_status(hospital_id)
    except Exception as exc:
        return {"configured": False, "enabled": False, "reachable": False, "error": str(exc)}


@router.post("/hospitals/{hospital_id}/his-config/test", dependencies=[Depends(_require_auth)])
async def test_his_connection(hospital_id: str):
    """Ping the HIS endpoint to verify connectivity."""
    try:
        from src.integrations.his.service import get_his_adapter, invalidate_his_cache
        # Force reload so we test with the latest saved config
        invalidate_his_cache(hospital_id)
        his = await get_his_adapter(hospital_id)
        if not his:
            return {"reachable": False, "reason": "HIS not configured or not enabled"}
        reachable = await his.ping()
        return {"reachable": reachable}
    except Exception as exc:
        return {"reachable": False, "reason": str(exc)}


# ── Tenants (multi-DB control plane) ────────────────────────────────────────────
#
# Each tenant (hospital/clinic) has its OWN Supabase database. The control DB
# (settings.DATABASE_URL) holds the registry that maps slug -> db_url + features.
# Onboarding: create the registry row, provision the tenant DB (run all
# migrations), then seed the hospital + departments/doctors/faqs into that DB.
# Features are auto-filled from the tier matrix and editable per tenant.

class TenantOnboardIn(BaseModel):
    # identity / persona
    name: str
    name_ml: Optional[str] = ""
    slug: Optional[str] = None              # auto-derived if omitted
    tier: Optional[str] = "hospital"        # "hospital" | "clinic"
    agent_name: Optional[str] = "Arya"
    agent_language: Optional[str] = "ml-IN"
    # contact / location
    address: Optional[str] = ""
    phone: Optional[str] = ""
    contact_person: Optional[str] = ""
    contact_phone: Optional[str] = ""
    plivo_number: Optional[str] = ""
    notes: Optional[str] = ""
    # the hospital's OWN Supabase connection string (admin pastes it)
    db_url: Optional[str] = ""
    # feature flag overrides on top of tier defaults; None = pure tier defaults
    features: Optional[dict] = None
    # optional seed data for the tenant DB
    hours: Optional[dict] = None
    departments: list[_DeptIn] = []
    faqs: list[_FaqIn] = []
    emergency_contacts: list[_EmergencyIn] = []


class TenantUpdateIn(BaseModel):
    name: Optional[str] = None
    name_ml: Optional[str] = None
    tier: Optional[str] = None
    db_url: Optional[str] = None
    plivo_number: Optional[str] = None
    agent_name: Optional[str] = None
    agent_language: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[bool] = None


class FeaturesIn(BaseModel):
    features: dict


async def _seed_tenant_db(db_url: str, slug: str, body: TenantOnboardIn) -> str:
    """Seed the hospital row + departments/doctors/faqs/emergency into the
    tenant's own DB. Returns the new hospital_id (within that DB)."""
    from src.tenancy.pools import tenant_pool

    hospital_id = str(uuid.uuid4())
    pool = await tenant_pool(db_url)
    async with pool.acquire() as conn:
        tier = body.tier if body.tier in ("clinic", "hospital") else "hospital"
        await conn.execute(
            """INSERT INTO hospitals
               (id, name, name_ml, address, phone, hours, active, slug, tier,
                agent_name, agent_language)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
            hospital_id, body.name, body.name_ml or "",
            body.address or "", body.phone or "",
            json.dumps(body.hours or {}), True, slug, tier,
            body.agent_name or "Arya", body.agent_language or "ml-IN",
        )
        for dept in body.departments:
            dept_id = str(uuid.uuid4())
            await conn.execute(
                """INSERT INTO departments
                   (id, hospital_id, name, name_ml, floor, location_hint, phone_ext, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                dept_id, hospital_id, dept.name, dept.name_ml or "",
                dept.floor or "", dept.location_hint or "", dept.phone_ext or "", True,
            )
            for doc in dept.doctors:
                doc_id = str(uuid.uuid4())
                await conn.execute(
                    """INSERT INTO doctors
                       (id, hospital_id, dept_id, name, name_ml, specialty, qualifications, active)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                    doc_id, hospital_id, dept_id, doc.name, doc.name_ml or "",
                    doc.specialty or "", doc.qualifications or "", True,
                )
                for sched in doc.schedules:
                    await conn.execute(
                        """INSERT INTO schedules
                           (id, doctor_id, hospital_id, day_of_week, start_time, end_time, room, active)
                           VALUES ($1,$2,$3,$4,$5::time,$6::time,$7,$8)""",
                        str(uuid.uuid4()), doc_id, hospital_id,
                        sched.day_of_week, sched.start_time, sched.end_time,
                        sched.room or "", True,
                    )
        for faq in body.faqs:
            await conn.execute(
                """INSERT INTO faqs
                   (id, hospital_id, category, question, answer, answer_ml, tags, priority, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                str(uuid.uuid4()), hospital_id, faq.category or "general",
                faq.question, faq.answer, faq.answer_ml or "",
                json.dumps(faq.tags or []), faq.priority or 0, True,
            )
        for ec in body.emergency_contacts:
            await conn.execute(
                """INSERT INTO emergency_contacts
                   (id, hospital_id, label, label_ml, phone, priority, active)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                str(uuid.uuid4()), hospital_id, ec.label, ec.label_ml or "",
                ec.phone, ec.priority or 0, True,
            )
    return hospital_id


@router.get("/features/catalog", dependencies=[Depends(_require_auth)])
async def features_catalog():
    """The feature key->label list plus per-tier default maps, for the form."""
    from src.tenancy import features as feat
    return {
        "features": feat.FEATURES,
        "tier_defaults": {t: feat.default_features(t) for t in feat.TIER_DEFAULTS},
    }


@router.get("/tenants", dependencies=[Depends(_require_super)])
async def list_tenants_route(include_inactive: bool = True):
    from src.tenancy import registry
    tenants = await registry.list_tenants(include_inactive=include_inactive)
    for t in tenants:
        t["id"] = str(t["id"])
        if t.get("created_at"):
            t["created_at"] = t["created_at"].isoformat()
    return tenants


@router.get("/tenants/{slug}", dependencies=[Depends(_require_super)])
async def get_tenant_route(slug: str):
    from src.tenancy import registry
    t = await registry.get_tenant(slug)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t["id"] = str(t["id"])
    if t.get("created_at"):
        t["created_at"] = t["created_at"].isoformat()
    return t


@router.post("/tenants", dependencies=[Depends(_require_super)])
async def create_tenant_route(body: TenantOnboardIn):
    """Onboard a hospital/clinic: registry row (features auto from tier) +
    provision its own Supabase DB (run migrations) + seed hospital data."""
    from src.tenancy import registry
    from src.tenancy.pools import provision_tenant_db

    if not body.name:
        raise HTTPException(status_code=400, detail="name is required")
    slug = body.slug or _derive_slug(body.name)

    if await registry.get_tenant(slug):
        raise HTTPException(status_code=409, detail=f"Tenant slug '{slug}' already exists")

    migrations_applied = 0
    hospital_id = ""
    if body.db_url:
        try:
            migrations_applied = await provision_tenant_db(body.db_url)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach/provision tenant DB: {exc}",
            )
        try:
            hospital_id = await _seed_tenant_db(body.db_url, slug, body)
        except Exception as exc:
            logger.warning("tenant_seed_failed", slug=slug, error=str(exc))

    tenant = await registry.create_tenant(
        slug=slug,
        name=body.name,
        name_ml=body.name_ml or "",
        tier=body.tier or "hospital",
        db_url=body.db_url or "",
        features=body.features,
        plivo_number=body.plivo_number or "",
        agent_name=body.agent_name or "Arya",
        agent_language=body.agent_language or "ml-IN",
        address=body.address or "",
        phone=body.phone or "",
        contact_person=body.contact_person or "",
        contact_phone=body.contact_phone or "",
        notes=body.notes or "",
    )
    tenant["id"] = str(tenant["id"])
    if tenant.get("created_at"):
        tenant["created_at"] = tenant["created_at"].isoformat()
    return {
        "tenant": tenant,
        "slug": slug,
        "hospital_id": hospital_id,
        "migrations_applied": migrations_applied,
        "status": "created",
    }


@router.put("/tenants/{slug}", dependencies=[Depends(_require_super)])
async def update_tenant_route(slug: str, body: TenantUpdateIn):
    from src.tenancy import registry
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    t = await registry.update_tenant(slug, fields)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    t["id"] = str(t["id"])
    if t.get("created_at"):
        t["created_at"] = t["created_at"].isoformat()
    return t


@router.put("/tenants/{slug}/features", dependencies=[Depends(_require_super)])
async def set_tenant_features_route(slug: str, body: FeaturesIn):
    from src.tenancy import registry
    t = await registry.set_features(slug, body.features)
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"slug": slug, "features": t["features"]}


@router.delete("/tenants/{slug}", dependencies=[Depends(_require_super)])
async def deactivate_tenant_route(slug: str):
    from src.tenancy import registry
    ok = await registry.deactivate_tenant(slug)
    if not ok:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"slug": slug, "active": False}


# ── Patient Intake Workflow ───────────────────────────────────────────────────
# Front Desk section: Patients, Bookings & Tokens, WhatsApp feed.
# All routes enforce the same hospital-scoped tenant check as the core routes.


def _generate_patient_id(date_str: str, seq: int) -> str:
    return f"P-{date_str}-{seq:03d}"


def _generate_token_code() -> str:
    return f"TKN-{random.randint(1000, 9999)}"


async def _log_whatsapp(conn, hospital_id: str, phone: str, patient_name: str, body: str) -> str:
    wa_id = f"wa-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        """INSERT INTO whatsapp_messages (id, hospital_id, phone, patient_name, body)
           VALUES ($1, $2, $3, $4, $5)""",
        wa_id, hospital_id, phone, patient_name, body,
    )
    return wa_id


class PatientBody(BaseModel):
    name: str
    phone: str


@router.get("/hospitals/{hospital_id}/patients", dependencies=[Depends(_require_auth)])
async def list_patients(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, hospital_id, name, phone, created_at
               FROM patients WHERE hospital_id=$1 ORDER BY created_at DESC""",
            hospital_id,
        )
    return [
        {
            "id": r["id"],
            "hospital_id": str(r["hospital_id"]),
            "name": r["name"],
            "phone": r["phone"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


@router.post("/hospitals/{hospital_id}/patients", dependencies=[Depends(_require_auth)])
async def create_patient(hospital_id: str, body: PatientBody):
    if not body.name or not body.phone:
        raise HTTPException(status_code=400, detail="name and phone are required")

    pool = await _db()
    async with pool.acquire() as conn:
        date_str = datetime.now(timezone.utc).strftime("%y%m%d")
        seq = (await conn.fetchval(
            "SELECT COUNT(*) FROM patients WHERE hospital_id=$1 "
            "AND DATE(created_at AT TIME ZONE 'UTC') = CURRENT_DATE",
            hospital_id,
        ) or 0) + 1
        patient_id = _generate_patient_id(date_str, seq)

        await conn.execute(
            """INSERT INTO patients (id, hospital_id, name, phone)
               VALUES ($1, $2, $3, $4)""",
            patient_id, hospital_id, body.name, body.phone,
        )

        hosp_name = await conn.fetchval("SELECT name FROM hospitals WHERE id=$1", hospital_id) or "the hospital"

        # Log WhatsApp welcome
        welcome_body = (
            f"Hi {body.name}, welcome to {hosp_name}! "
            f"Your patient ID is {patient_id}. "
            "We're glad to have you with us."
        )
        await _log_whatsapp(conn, hospital_id, body.phone, body.name, welcome_body)

        # Log outbound welcome call intent (actual scheduling handled by the AI layer)
        call_id = f"welcome-{patient_id}"
        await conn.execute(
            """INSERT INTO call_logs (hospital_id, call_id, caller, started_at, outcome)
               VALUES ($1, $2, $3, NOW(), 'outbound_welcome_queued')
               ON CONFLICT (call_id) DO NOTHING""",
            hospital_id, call_id, body.phone,
        )

        row = await conn.fetchrow(
            "SELECT id, hospital_id, name, phone, created_at FROM patients WHERE id=$1",
            patient_id,
        )

    return {
        "id": row["id"],
        "hospital_id": str(row["hospital_id"]),
        "name": row["name"],
        "phone": row["phone"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


# ── Bookings ──────────────────────────────────────────────────────────────────

class BookingBody(BaseModel):
    patient_id: str
    slot: str                      # ISO8601 datetime
    payment_mode: str              # "pay_now" | "pay_later"
    amount_paise: int = 0


class BookingStatusBody(BaseModel):
    status: str


def _booking_row(r) -> dict:
    token = None
    if r["token_code"]:
        token = {"code": r["token_code"], "active": r["token_active"]}
    return {
        "id": r["id"],
        "hospital_id": str(r["hospital_id"]),
        "patient_id": r["patient_id"],
        "patient_name": r["patient_name"] or "",
        "patient_phone": r["patient_phone"] or "",
        "slot": r["slot"].isoformat() if r["slot"] else None,
        "payment_mode": r["payment_mode"],
        "status": r["status"],
        "amount_paise": r["amount_paise"],
        "token": token,
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
    }


_BOOKING_SELECT = """
    SELECT b.id, b.hospital_id, b.patient_id,
           p.name AS patient_name, p.phone AS patient_phone,
           b.slot, b.payment_mode, b.status, b.amount_paise,
           b.token_code, b.token_active, b.created_at
    FROM bookings b
    LEFT JOIN patients p ON p.id = b.patient_id
"""


@router.get("/hospitals/{hospital_id}/bookings", dependencies=[Depends(_require_auth)])
async def list_bookings(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _BOOKING_SELECT + "WHERE b.hospital_id=$1 ORDER BY b.created_at DESC",
            hospital_id,
        )
    return [_booking_row(r) for r in rows]


@router.post("/hospitals/{hospital_id}/bookings", dependencies=[Depends(_require_auth)])
async def create_booking(hospital_id: str, body: BookingBody):
    if body.payment_mode not in ("pay_now", "pay_later"):
        raise HTTPException(status_code=400, detail="payment_mode must be pay_now or pay_later")

    try:
        slot_dt = datetime.fromisoformat(body.slot.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="slot must be a valid ISO8601 datetime")

    pool = await _db()
    async with pool.acquire() as conn:
        patient = await conn.fetchrow(
            "SELECT name, phone FROM patients WHERE id=$1 AND hospital_id=$2",
            body.patient_id, hospital_id,
        )
        if not patient:
            raise HTTPException(status_code=404, detail="Patient not found in this hospital")

        booking_id = f"appt-{secrets.token_hex(3)}"
        token_code = None
        token_active = False
        status = "pending_payment"

        if body.payment_mode == "pay_later":
            status = "awaiting_confirmation"
            token_code = _generate_token_code()

        await conn.execute(
            """INSERT INTO bookings
               (id, hospital_id, patient_id, slot, payment_mode, status, amount_paise,
                token_code, token_active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            booking_id, hospital_id, body.patient_id, slot_dt,
            body.payment_mode, status, body.amount_paise, token_code, token_active,
        )

        hosp_name = await conn.fetchval("SELECT name FROM hospitals WHERE id=$1", hospital_id) or "the hospital"

        # Send WhatsApp for pay_later (token issued)
        if body.payment_mode == "pay_later" and token_code:
            wa_body = (
                f"Hi {patient['name']}, your appointment at {hosp_name} "
                f"on {slot_dt.strftime('%d %b %Y at %I:%M %p')} has been booked. "
                f"Your token is {token_code} (inactive until confirmed). "
                "We'll activate it closer to your appointment."
            )
            await _log_whatsapp(conn, hospital_id, patient["phone"], patient["name"], wa_body)

        row = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1",
            booking_id,
        )

    return _booking_row(row)


@router.put(
    "/hospitals/{hospital_id}/bookings/{booking_id}/status",
    dependencies=[Depends(_require_auth)],
)
async def update_booking_status(hospital_id: str, booking_id: str, body: BookingStatusBody):
    allowed_statuses = {"confirmed", "cancelled", "pending_payment", "awaiting_confirmation", "completed"}
    if body.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(allowed_statuses))}")

    pool = await _db()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1 AND b.hospital_id=$2",
            booking_id, hospital_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Booking not found")

        await conn.execute(
            "UPDATE bookings SET status=$1 WHERE id=$2 AND hospital_id=$3",
            body.status, booking_id, hospital_id,
        )

        hosp_name = await conn.fetchval("SELECT name FROM hospitals WHERE id=$1", hospital_id) or "the hospital"
        phone = existing["patient_phone"] or ""
        name = existing["patient_name"] or ""
        slot = existing["slot"]
        slot_str = slot.strftime("%d %b %Y at %I:%M %p") if slot else "your appointment"

        if body.status == "confirmed" and phone:
            wa_body = (
                f"Hi {name}, your payment for the appointment at {hosp_name} "
                f"on {slot_str} has been received. Your booking is confirmed!"
            )
            await _log_whatsapp(conn, hospital_id, phone, name, wa_body)
        elif body.status == "cancelled" and phone:
            wa_body = (
                f"Hi {name}, your appointment at {hosp_name} "
                f"on {slot_str} has been cancelled. "
                "Please contact us if you have any questions."
            )
            await _log_whatsapp(conn, hospital_id, phone, name, wa_body)

        row = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1",
            booking_id,
        )

    return _booking_row(row)


@router.post(
    "/hospitals/{hospital_id}/bookings/{booking_id}/change-token",
    dependencies=[Depends(_require_auth)],
)
async def change_booking_token(hospital_id: str, booking_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1 AND b.hospital_id=$2",
            booking_id, hospital_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Booking not found")

        new_token = _generate_token_code()
        await conn.execute(
            "UPDATE bookings SET token_code=$1 WHERE id=$2 AND hospital_id=$3",
            new_token, booking_id, hospital_id,
        )

        phone = existing["patient_phone"] or ""
        name = existing["patient_name"] or ""
        hosp_name = await conn.fetchval("SELECT name FROM hospitals WHERE id=$1", hospital_id) or "the hospital"
        slot = existing["slot"]
        slot_str = slot.strftime("%d %b %Y at %I:%M %p") if slot else "your appointment"

        if phone:
            wa_body = (
                f"Hi {name}, your token for the appointment at {hosp_name} "
                f"on {slot_str} has been updated. Your new token is {new_token}."
            )
            await _log_whatsapp(conn, hospital_id, phone, name, wa_body)

        row = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1",
            booking_id,
        )

    return _booking_row(row)


@router.post(
    "/hospitals/{hospital_id}/bookings/{booking_id}/confirm-call",
    dependencies=[Depends(_require_auth)],
)
async def booking_confirm_call(hospital_id: str, booking_id: str):
    """Simulate the ~1-week-prior AI confirmation call: confirms the booking,
    activates the token, logs the call, and sends a WhatsApp notification."""
    pool = await _db()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1 AND b.hospital_id=$2",
            booking_id, hospital_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Booking not found")

        await conn.execute(
            "UPDATE bookings SET status='confirmed', token_active=true WHERE id=$1 AND hospital_id=$2",
            booking_id, hospital_id,
        )

        # Log the AI confirmation call
        call_id = f"confirm-{booking_id}"
        await conn.execute(
            """INSERT INTO call_logs (hospital_id, call_id, caller, started_at, outcome)
               VALUES ($1, $2, $3, NOW(), 'outbound_confirmation')
               ON CONFLICT (call_id) DO NOTHING""",
            hospital_id, call_id, existing["patient_phone"] or "",
        )

        phone = existing["patient_phone"] or ""
        name = existing["patient_name"] or ""
        token_code = existing["token_code"] or ""
        hosp_name = await conn.fetchval("SELECT name FROM hospitals WHERE id=$1", hospital_id) or "the hospital"
        slot = existing["slot"]
        slot_str = slot.strftime("%d %b %Y at %I:%M %p") if slot else "your appointment"

        if phone:
            wa_body = (
                f"Hi {name}, your appointment at {hosp_name} on {slot_str} is confirmed! "
                f"Your token {token_code} is now active. Please arrive 10 minutes early."
            )
            await _log_whatsapp(conn, hospital_id, phone, name, wa_body)

        row = await conn.fetchrow(
            _BOOKING_SELECT + "WHERE b.id=$1",
            booking_id,
        )

    return _booking_row(row)


# ── WhatsApp Feed ─────────────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/whatsapp", dependencies=[Depends(_require_auth)])
async def list_whatsapp(hospital_id: str, limit: int = 100):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, hospital_id, phone, patient_name, body, at
               FROM whatsapp_messages WHERE hospital_id=$1
               ORDER BY at DESC LIMIT $2""",
            hospital_id, min(limit, 500),
        )
    return [
        {
            "id": r["id"],
            "hospital_id": str(r["hospital_id"]),
            "phone": r["phone"],
            "patient_name": r["patient_name"] or "",
            "body": r["body"],
            "at": r["at"].isoformat() if r["at"] else None,
        }
        for r in rows
    ]


# ── Trial / Subscription ──────────────────────────────────────────────────────

@router.get("/hospitals/{hospital_id}/trial-status", dependencies=[Depends(_require_auth)])
async def get_trial_status(hospital_id: str):
    """Return trial/subscription status for a hospital."""
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT subscription_status, trial_started_at, trial_expires_at, activated_at
               FROM hospitals WHERE id = $1""",
            hospital_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Hospital not found")
    now = datetime.now(timezone.utc)
    expires = row["trial_expires_at"]
    days_remaining = None
    if row["subscription_status"] == "trial" and expires:
        delta = (expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires) - now
        days_remaining = max(0, delta.days)
    return {
        "subscription_status": row["subscription_status"],
        "trial_started_at": row["trial_started_at"].isoformat() if row["trial_started_at"] else None,
        "trial_expires_at": expires.isoformat() if expires else None,
        "trial_days_remaining": days_remaining,
        "activated_at": row["activated_at"].isoformat() if row["activated_at"] else None,
        "is_trial": row["subscription_status"] == "trial",
        "is_expired": row["subscription_status"] == "expired" or (
            row["subscription_status"] == "trial" and days_remaining == 0
        ),
    }


class ActivateBody(BaseModel):
    plan: Optional[str] = "active"


@router.post("/hospitals/{hospital_id}/activate", dependencies=[Depends(_require_super)])
async def activate_hospital(hospital_id: str, body: ActivateBody):
    """Activate a hospital's subscription (super_admin only)."""
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM hospitals WHERE id=$1", hospital_id)
        if not row:
            raise HTTPException(status_code=404, detail="Hospital not found")
        await conn.execute(
            """UPDATE hospitals
               SET subscription_status=$1, activated_at=NOW()
               WHERE id=$2""",
            body.plan or "active", hospital_id,
        )
    _invalidate(hospital_id)
    return {"status": "activated", "subscription_status": body.plan or "active"}


# ── Vobiz SIP Provisioning ────────────────────────────────────────────────────

@router.post("/sip/vobiz/setup", dependencies=[Depends(_require_super)])
async def setup_vobiz(request: Request):
    """One-time Vobiz SIP trunk creation.

    Creates the outbound trunk and, for every hospital with a vobiz_phone_number,
    creates the inbound trunk + dispatch rule. Returns trunk IDs — save
    LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID to environment after this call.
    """
    from src.services.vobiz_sip import setup_vobiz_outbound_trunk, setup_hospital_inbound_vobiz

    outbound_trunk_id = await setup_vobiz_outbound_trunk()

    pool = await _db()
    async with pool.acquire() as conn:
        hospitals = await conn.fetch(
            "SELECT id, slug, vobiz_phone_number FROM hospitals WHERE active=true AND vobiz_phone_number IS NOT NULL"
        )

    inbound_results = []
    for h in hospitals:
        trunk_id, rule_id = await setup_hospital_inbound_vobiz(h["slug"], h["vobiz_phone_number"])
        if trunk_id:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE hospitals SET vobiz_inbound_trunk_id=$1, vobiz_outbound_trunk_id=$2 WHERE id=$3",
                    trunk_id, outbound_trunk_id, h["id"],
                )
            inbound_results.append({
                "hospital_id": str(h["id"]),
                "slug": h["slug"],
                "trunk_id": trunk_id,
                "dispatch_rule_id": rule_id,
            })

    return {
        "outbound_trunk_id": outbound_trunk_id,
        "message": "Set LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID to the outbound_trunk_id above",
        "hospitals_configured": inbound_results,
    }


@router.get("/sip/vobiz/status")
async def vobiz_sip_status():
    """Check Vobiz SIP configuration completeness."""
    api_key = bool(getattr(settings, "VOBIZ_API_KEY", ""))
    api_secret = bool(getattr(settings, "VOBIZ_API_SECRET", ""))
    phone = bool(getattr(settings, "VOBIZ_PHONE_NUMBER", ""))
    trunk = bool(getattr(settings, "LIVEKIT_SIP_VOBIZ_OUTBOUND_TRUNK_ID", ""))
    ok = all([api_key, api_secret, phone, trunk])
    return {
        "configured": ok,
        "vobiz_api_key": api_key,
        "vobiz_api_secret": api_secret,
        "vobiz_phone_number": phone,
        "outbound_trunk_id": trunk,
    }


# ── Doctor Availability ───────────────────────────────────────────────────────

VALID_AVAILABILITY_STATUSES = {"available", "busy", "delayed", "unavailable", "on_leave"}


class DoctorAvailabilityBody(BaseModel):
    status: str                    # 'available'|'busy'|'delayed'|'unavailable'|'on_leave'
    note: Optional[str] = None


@router.get(
    "/hospitals/{hospital_id}/doctors/{doctor_id}/availability",
    dependencies=[Depends(_require_auth)],
)
async def get_doctor_availability(hospital_id: str, doctor_id: str):
    """Return current availability status for a doctor plus recent event history."""
    pool = await _db()
    async with pool.acquire() as conn:
        doctor = await conn.fetchrow(
            "SELECT id, name, availability_status FROM doctors WHERE id=$1 AND hospital_id=$2",
            doctor_id, hospital_id,
        )
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")
        events = await conn.fetch(
            """SELECT id, status, note, changed_by, changed_at
               FROM doctor_availability_events
               WHERE doctor_id=$1 ORDER BY changed_at DESC LIMIT 20""",
            doctor_id,
        )
    return {
        "doctor_id": doctor_id,
        "name": doctor["name"],
        "availability_status": doctor["availability_status"],
        "events": [
            {
                "id": str(e["id"]),
                "doctor_id": doctor_id,
                "hospital_id": hospital_id,
                "status": e["status"],
                "note": e["note"],
                "created_at": e["changed_at"].isoformat() if e["changed_at"] else None,
            }
            for e in events
        ],
    }


@router.put(
    "/hospitals/{hospital_id}/doctors/{doctor_id}/availability",
    dependencies=[Depends(_require_auth)],
)
async def set_doctor_availability(
    hospital_id: str,
    doctor_id: str,
    body: DoctorAvailabilityBody,
    payload: dict = Depends(_require_auth),
):
    """Update doctor availability and trigger patient notifications if needed.

    When status is 'delayed' or 'unavailable', appointment-day patients for this
    doctor who haven't been notified yet will be called by the doctor_availability_loop
    on its next pass (within 10 minutes). The loop reads the doctor's current
    availability_status column, so updating it here is sufficient.
    """
    status = body.status.lower()
    if status not in VALID_AVAILABILITY_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_AVAILABILITY_STATUSES))}",
        )

    actor = payload.get("sub", "staff")
    pool = await _db()
    async with pool.acquire() as conn:
        doctor = await conn.fetchrow(
            "SELECT id, name FROM doctors WHERE id=$1 AND hospital_id=$2",
            doctor_id, hospital_id,
        )
        if not doctor:
            raise HTTPException(status_code=404, detail="Doctor not found")

        await conn.execute(
            "UPDATE doctors SET availability_status=$1 WHERE id=$2",
            status, doctor_id,
        )
        await conn.execute(
            """INSERT INTO doctor_availability_events
                   (id, doctor_id, hospital_id, status, note, changed_by)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            str(uuid.uuid4()), doctor_id, hospital_id, status, body.note, actor,
        )

        # Count today's appointments affected (for informational response)
        affected = await conn.fetchval(
            """SELECT COUNT(*) FROM appointments
               WHERE doctor_id=$1
                 AND DATE(slot_time AT TIME ZONE 'Asia/Kolkata') = CURRENT_DATE
                 AND status IN ('booked', 'confirmed')
                 AND doctor_availability_notified = false""",
            doctor_id,
        )

    return {
        "doctor_id": doctor_id,
        "name": doctor["name"],
        "availability_status": status,
        "affected_appointments_today": affected,
        "note": "Patients will be called by the availability loop within 10 minutes"
        if status != "available" else "Status updated",
    }


# ── Appointment Events (audit trail) ─────────────────────────────────────────

@router.get(
    "/hospitals/{hospital_id}/appointments/{appointment_id}/events",
    dependencies=[Depends(_require_auth)],
)
async def get_appointment_events(hospital_id: str, appointment_id: str):
    """Return the full audit trail for one appointment."""
    pool = await _db()
    async with pool.acquire() as conn:
        appt = await conn.fetchrow(
            """SELECT id, patient_name, patient_phone, slot_time, status, workflow_status,
                      confirmation_attempts, reminder_attempts, doctor_availability_attempts
               FROM appointments WHERE id=$1 AND hospital_id=$2""",
            appointment_id, hospital_id,
        )
        if not appt:
            raise HTTPException(status_code=404, detail="Appointment not found")
        events = await conn.fetch(
            """SELECT id, event_type, old_status, new_status, note, actor, created_at
               FROM appointment_events
               WHERE appointment_id=$1 ORDER BY created_at ASC""",
            appointment_id,
        )
    return [
        {
            "id": str(e["id"]),
            "appointment_id": appointment_id,
            "hospital_id": hospital_id,
            "event_type": e["event_type"],
            "detail": e["note"],
            "created_at": e["created_at"].isoformat() if e["created_at"] else None,
        }
        for e in events
    ]


# ── Utility ───────────────────────────────────────────────────────────────────

def _maybe_json(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value
