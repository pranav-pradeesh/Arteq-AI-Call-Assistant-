"""
Direct asyncpg queries against the existing Supabase schema.

All queries are parameterised. No ORM — direct SQL for speed and clarity.
Hospital context is loaded once per process and cached in-memory.

day_of_week convention (matches existing data):
  0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday,
  4=Thursday, 5=Friday, 6=Saturday
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid as _uuid_mod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import asyncpg
import pytz

from src.config.settings import settings


def _maybe_json(value: Any) -> Any:
    """asyncpg returns JSON columns (and json_agg output) as raw strings.
    Decode if needed; pass through if already a list/dict."""
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

INDIA_TZ = pytz.timezone("Asia/Kolkata")

# ── Day-of-week helpers ──────────────────────────────────────────────────────

# Python datetime.weekday(): 0=Mon … 6=Sun
# Our DB convention:          0=Sun, 1=Mon … 6=Sat
_PY_TO_DB = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}
_DB_DOW_NAMES = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
                 4: "Thursday", 5: "Friday", 6: "Saturday"}
_DAY_ML = {
    0: "ഞായർ", 1: "തിങ്കൾ", 2: "ചൊവ്വ",
    3: "ബുധൻ", 4: "വ്യാഴം", 5: "വെള്ളി", 6: "ശനി"
}


def today_db_dow() -> int:
    """Return today's day-of-week in DB convention (0=Sun … 6=Sat)."""
    return _PY_TO_DB[datetime.now(INDIA_TZ).weekday()]


def named_dow_to_db(name: str) -> Optional[int]:
    """'monday' → 1, 'sunday' → 0, etc."""
    m = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
         "thursday": 4, "friday": 5, "saturday": 6}
    return m.get(name.lower())


# ── Connection pool ──────────────────────────────────────────────────────────

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()
_pool_failed = False   # fail-fast after first connection failure


def _resolve_ssl(url: str):
    """Decide the asyncpg ssl parameter from DB_SSL + the connection host.

    Returns "require" for remote hosts and False for local hosts so the same
    code path works against Supabase (prod) and a local/docker Postgres (dev).
    """
    mode = (getattr(settings, "DB_SSL", "auto") or "auto").lower()
    if mode in ("disable", "off", "false", "none"):
        return False
    if mode in ("require", "on", "true"):
        return "require"
    # auto
    import urllib.parse
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    _local = {"localhost", "127.0.0.1", "::1", "postgres", "db", "arteq_postgres"}
    if host in _local or host.endswith(".local") or host.endswith(".internal"):
        return False
    return "require"


async def get_pool() -> asyncpg.Pool:
    global _pool, _pool_failed
    if _pool is not None:
        return _pool
    if _pool_failed:
        raise RuntimeError("Database unavailable (connection failed at startup)")
    async with _pool_lock:
        if _pool is None and not _pool_failed:
            try:
                url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
                _pool = await asyncpg.create_pool(
                    url,
                    min_size=2,
                    max_size=30,   # supports 25+ tenants with concurrent calls
                    command_timeout=30,
                    ssl=_resolve_ssl(url),
                    timeout=20,
                )
            except Exception:
                _pool_failed = True
                raise
    return _pool


async def close_pool() -> None:
    global _pool, _pool_failed
    if _pool:
        await _pool.close()
        _pool = None
    _pool_failed = False


# ── Typed result objects ─────────────────────────────────────────────────────

@dataclass
class DeptInfo:
    id: str
    name: str
    name_ml: str
    floor: str
    location_hint: str
    phone_ext: str


@dataclass
class SlotInfo:
    dow: int
    start: str   # "HH:MM"
    end: str
    room: str


@dataclass
class DoctorInfo:
    id: str
    name: str
    name_ml: str
    specialty: str
    qualifications: str
    dept_name: str
    dept_name_ml: str
    slots: list[SlotInfo] = field(default_factory=list)
    dept_id: str = ""   # FK to departments — used so bookings record a department


@dataclass
class BillingRow:
    item: str          # e.g. "consultation:general"
    item_ml: str
    price_min: float
    price_max: float
    notes: str


@dataclass
class FaqRow:
    category: str
    question: str
    answer: str
    answer_ml: str
    tags: list[str]


@dataclass
class EmergencyContact:
    label: str
    label_ml: str
    phone: str


