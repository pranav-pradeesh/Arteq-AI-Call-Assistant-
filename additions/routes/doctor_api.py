"""
Doctor self-service API — a logged-in doctor sees ONLY their own data.

Auth: a Bearer JWT with role="doctor" and a doctor_id claim (issued at login for
users linked to a doctors row via migration 024). Every query is scoped to that
doctor_id from the token, so a doctor can never read another doctor's patients,
schedule, or appointments.

Routes (doctor, role="doctor"):
  GET  /doctor/me                → the doctor's own profile
  GET  /doctor/me/appointments   → their appointments (?day=YYYY-MM-DD, else upcoming)
  GET  /doctor/me/schedule       → their weekly schedule
  POST /doctor/me/availability   → set today's availability (available|delayed|unavailable)

Provisioning (admin):
  POST /admin/doctor-logins      → create a login for a doctors row (admin only)
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..deps import AuthDep, PoolDep, require_role

router = APIRouter(prefix="/doctor", tags=["doctor"])
admin_router = APIRouter(prefix="/admin", tags=["doctor-admin"])

_VALID_AVAIL = {"available", "delayed", "unavailable"}


async def _doctor_ctx(payload: AuthDep) -> tuple:
    """Return (doctor_id, hospital_id) from a doctor token; 403 otherwise.

    This is the single scoping choke-point: only role="doctor" tokens with a
    doctor_id claim get through, and the doctor_id comes from the signed token,
    never from a client-supplied parameter.
    """
    if payload.get("role") != "doctor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Doctor login required.")
    doctor_id = payload.get("doctor_id")
    if not doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Token is missing doctor_id.")
    return str(doctor_id), str(payload.get("hospital_id") or "")


DoctorCtx = Annotated[tuple, Depends(_doctor_ctx)]


@router.get("/me", summary="The logged-in doctor's own profile")
async def doctor_me(ctx: DoctorCtx, pool: PoolDep) -> dict:
    doctor_id, _ = ctx
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT d.id::text AS id, d.name, d.name_ml, d.specialty,
                      d.qualifications, dep.name AS department, h.name AS hospital
               FROM doctors d
               LEFT JOIN departments dep ON dep.id = d.dept_id
               LEFT JOIN hospitals  h   ON h.id  = d.hospital_id
               WHERE d.id = $1""",
            doctor_id,
        )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Doctor not found.")
    return dict(row)


@router.get("/me/appointments", summary="The doctor's own appointments")
async def doctor_appointments(
    ctx: DoctorCtx,
    pool: PoolDep,
    day: Optional[str] = Query(None, description="YYYY-MM-DD; default = upcoming"),
) -> list[dict]:
    doctor_id, _ = ctx
    async with pool.acquire() as conn:
        if day:
            rows = await conn.fetch(
                """SELECT id::text, patient_name, patient_phone, slot_time,
                          status, confirmation_code
                   FROM appointments
                   WHERE doctor_id = $1 AND slot_time::date = $2::date
                   ORDER BY slot_time""",
                doctor_id, day,
            )
        else:
            rows = await conn.fetch(
                """SELECT id::text, patient_name, patient_phone, slot_time,
                          status, confirmation_code
                   FROM appointments
                   WHERE doctor_id = $1 AND slot_time >= now() - interval '12 hours'
                   ORDER BY slot_time
                   LIMIT 200""",
                doctor_id,
            )
    return [dict(r) for r in rows]


@router.get("/me/schedule", summary="The doctor's weekly schedule")
async def doctor_schedule(ctx: DoctorCtx, pool: PoolDep) -> list[dict]:
    doctor_id, _ = ctx
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT day_of_week, start_time::text AS start_time,
                      end_time::text AS end_time, room, active
               FROM schedules WHERE doctor_id = $1 ORDER BY day_of_week""",
            doctor_id,
        )
    return [dict(r) for r in rows]


class AvailabilityIn(BaseModel):
    status: str = Field(..., description="available | delayed | unavailable")
    note: str = ""


@router.post("/me/availability", summary="Set the doctor's availability for today")
async def set_availability(body: AvailabilityIn, ctx: DoctorCtx, pool: PoolDep) -> dict:
    doctor_id, hospital_id = ctx
    if body.status not in _VALID_AVAIL:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of {sorted(_VALID_AVAIL)}",
        )
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO doctor_availability
                   (id, doctor_id, hospital_id, status, note, changed_by)
               VALUES (gen_random_uuid(), $1, $2, $3, $4, 'doctor')""",
            doctor_id, hospital_id or None, body.status, body.note,
        )
    return {"status": body.status, "note": body.note}


# ── Provisioning (admin) ─────────────────────────────────────────────────────

class DoctorLoginIn(BaseModel):
    doctor_id: str
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)


@admin_router.post(
    "/doctor-logins",
    dependencies=[Depends(require_role("super_admin", "tenant_admin"))],
    summary="Create a login for a doctor (admin only)",
)
async def create_doctor_login(body: DoctorLoginIn, pool: PoolDep) -> dict:
    # Imported lazily to avoid a circular import at module load.
    from .users_api import hash_password

    email = body.email.strip().lower()
    async with pool.acquire() as conn:
        doc = await conn.fetchrow(
            "SELECT id::text AS id, hospital_id::text AS hospital_id, name "
            "FROM doctors WHERE id = $1",
            body.doctor_id,
        )
        if not doc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Doctor not found.")
        if await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered.")
        await conn.execute(
            """INSERT INTO users (email, password_hash, role, active, doctor_id, hospital_id)
               VALUES ($1, $2, 'doctor', TRUE, $3, $4)""",
            email, hash_password(body.password), doc["id"], doc["hospital_id"],
        )
    return {
        "email": email,
        "role": "doctor",
        "doctor_id": doc["id"],
        "doctor_name": doc["name"],
    }
