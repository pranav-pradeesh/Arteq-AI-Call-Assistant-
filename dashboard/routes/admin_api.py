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
import re
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
#
# Two token shapes share DASHBOARD_JWT_SECRET:
#   * legacy single-password login  → {"sub": "admin", "role": "super_admin"}
#   * per-user RBAC login (additions/routes/users_api.py)
#                                   → {"sub": <email>, "role": <role>}
# Both are accepted everywhere; non-super roles are additionally scoped to the
# hospitals listed for them in user_tenants (see _require_hospital_access).

_RBAC_ROLES = {"super_admin", "tenant_admin", "viewer"}


class LoginIn(BaseModel):
    password: str


def _create_token() -> str:
    exp = datetime.now(timezone.utc) + timedelta(
        minutes=getattr(settings, "DASHBOARD_JWT_EXPIRE_MINUTES", 720)
    )
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    return jwt.encode(
        {"sub": "admin", "role": "super_admin", "exp": exp}, secret, algorithm=ALGORITHM
    )


def _decode_token(credentials: Optional[HTTPAuthorizationCredentials]) -> dict:
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("sub") != "admin" and payload.get("role") not in _RBAC_ROLES:
        raise HTTPException(status_code=401, detail="Invalid token")
    return payload


def _is_super(payload: dict) -> bool:
    return payload.get("sub") == "admin" or payload.get("role") == "super_admin"


async def _require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    return _decode_token(credentials)