@dataclass
class HospitalContext:
    """Full hospital snapshot loaded from DB, cached in-memory."""
    hospital_id: str
    name: str
    name_ml: str
    address: str
    phone: str
    hours: dict          # {"mon":["08:00","21:00"], ...}
    departments: list[DeptInfo]
    doctors: list[DoctorInfo]
    billing: list[BillingRow]
    faqs: list[FaqRow]
    emergency: list[EmergencyContact]
    knowledge_base: str = ""   # free-form staff handbook (parking, insurance, policies, …)
    tier: str = "hospital"     # "clinic" | "hospital"
    agent_name: str = "Arya"         # per-hospital AI persona name
    agent_language: str = "ml-IN"    # BCP-47: ml-IN, hi-IN, ta-IN, kn-IN, te-IN, en-IN
    queue_data: dict = field(default_factory=dict)  # {dept_name: queue_count} — per-call, not cached
    loaded_at: float = 0.0

    # ── Quick lookup helpers ──────────────────────────────────────────────────

    def find_dept(self, keyword: str) -> Optional[DeptInfo]:
        kw = keyword.lower()
        pattern = re.compile(r'\b' + re.escape(kw) + r'\b')
        for d in self.departments:
            if pattern.search(d.name.lower()) or pattern.search((d.name_ml or "").lower()):
                return d
        return None

    def doctors_for_dept(self, dept_name: str) -> list[DoctorInfo]:
        dn = dept_name.lower()
        return [d for d in self.doctors if dn in d.dept_name.lower()]

    def billing_for_dept(self, dept_key: str) -> Optional[BillingRow]:
        """dept_key: 'general', 'cardiology', etc."""
        key = f"consultation:{dept_key.lower()}"
        for b in self.billing:
            if b.item == key:
                return b
        return None

    def hours_for_day(self, dow: int) -> Optional[tuple[str, str]]:
        """dow is DB convention. Returns (open, close) or None if closed."""
        key = _DB_DOW_NAMES[dow][:3].lower()
        h = self.hours.get(key)
        return (h[0], h[1]) if h else None

    def faqs_by_tags(self, tags: list[str]) -> list[FaqRow]:
        result = []
        for faq in self.faqs:
            if any(t in faq.tags for t in tags):
                result.append(faq)
        return result[:3]


# ── Loader ───────────────────────────────────────────────────────────────────

class HospitalNotFound(Exception):
    """Raised when load_hospital_context can't find the given hospital_id."""


