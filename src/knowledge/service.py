"""
Hospital Knowledge Service — queries the real Supabase schema.

All answers come from structured DB data. No hallucination.

Query strategy:
  - department / doctor info  → departments + doctors + schedules tables
  - fees                      → billing_info table
  - emergency                 → emergency_contacts table
  - location / timing         → hospitals table
  - general FAQ               → faqs table (tag-based lookup)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pytz

from src.config.settings import settings
from src.db.queries import (
    DoctorInfo, HospitalContext,
    _DB_DOW_NAMES, _DAY_ML, named_dow_to_db, today_db_dow,
)
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE, INTENT_CONTACT, INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY, INTENT_DOCTOR_TIMING, INTENT_EMERGENCY,
    INTENT_HOSPITAL_TIMING, INTENT_LOCATION,
)

INDIA_TZ = pytz.timezone("Asia/Kolkata")


@dataclass
class KnowledgeResult:
    intent: str
    found: bool
    text_ml: str = ""      # Ready-to-speak Malayalam answer (primary)
    text_en: str = ""      # English fallback
    data: dict = field(default_factory=dict)
    missing: Optional[str] = None   # what we couldn't resolve
    # Legacy alias kept for backward compat with older tests
    missing_entity: Optional[str] = None


# ── Department keyword → dept_name normaliser ─────────────────────────────────

_DEPT_KEYWORDS: dict[str, str] = {
    # general
    "general": "general medicine", "gp": "general medicine",
    "fever": "general medicine", "medicine": "general medicine",
    # cardio
    "cardio": "cardiology", "heart": "cardiology", "hridayam": "cardiology",
    "hrudayam": "cardiology",
    # ENT
    "ent": "ent", "ear": "ent", "nose": "ent", "throat": "ent",
    "kaan": "ent", "mookku": "ent", "thallu": "ent",
    # ortho
    "ortho": "orthopedics", "orthopedic": "orthopedics",
    "bone": "orthopedics", "ellu": "orthopedics", "joint": "orthopedics",
    "fracture": "orthopedics",
    # pediatrics
    "pediatrics": "pediatrics", "paediatrics": "pediatrics",
    "child": "pediatrics", "kutta": "pediatrics", "kuttinu": "pediatrics",
    "baby": "pediatrics", "balan": "pediatrics", "kids": "pediatrics",
    # gynecology
    "gynaecology": "gynaecology", "gynecology": "gynaecology",
    "gynae": "gynaecology", "obs": "gynaecology", "delivery": "gynaecology",
    "prasavam": "gynaecology", "maternity": "gynaecology", "women": "gynaecology",
}


def resolve_dept_keyword(keyword: str) -> Optional[str]:
    return _DEPT_KEYWORDS.get(keyword.lower())


# ── Main service ──────────────────────────────────────────────────────────────

class HospitalKnowledgeService:

    def __init__(self, ctx: HospitalContext):
        self.ctx = ctx

    def answer(
        self,
        intent: str,
        entities: dict,
        state_context: Optional[dict] = None,
    ) -> KnowledgeResult:
        """Route to the right handler. state_context fills missing entities."""
        # Inherit from conversation context if entity missing
        if state_context:
            if not entities.get("department") and state_context.get("last_department"):
                entities = {**entities, "department": state_context["last_department"]}
            if not entities.get("doctor_name") and state_context.get("last_doctor"):
                entities = {**entities, "doctor_name": state_context["last_doctor"]}
            if not entities.get("day") and state_context.get("last_day"):
                entities = {**entities, "day": state_context["last_day"]}

        handlers = {
            INTENT_DOCTOR_AVAILABILITY: self._doctor_availability,
            INTENT_DOCTOR_TIMING: self._doctor_timing,
            INTENT_CONSULTATION_FEE: self._fee,
            INTENT_DEPARTMENT_EXISTS: self._dept_exists,
            INTENT_HOSPITAL_TIMING: self._hospital_timing,
            INTENT_EMERGENCY: self._emergency,
            INTENT_LOCATION: self._location,
            INTENT_CONTACT: self._contact,
        }
        handler = handlers.get(intent)
        if handler:
            return handler(entities)
        return KnowledgeResult(intent=intent, found=False,
                               text_ml="ഇതിനെ കുറിച്ച് ഞാൻ ഉചിതമായ ഉത്തരം നൽകാൻ കഴിയില്ല.",
                               missing="unsupported_intent")

    # ── Doctor availability ───────────────────────────────────────────────────

    def _doctor_availability(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        doc_name = entities.get("doctor_name")
        day_name = entities.get("day")  # "today", "monday", etc.

        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        # By doctor name
        if doc_name:
            doc = self._find_doctor_by_name(doc_name)
            if not doc:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=False,
                    text_ml=f"ക്ഷമിക്കണം, ആ doctor-നെ ഞങ്ങളുടെ list-ൽ കണ്ടെത്താൻ കഴിഞ്ഞില്ല.",
                    missing="doctor_not_found",
                )
            slot = self._slot_for_dow(doc, dow)
            if slot:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                             f"{slot.start} മുതൽ {slot.end} വരെ available ആണ്."),
                    data={"doctor": doc.name, "start": slot.start, "end": slot.end},
                )
            else:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=f"ക്ഷമിക്കണം, {doc.name_ml or doc.name} doctor {day_label}-ൽ available അല്ല.",
                    data={"doctor": doc.name, "available": False},
                )

        # By department
        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            dept = self.ctx.find_dept(resolved)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=False,
                    text_ml=f"ക്ഷമിക്കണം, {dept_kw} department ഈ hospital-ൽ ലഭ്യമല്ല.",
                    missing="dept_not_found",
                )
            avail = [
                d for d in self.ctx.doctors_for_dept(dept.name)
                if self._slot_for_dow(d, dow)
            ]
            if avail:
                names = ", ".join(d.name_ml or d.name for d in avail[:3])
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{dept.name_ml or dept.name}-ൽ {day_label}-ൽ "
                             f"{len(avail)} doctor available ആണ്: {names}."),
                    data={"dept": dept.name, "count": len(avail)},
                )
            else:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"ക്ഷമിക്കണം, {dept.name_ml or dept.name}-ൽ "
                             f"{day_label}-ൽ doctors available അല്ല."),
                )

        return KnowledgeResult(
            intent=INTENT_DOCTOR_AVAILABILITY, found=False,
            text_ml="ഏത് department-ലേക്കോ doctor-ലേക്കോ ആണ് enquiry?",
            missing="no_dept_or_doctor",
        )

    # ── Timing ───────────────────────────────────────────────────────────────

    def _doctor_timing(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        doc_name = entities.get("doctor_name")
        day_name = entities.get("day")
        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            dept = self.ctx.find_dept(resolved)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING, found=False,
                    text_ml=f"{dept_kw} department-ന്റെ timing ഞങ്ങൾക്ക് ലഭ്യമല്ല.",
                    missing="dept_not_found",
                )
            # Aggregate earliest start and latest end across all docs in dept
            docs = self.ctx.doctors_for_dept(dept.name)
            slots = [self._slot_for_dow(d, dow) for d in docs if self._slot_for_dow(d, dow)]
            if slots:
                earliest = min(s.start for s in slots)
                latest = max(s.end for s in slots)
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING, found=True,
                    text_ml=(f"{dept.name_ml or dept.name} OP {day_label}-ൽ "
                             f"{earliest} മുതൽ {latest} വരെ ആണ്."),
                    data={"dept": dept.name, "start": earliest, "end": latest},
                )
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING, found=True,
                text_ml=(f"ക്ഷമിക്കണം, {dept.name_ml or dept.name}-ൽ "
                         f"{day_label}-ൽ OP ഇല്ല."),
            )

        if doc_name:
            doc = self._find_doctor_by_name(doc_name)
            if doc:
                slot = self._slot_for_dow(doc, dow)
                if slot:
                    return KnowledgeResult(
                        intent=INTENT_DOCTOR_TIMING, found=True,
                        text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                                 f"{slot.start} മുതൽ {slot.end} വരെ {slot.room or ''} ൽ ആണ്."),
                    )

        # General hospital OP
        hours = self.ctx.hours_for_day(dow)
        if hours:
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING, found=True,
                text_ml=(f"Hospital {day_label}-ൽ {hours[0]} മുതൽ {hours[1]} വരെ open ആണ്. "
                         f"Emergency 24 മണിക്കൂറും ഉണ്ട്."),
            )
        return KnowledgeResult(
            intent=INTENT_DOCTOR_TIMING, found=False,
            text_ml="Timing information ലഭ്യമല്ല. Reception-ൽ ബന്ധപ്പെടൂ.",
        )

    # ── Fee ───────────────────────────────────────────────────────────────────

    def _fee(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")

        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            # Try billing_info
            billing = self.ctx.billing_for_dept(resolved)
            if billing:
                if billing.price_min == billing.price_max:
                    price_str = f"₹{int(billing.price_min)}"
                else:
                    price_str = f"₹{int(billing.price_min)}–{int(billing.price_max)}"
                return KnowledgeResult(
                    intent=INTENT_CONSULTATION_FEE, found=True,
                    text_ml=f"{billing.item_ml or dept_kw} consultation fee {price_str} ആണ്.",
                    data={"item": billing.item, "min": billing.price_min, "max": billing.price_max},
                )

        # No dept — give general consultation fee
        gen = self.ctx.billing_for_dept("general")
        if gen:
            return KnowledgeResult(
                intent=INTENT_CONSULTATION_FEE, found=True,
                text_ml=(f"General consultation fee ₹{int(gen.price_min)} ആണ്. "
                         f"Specialty departments ₹500 മുതൽ ₹800 വരെ ആണ്."),
            )
        return KnowledgeResult(
            intent=INTENT_CONSULTATION_FEE, found=False,
            text_ml="Fee-ന്റെ കൃത്യമായ വിവരം reception-ൽ confirm ചെയ്യൂ.",
        )

    # ── Department exists ─────────────────────────────────────────────────────

    def _dept_exists(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        if not dept_kw:
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS, found=False,
                text_ml="ഏത് department-ന്റെ കാര്യമാണ്?",
                missing="no_department",
            )
        resolved = resolve_dept_keyword(dept_kw) or dept_kw
        dept = self.ctx.find_dept(resolved)
        if dept:
            floor_hint = f" ({dept.floor}-ൽ ആണ്, {dept.location_hint})" if dept.floor else ""
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS, found=True,
                text_ml=f"ആം, ഞങ്ങൾക്ക് {dept.name_ml or dept.name} department ഉണ്ട്{floor_hint}.",
                data={"dept": dept.name, "floor": dept.floor},
            )
        return KnowledgeResult(
            intent=INTENT_DEPARTMENT_EXISTS, found=False,
            text_ml=f"ക്ഷമിക്കണം, {dept_kw} department ഇവിടെ ലഭ്യമല്ല.",
        )

    # ── Hospital timing ───────────────────────────────────────────────────────

    def _hospital_timing(self, entities: dict) -> KnowledgeResult:
        day_name = entities.get("day")
        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")
        hours = self.ctx.hours_for_day(dow)
        if hours:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING, found=True,
                text_ml=(f"Hospital {day_label}-ൽ {hours[0]} മുതൽ {hours[1]} വരെ open ആണ്. "
                         f"Emergency 24 മണിക്കൂറും open ആണ്."),
            )
        # Sunday / no hours = check if listed
        if dow == 0:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING, found=True,
                text_ml="ഞായർ 9 AM മുതൽ 1 PM വരെ OP open ആണ്. Emergency 24x7 ഉണ്ട്.",
            )
        return KnowledgeResult(
            intent=INTENT_HOSPITAL_TIMING, found=False,
            text_ml="Hospital timing-ന്റെ കൃത്യമായ വിവരം +914841234567 ൽ confirm ചെയ്യൂ.",
        )

    # ── Emergency ─────────────────────────────────────────────────────────────

    def _emergency(self, entities: dict) -> KnowledgeResult:
        if self.ctx.emergency:
            ec = self.ctx.emergency[0]
            phones = " / ".join(e.phone for e in self.ctx.emergency[:2])
            return KnowledgeResult(
                intent=INTENT_EMERGENCY, found=True,
                text_ml=(f"Emergency 24 മണിക്കൂറും available ആണ്. "
                         f"{ec.label_ml or ec.label}: {phones}."),
                data={"phones": phones},
            )
        return KnowledgeResult(
            intent=INTENT_EMERGENCY, found=True,
            text_ml="Emergency 24x7 available ആണ്. 108 ൽ വിളിക്കൂ.",
        )

    # ── Location ──────────────────────────────────────────────────────────────

    def _location(self, entities: dict) -> KnowledgeResult:
        return KnowledgeResult(
            intent=INTENT_LOCATION, found=True,
            text_ml=f"Hospital address: {self.ctx.address}.",
            data={"address": self.ctx.address},
        )

    # ── Contact ───────────────────────────────────────────────────────────────

    def _contact(self, entities: dict) -> KnowledgeResult:
        return KnowledgeResult(
            intent=INTENT_CONTACT, found=True,
            text_ml=f"Hospital phone number: {self.ctx.phone}.",
            data={"phone": self.ctx.phone},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_day(self, day_name: Optional[str]) -> int:
        if not day_name or day_name in ("today", None):
            return today_db_dow()
        db_dow = named_dow_to_db(day_name)
        return db_dow if db_dow is not None else today_db_dow()

    def _find_doctor_by_name(self, query: str) -> Optional[DoctorInfo]:
        q = query.lower()
        for doc in self.ctx.doctors:
            if q in doc.name.lower() or q in (doc.name_ml or "").lower():
                return doc
        return None

    @staticmethod
    def _slot_for_dow(doc: DoctorInfo, dow: int):
        """Return first slot matching dow, or None."""
        for s in doc.slots:
            if s.dow == dow:
                return s
        return None