async def _require_super(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    payload = _decode_token(credentials)
    if not _is_super(payload):
        raise HTTPException(status_code=403, detail="super_admin role required")
    return payload


async def _assert_hospital_access(payload: dict, hospital_id: str) -> None:
    """403 unless the token's user may touch this hospital.

    super_admin (and the legacy single-password admin) pass unconditionally;
    tenant_admin / viewer must have a user_tenants row linking their email to
    the hospital's slug.
    """
    if _is_super(payload):
        return
    email = payload.get("sub", "")
    pool = await _db()
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
        raise HTTPException(status_code=403, detail="No access to this hospital")


async def _require_hospital_access(
    hospital_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """Auth + per-hospital scoping for routes carrying a hospital_id."""
    payload = _decode_token(credentials)
    await _assert_hospital_access(payload, hospital_id)
    return payload


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
    pool = await _db()
    async with pool.acquire() as conn:
        if _is_super(payload):
            rows = await conn.fetch(
                "SELECT id, name, name_ml, address, phone, hours, active, "
                "slug, plivo_number, tier, agent_name, agent_language FROM hospitals ORDER BY name"
            )
        else:
            # tenant_admin / viewer only see hospitals assigned via user_tenants
            rows = await conn.fetch(
                """SELECT h.id, h.name, h.name_ml, h.address, h.phone, h.hours, h.active,
                          h.slug, h.plivo_number, h.tier, h.agent_name, h.agent_language
                   FROM hospitals h
                   JOIN user_tenants ut ON ut.tenant_slug = h.slug
                   JOIN users u ON u.id = ut.user_id
                   WHERE u.email = $1 AND u.active
                   ORDER BY h.name""",
                payload.get("sub", ""),
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


@router.get("/hospitals/{hospital_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/departments", dependencies=[Depends(_require_hospital_access)])
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


@router.post("/hospitals/{hospital_id}/departments", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/departments/{dept_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.delete("/hospitals/{hospital_id}/departments/{dept_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/doctors", dependencies=[Depends(_require_hospital_access)])
async def list_doctors(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT d.id, d.name, d.name_ml, d.specialty, d.qualifications,
                      d.active, dep.name as dept_name, dep.id as dept_id,
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
                        d.active, dep.name, dep.id
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


@router.post("/hospitals/{hospital_id}/doctors", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/doctors/{doctor_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.delete("/hospitals/{hospital_id}/doctors/{doctor_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/billing", dependencies=[Depends(_require_hospital_access)])
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


@router.post("/hospitals/{hospital_id}/billing", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/billing/{item_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.delete("/hospitals/{hospital_id}/billing/{item_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/emergency", dependencies=[Depends(_require_hospital_access)])
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


@router.post("/hospitals/{hospital_id}/emergency", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/emergency/{contact_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.delete("/hospitals/{hospital_id}/emergency/{contact_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/faqs", dependencies=[Depends(_require_hospital_access)])
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


@router.post("/hospitals/{hospital_id}/faqs", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/faqs/{faq_id}", dependencies=[Depends(_require_hospital_access)])
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


@router.delete("/hospitals/{hospital_id}/faqs/{faq_id}", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/calls", dependencies=[Depends(_require_hospital_access)])
async def list_calls(hospital_id: str, limit: int = 50):
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT call_id, caller, started_at, ended_at, total_turns,
                      latency_avg_ms, intents, outcome
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
        }
        for r in rows
    ]


@router.get("/hospitals/{hospital_id}/stats", dependencies=[Depends(_require_hospital_access)])
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

@router.get("/hospitals/{hospital_id}/appointments", dependencies=[Depends(_require_hospital_access)])
async def list_appointments(hospital_id: str, status: str = "", limit: int = 50):
    pool = await _db()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """SELECT a.id, a.patient_name, a.patient_phone, a.slot_time,
                          a.status, a.notes, a.created_at,
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
                          a.status, a.notes, a.created_at,
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
            # Frontend expects appointment_date (ISO date) + appointment_time (HH:MM[:SS]);
            # both are derived from the single slot_time column.
            "appointment_date": r["slot_time"].date().isoformat() if r["slot_time"] else None,
            "appointment_time": r["slot_time"].strftime("%H:%M:%S") if r["slot_time"] else None,
            "status": r["status"] or "requested",
            "notes": r["notes"] or "",
            "doctor_name": r["doctor_name"] or "",
            "dept_name": r["dept_name"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


class ApptStatusBody(BaseModel):
    status: str


@router.put("/hospitals/{hospital_id}/appointments/{appt_id}/status", dependencies=[Depends(_require_hospital_access)])
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
    dependencies=[Depends(_require_hospital_access)],
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

@router.get("/hospitals/{hospital_id}/callbacks", dependencies=[Depends(_require_hospital_access)])
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
    sarvam = bool(getattr(settings, "SARVAM_API_KEY", ""))
    groq = bool(getattr(settings, "GROQ_API_KEY", ""))

    hospital_plivo_number = ""
    if hospital_id:
        try:
            pool = await _db()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT plivo_number FROM hospitals WHERE id=$1", hospital_id
                )
            if row:
                hospital_plivo_number = row["plivo_number"] or ""
        except Exception:
            pass

    livekit_ok = lk_url and lk_key and lk_secret
    plivo_ok = plivo_id and plivo_token and plivo_number
    sip_ready = livekit_ok and sip_outbound and sip_host
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
        "plivo": {
            "configured": plivo_ok,
            "auth_id": plivo_id,
            "auth_token": plivo_token,
            "phone_number": plivo_number,
            "hospital_did": hospital_plivo_number or None,
        },
        "voice_ai": {
            "configured": voice_ready,
            "sarvam_stt_tts": sarvam,
            "groq_llm": groq,
        },
        "overall": {
            "sip_calls_ready": sip_ready and plivo_ok,
            "voice_pipeline_ready": voice_ready,
            "inbound_ready": sip_ready and plivo_ok and bool(hospital_plivo_number),
            "outbound_ready": sip_ready and plivo_ok,
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

@router.post("/hospitals/{hospital_id}/cache/clear", dependencies=[Depends(_require_hospital_access)])
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


@router.post("/hospitals/{hospital_id}/provision-number", dependencies=[Depends(_require_hospital_access)])
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


@router.get("/hospitals/{hospital_id}/setup-status", dependencies=[Depends(_require_hospital_access)])
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


@router.get("/hospitals/{hospital_id}/his-config", dependencies=[Depends(_require_hospital_access)])
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


@router.put("/hospitals/{hospital_id}/his-config", dependencies=[Depends(_require_hospital_access)])
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


@router.get("/hospitals/{hospital_id}/his-status", dependencies=[Depends(_require_hospital_access)])
async def get_his_status(hospital_id: str):
    """Return HIS connectivity status (reachable / not configured / etc.)."""
    try:
        from src.integrations.his.service import his_status
        return await his_status(hospital_id)
    except Exception as exc:
        return {"configured": False, "enabled": False, "reachable": False, "error": str(exc)}


@router.post("/hospitals/{hospital_id}/his-config/test", dependencies=[Depends(_require_hospital_access)])
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