async def load_hospital_context(hospital_id: str) -> HospitalContext:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Hospital
        h = await conn.fetchrow(
            "SELECT id, name, name_ml, address, phone, hours FROM hospitals WHERE id=$1",
            hospital_id,
        )
        if h is None:
            raise HospitalNotFound(
                f"hospital_id={hospital_id} not found in hospitals table"
            )

        # Departments
        dept_rows = await conn.fetch(
            "SELECT id, name, name_ml, floor, location_hint, phone_ext "
            "FROM departments WHERE hospital_id=$1 AND active=true ORDER BY name",
            hospital_id,
        )
        departments = [
            DeptInfo(str(r["id"]), r["name"], r["name_ml"] or "",
                     r["floor"] or "", r["location_hint"] or "", r["phone_ext"] or "")
            for r in dept_rows
        ]

        # Doctors + schedules
        doc_rows = await conn.fetch(
            """SELECT d.id, d.dept_id, d.name, d.name_ml, d.specialty, d.qualifications,
                      dep.name as dept_name, dep.name_ml as dept_name_ml,
                      json_agg(json_build_object(
                          'dow', s.day_of_week,
                          'start', to_char(s.start_time,'HH24:MI'),
                          'end', to_char(s.end_time,'HH24:MI'),
                          'room', s.room
                      ) ORDER BY s.day_of_week, s.start_time) FILTER (WHERE s.id IS NOT NULL) as slots
               FROM doctors d
               LEFT JOIN departments dep ON d.dept_id = dep.id
               LEFT JOIN schedules s ON s.doctor_id = d.id AND s.active = true
               WHERE d.hospital_id = $1 AND d.active = true
               GROUP BY d.id, d.dept_id, d.name, d.name_ml, d.specialty, d.qualifications, dep.name, dep.name_ml
               ORDER BY dep.name, d.name""",
            hospital_id,
        )
        doctors = []
        for r in doc_rows:
            raw_slots = _maybe_json(r["slots"]) or []
            slots = [
                SlotInfo(s["dow"], s["start"], s["end"], (s.get("room") or "") if isinstance(s, dict) else "")
                for s in raw_slots if isinstance(s, dict)
            ]
            doctors.append(DoctorInfo(
                str(r["id"]), r["name"], r["name_ml"] or "",
                r["specialty"] or "", r["qualifications"] or "",
                r["dept_name"] or "", r["dept_name_ml"] or "",
                slots,
                dept_id=str(r["dept_id"]) if r["dept_id"] else "",
            ))

        # Billing
        billing_rows = await conn.fetch(
            "SELECT item, item_ml, price_min, price_max, notes "
            "FROM billing_info WHERE hospital_id=$1 AND active=true",
            hospital_id,
        )
        # If price_min or price_max is NULL we drop the row rather than coerce
        # to ₹0 — quoting "free" by mistake is worse than saying "not available".
        billing = []
        for r in billing_rows:
            pmin, pmax = r["price_min"], r["price_max"]
            if pmin is None and pmax is None:
                continue
            billing.append(BillingRow(
                r["item"], r["item_ml"] or "",
                float(pmin) if pmin is not None else float(pmax),
                float(pmax) if pmax is not None else float(pmin),
                r["notes"] or "",
            ))

        # FAQs
        faq_rows = await conn.fetch(
            "SELECT category, question, answer, answer_ml, tags "
            "FROM faqs WHERE hospital_id=$1 AND active=true ORDER BY priority DESC",
            hospital_id,
        )
        faqs = [
            FaqRow(r["category"], r["question"], r["answer"],
                   r["answer_ml"] or "", list(_maybe_json(r["tags"]) or []))
            for r in faq_rows
        ]

        # Emergency
        emerg_rows = await conn.fetch(
            "SELECT label, label_ml, phone FROM emergency_contacts "
            "WHERE hospital_id=$1 AND active=true ORDER BY priority DESC",
            hospital_id,
        )
        emergency = [
            EmergencyContact(r["label"], r["label_ml"] or "", r["phone"])
            for r in emerg_rows
        ]

        # Extended columns — graceful fallback if column doesn't exist yet (old DB).
        knowledge_base = ""
        tier = "hospital"
        agent_name = "Arya"
        agent_language = "ml-IN"
        try:
            row = await conn.fetchrow(
                "SELECT knowledge_base, tier, agent_name, agent_language "
                "FROM hospitals WHERE id=$1", hospital_id
            )
            if row:
                knowledge_base = row["knowledge_base"] or ""
                tier = row["tier"] or "hospital"
                agent_name = row["agent_name"] or "Arya"
                agent_language = row["agent_language"] or "ml-IN"
        except Exception:
            pass

    import time
    return HospitalContext(
        hospital_id=hospital_id,
        name=h["name"],
        name_ml=h["name_ml"] or "",
        address=h["address"] or "",
        phone=h["phone"] or "",
        hours=_maybe_json(h["hours"]) or {},
        departments=departments,
        doctors=doctors,
        billing=billing,
        faqs=faqs,
        emergency=emergency,
        knowledge_base=knowledge_base,
        tier=tier,
        agent_name=agent_name,
        agent_language=agent_language,
        loaded_at=time.time(),
    )


# Per-hospital locks prevent multiple concurrent calls from all hitting the DB
# at startup when the cache is cold (thundering herd).
_ctx_locks: dict[str, asyncio.Lock] = {}


async def get_or_load_hospital_context(hospital_id: str) -> HospitalContext:
    """Return cached HospitalContext, refreshing from DB every 5 minutes."""
    from src.cache.store import hospital_cache, HOSPITAL_CACHE_TTL
    cached = hospital_cache.get(hospital_id)
    if cached is not None:
        return cached
    if hospital_id not in _ctx_locks:   # asyncio is single-threaded; no race here
        _ctx_locks[hospital_id] = asyncio.Lock()
    async with _ctx_locks[hospital_id]:
        cached = hospital_cache.get(hospital_id)   # re-check inside lock
        if cached is not None:
            return cached
        ctx = await load_hospital_context(hospital_id)
        hospital_cache.set(hospital_id, ctx, ttl=HOSPITAL_CACHE_TTL)
        return ctx


