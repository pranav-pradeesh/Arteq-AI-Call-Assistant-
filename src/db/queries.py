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


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=8,
            command_timeout=10,
            ssl="require",
            timeout=10,      # per-connection connect timeout (seconds)
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


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
    loaded_at: float = 0.0

    # ── Quick lookup helpers ──────────────────────────────────────────────────

    def find_dept(self, keyword: str) -> Optional[DeptInfo]:
        kw = keyword.lower()
        for d in self.departments:
            if kw in d.name.lower() or kw in (d.name_ml or "").lower():
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

async def load_hospital_context(hospital_id: str) -> HospitalContext:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Hospital
        h = await conn.fetchrow(
            "SELECT id, name, name_ml, address, phone, hours FROM hospitals WHERE id=$1",
            hospital_id,
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
            """SELECT d.id, d.name, d.name_ml, d.specialty, d.qualifications,
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
               GROUP BY d.id, d.name, d.name_ml, d.specialty, d.qualifications, dep.name, dep.name_ml
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
            ))

        # Billing
        billing_rows = await conn.fetch(
            "SELECT item, item_ml, price_min, price_max, notes "
            "FROM billing_info WHERE hospital_id=$1 AND active=true",
            hospital_id,
        )
        billing = [
            BillingRow(r["item"], r["item_ml"] or "", float(r["price_min"] or 0),
                       float(r["price_max"] or 0), r["notes"] or "")
            for r in billing_rows
        ]

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
        loaded_at=time.time(),
    )


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
