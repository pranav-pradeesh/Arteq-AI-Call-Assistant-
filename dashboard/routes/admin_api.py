"""
Admin Dashboard API — asyncpg direct queries on the Supabase schema.

Auth: single-admin JWT (DASHBOARD_ADMIN_PASSWORD env var).
Multi-tenant: list all hospitals, CRUD per hospital_id.
Cache: invalidate hospital_cache on every write.

Day-of-week convention (matches DB): 0=Sunday … 6=Saturday.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    return jwt.encode({"sub": "admin", "exp": exp}, secret, algorithm=ALGORITHM)


async def _require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> None:
    secret = getattr(settings, "DASHBOARD_JWT_SECRET", "insecure-dev-secret")
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(credentials.credentials, secret, algorithms=[ALGORITHM])
        if payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


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

@router.get("/hospitals", dependencies=[Depends(_require_auth)])
async def list_hospitals():
    pool = await _db()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, name_ml, address, phone, hours, active "
            "FROM hospitals ORDER BY name"
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
        }
        for r in rows
    ]


@router.get("/hospitals/{hospital_id}", dependencies=[Depends(_require_auth)])
async def get_hospital(hospital_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, name_ml, address, phone, hours, active "
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
    }


class HospitalUpdate(BaseModel):
    name: Optional[str] = None
    name_ml: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[dict] = None
    active: Optional[bool] = None


@router.post("/hospitals", dependencies=[Depends(_require_auth)])
async def create_hospital(body: HospitalUpdate):
    if not body.name:
        raise HTTPException(status_code=400, detail="name is required")
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO hospitals (id, name, name_ml, address, phone, hours, active)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            new_id,
            body.name,
            body.name_ml or "",
            body.address or "",
            body.phone or "",
            json.dumps(body.hours or {}),
            True,
        )
    return {"id": new_id, "status": "created"}


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
        if not fields:
            return {"status": "no_changes"}
        values.append(hospital_id)
        await conn.execute(
            f"UPDATE hospitals SET {', '.join(fields)} WHERE id=${i}",
            *values,
        )
    _invalidate(hospital_id)
    return {"status": "updated"}


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


@router.post("/doctors/{doctor_id}/schedules", dependencies=[Depends(_require_auth)])
async def add_schedule(doctor_id: str, body: ScheduleBody):
    new_id = str(uuid.uuid4())
    pool = await _db()
    async with pool.acquire() as conn:
        # Get hospital_id for cache invalidation
        row = await conn.fetchrow("SELECT hospital_id FROM doctors WHERE id=$1", doctor_id)
        if not row:
            raise HTTPException(status_code=404, detail="Doctor not found")
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


@router.delete("/schedules/{schedule_id}", dependencies=[Depends(_require_auth)])
async def delete_schedule(schedule_id: str):
    pool = await _db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT hospital_id FROM schedules WHERE id=$1", schedule_id)
        if row:
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
                 AND started_at > NOW() - INTERVAL '%s days'""" % int(days),
            hospital_id,
        )
    return {
        "total_calls": row["total_calls"] or 0,
        "avg_latency_ms": row["avg_latency_ms"] or 0,
        "transfers": row["transfers"] or 0,
        "avg_turns": round(row["avg_turns"] or 0, 1),
        "days": days,
    }


# ── Cache Clear ───────────────────────────────────────────────────────────────

@router.post("/hospitals/{hospital_id}/cache/clear", dependencies=[Depends(_require_auth)])
async def clear_cache(hospital_id: str):
    _invalidate(hospital_id)
    return {"status": "cache_cleared", "hospital_id": hospital_id}


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