async def write_call_log(
    hospital_id: str,
    call_id: str,
    caller: str,
    started_at: datetime,
    ended_at: datetime,
    total_turns: int,
    latency_avg_ms: int,
    cost_paise: int,
    transcript: list,
    intents: list,
    outcome: str,
) -> None:
    """Write call log row asynchronously. Non-blocking — called as background task."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO call_logs
                   (hospital_id, call_id, caller, started_at, ended_at,
                    total_turns, latency_avg_ms, cost_paise, transcript, intents, outcome)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                   ON CONFLICT (call_id) DO NOTHING""",
                hospital_id, call_id, caller, started_at, ended_at,
                total_turns, latency_avg_ms, cost_paise,
                __import__("json").dumps(transcript, ensure_ascii=False),
                __import__("json").dumps(intents, ensure_ascii=False),
                outcome,
            )
    except Exception as e:
        import logging
        logging.error(f"call_log write failed: {e}")


# ── Appointments ──────────────────────────────────────────────────────────────

async def create_appointment(
    hospital_id: str,
    patient_name: str,
    patient_phone: str,
    doctor_id: Optional[str],
    dept_id: Optional[str],
    slot_time: Optional[datetime],
    notes: str,
    call_id: str,
    his_appointment_id: Optional[str] = None,
) -> str:
    """Insert a new appointment; returns the new UUID as a string.

    Rejects a duplicate (same doctor + slot + hospital) that is still active,
    so two concurrent calls cannot double-book the same slot.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if slot_time and doctor_id:
            conflict = await conn.fetchval(
                """SELECT 1 FROM appointments
                   WHERE hospital_id=$1 AND doctor_id=$2 AND slot_time=$3
                     AND status IN ('booked','confirmed','requested')
                   LIMIT 1""",
                hospital_id,
                _uuid_mod.UUID(doctor_id),
                slot_time,
            )
            if conflict:
                raise ValueError("slot_already_booked")

        row = await conn.fetchrow(
            """INSERT INTO appointments
               (hospital_id, patient_name, patient_phone, doctor_id, dept_id,
                slot_time, notes, call_id, status, reminder_sent, his_appointment_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'booked',false,$9)
               RETURNING id""",
            hospital_id,
            patient_name,
            patient_phone,
            _uuid_mod.UUID(doctor_id) if doctor_id else None,
            _uuid_mod.UUID(dept_id) if dept_id else None,
            slot_time,
            notes or "",
            call_id,
            his_appointment_id,
        )
    return str(row["id"])


async def cancel_appointment_by_id(appointment_id: str, hospital_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE appointments SET status='cancelled', updated_at=NOW() "
            "WHERE id=$1 AND hospital_id=$2",
            _uuid_mod.UUID(appointment_id),
            hospital_id,
        )
    return result.split()[-1] != "0"


async def reschedule_appointment_by_id(
    appointment_id: str, new_slot_time: datetime, hospital_id: str = ""
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if hospital_id:
            result = await conn.execute(
                "UPDATE appointments SET slot_time=$2, status='rescheduled', "
                "updated_at=NOW() WHERE id=$1 AND hospital_id=$3",
                _uuid_mod.UUID(appointment_id),
                new_slot_time,
                hospital_id,
            )
        else:
            result = await conn.execute(
                "UPDATE appointments SET slot_time=$2, status='rescheduled', "
                "updated_at=NOW() WHERE id=$1",
                _uuid_mod.UUID(appointment_id),
                new_slot_time,
            )
    return result.split()[-1] != "0"


async def get_appointments_by_phone(
    phone: str, hospital_id: str
) -> list[dict]:
    """Return the last 3 active appointments for a caller phone number."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.id, a.patient_name, a.slot_time, a.status,
                      a.his_appointment_id,
                      d.name AS doctor_name, dep.name AS dept_name
               FROM appointments a
               LEFT JOIN doctors d ON a.doctor_id = d.id
               LEFT JOIN departments dep ON a.dept_id = dep.id
               WHERE a.patient_phone=$1 AND a.hospital_id=$2
                 AND a.status IN ('pending','booked','confirmed')
               ORDER BY a.created_at DESC
               LIMIT 3""",
            phone, hospital_id,
        )
    return [dict(r) for r in rows]


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def create_callback(
    hospital_id: str,
    patient_phone: str,
    patient_name: str,
    reason: str,
    preferred_time: str,
    call_id: str,
) -> str:
    """Insert a callback request; returns the new UUID as a string."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO callbacks
               (hospital_id, patient_phone, patient_name, reason, preferred_time,
                call_id, status)
               VALUES ($1,$2,$3,$4,$5,$6,'pending')
               RETURNING id""",
            hospital_id, patient_phone, patient_name or "",
            reason or "", preferred_time or "", call_id,
        )
    return str(row["id"])


# ── OPD queue ─────────────────────────────────────────────────────────────────

async def get_opd_queue_estimate(dept_id: str) -> int:
    """Return today's booked appointment count for a department (0 if unknown)."""
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT queue_count FROM opd_queue_today WHERE dept_id=$1",
                _uuid_mod.UUID(dept_id),
            )
        return int(row["queue_count"]) if row else 0
    except Exception:
        return 0


async def get_all_opd_queue_estimates(hospital_id: str) -> dict[str, int]:
    """Return {dept_name: queue_count} for all departments in one query."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT d.name AS dept_name, q.queue_count
                   FROM opd_queue_today q
                   JOIN departments d ON d.id = q.dept_id
                   WHERE d.hospital_id = $1 AND q.queue_count > 0""",
                hospital_id,
            )
        return {row["dept_name"]: int(row["queue_count"]) for row in rows}
    except Exception:
        return {}


# ── Doctor / dept fuzzy lookup (for booking from voice transcript) ────────────

async def get_doctor_by_name_fuzzy(name_fragment: str, hospital_id: str) -> Optional[dict]:
    """Find a doctor by partial name match (case-insensitive)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id::text, name, name_ml FROM doctors
               WHERE hospital_id=$1
                 AND (LOWER(name) LIKE $2 OR name_ml LIKE $2)
               LIMIT 1""",
            hospital_id,
            f"%{name_fragment.lower()}%",
        )
    return dict(row) if row else None


async def get_dept_by_name_fuzzy(name_fragment: str, hospital_id: str) -> Optional[dict]:
    """Find a department by partial name match (case-insensitive)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id::text, name, name_ml FROM departments
               WHERE hospital_id=$1 AND active=true
                 AND (LOWER(name) LIKE $2 OR LOWER(COALESCE(name_ml,'')) LIKE $2)
               LIMIT 1""",
            hospital_id,
            f"%{name_fragment.lower()}%",
        )
    return dict(row) if row else None


# ── Call feedback ─────────────────────────────────────────────────────────────

async def get_patient_profile(phone: str, hospital_id: str) -> Optional[dict]:
    """Return patient name + last 3 appointments for a phone number.

    Used for personalized greetings: Arya recognises returning patients.
    Returns None if the patient has never called/booked before.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.patient_name, a.slot_time, a.status,
                      d.name AS doctor_name, dep.name AS dept_name
               FROM appointments a
               LEFT JOIN doctors d ON a.doctor_id = d.id
               LEFT JOIN departments dep ON a.dept_id = dep.id
               WHERE a.patient_phone = $1 AND a.hospital_id = $2
               ORDER BY a.created_at DESC
               LIMIT 3""",
            phone, hospital_id,
        )
    if not rows:
        return None
    name = rows[0]["patient_name"] or ""
    history = [
        {
            "doctor": r["doctor_name"] or "",
            "dept": r["dept_name"] or "",
            "slot": str(r["slot_time"])[:10] if r["slot_time"] else "",
            "status": r["status"],
        }
        for r in rows
    ]
    return {"name": name, "history": history}


async def log_missed_question(
    hospital_id: str,
    call_id: str,
    question: str,
    language: str,
    context: str = "",
) -> None:
    """Record a question Arya couldn't answer for later KB improvement."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO missed_questions
                   (hospital_id, call_id, question, language, context)
                   VALUES ($1,$2,$3,$4,$5)""",
                hospital_id, call_id, question[:500], language, context[:200],
            )
    except Exception as e:
        import logging
        logging.debug(f"log_missed_question failed: {e}")


async def get_available_slots(
    doctor_id: str,
    date_str: str,
    hospital_id: str,
    slot_duration_minutes: int = 15,
) -> list[str]:
    """Return available HH:MM slot strings for a doctor on a given date.

    Derives availability by subtracting booked appointments from the
    doctor's schedule for that day of week.
    date_str: "YYYY-MM-DD"
    """
    import datetime as _dt
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            date = _dt.date.fromisoformat(date_str)
        except ValueError:
            return []
        # DB convention: 0=Sun, 1=Mon, ..., 6=Sat
        py_dow = date.weekday()  # 0=Mon ... 6=Sun
        db_dow = (py_dow + 1) % 7

        schedule = await conn.fetch(
            """SELECT to_char(start_time,'HH24:MI') AS start,
                      to_char(end_time,'HH24:MI') AS end
               FROM schedules
               WHERE doctor_id = $1 AND day_of_week = $2 AND active = true""",
            _uuid_mod.UUID(doctor_id), db_dow,
        )
        if not schedule:
            return []

        # Already booked slots for this doctor on this date (scoped to hospital)
        booked = await conn.fetch(
            """SELECT to_char(slot_time AT TIME ZONE 'Asia/Kolkata','HH24:MI') AS slot
               FROM appointments
               WHERE doctor_id = $1
                 AND slot_time::date = $2
                 AND hospital_id = $3
                 AND status IN ('booked','confirmed','requested')""",
            _uuid_mod.UUID(doctor_id), date, hospital_id,
        )
        booked_times = {r["slot"] for r in booked}

        # Generate all possible slots from schedule windows
        available = []
        for row in schedule:
            start_h, start_m = map(int, row["start"].split(":"))
            end_h, end_m = map(int, row["end"].split(":"))
            current = start_h * 60 + start_m
            end_total = end_h * 60 + end_m
            while current + slot_duration_minutes <= end_total:
                slot = f"{current // 60:02d}:{current % 60:02d}"
                if slot not in booked_times:
                    available.append(slot)
                current += slot_duration_minutes

        return available[:10]  # return up to 10 next available slots


async def get_pending_followups(db_pool, days_after: int = 3) -> list[dict]:
    """Appointments from ~days_after days ago needing a follow-up call."""
    query = """
        SELECT
            a.id,
            a.hospital_id,
            a.patient_phone,
            a.patient_name,
            a.slot_time,
            d.name AS doctor_name,
            h.slug  AS slug
        FROM appointments a
        LEFT JOIN doctors d ON a.doctor_id = d.id
        LEFT JOIN hospitals h ON h.id = a.hospital_id
        WHERE
            a.followup_sent = false
            AND a.status IN ('booked','confirmed','requested')
            AND a.slot_time BETWEEN now() - ($1 || ' days')::interval - interval '12 hours'
                                 AND now() - ($1 || ' days')::interval + interval '12 hours'
        ORDER BY a.slot_time
        LIMIT 20
    """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, str(days_after))
        return [dict(row) for row in rows]
    except Exception as exc:
        import logging
        logging.debug(f"get_pending_followups skipped: {exc}")
        return []


async def get_pending_confirmations(db_pool, days_min: int = 5, days_max: int = 14) -> list[dict]:
    """Appointments in the [days_min, days_max] window that haven't had a confirmation call."""
    query = """
        SELECT
            a.id,
            a.patient_phone,
            a.patient_name,
            a.slot_time,
            a.hospital_id,
            d.name AS doctor_name,
            h.slug  AS slug
        FROM appointments a
        LEFT JOIN doctors d ON a.doctor_id = d.id
        LEFT JOIN hospitals h ON h.id = a.hospital_id
        WHERE
            a.confirmation_sent = false
            AND a.status IN ('booked', 'confirmed', 'requested')
            AND a.slot_time BETWEEN now() + ($1 || ' days')::interval
                                 AND now() + ($2 || ' days')::interval
        ORDER BY a.slot_time
    """
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, str(days_min), str(days_max))
        return [dict(row) for row in rows]
    except Exception as exc:
        import logging
        logging.debug(f"get_pending_confirmations skipped: {exc}")
        return []


async def mark_confirmation_sent(appointment_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE appointments SET confirmation_sent = true WHERE id = $1",
            _uuid_mod.UUID(appointment_id),
        )


async def increment_campaign_calls_answered(campaign_id: str) -> None:
    """Increment calls_answered counter for a campaign when a call engages."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE campaigns SET calls_answered = calls_answered + 1, "
            "updated_at = NOW() WHERE id = $1",
            _uuid_mod.UUID(campaign_id),
        )


async def write_call_feedback(
    call_id: str,
    hospital_id: str,
    rating: Optional[int],
    comments: str,
) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO call_feedback (call_id, hospital_id, rating, comments)
                   VALUES ($1,$2,$3,$4)""",
                call_id, hospital_id, rating, comments or "",
            )
    except Exception as e:
        import logging
        logging.error(f"call_feedback write failed: {e}")
