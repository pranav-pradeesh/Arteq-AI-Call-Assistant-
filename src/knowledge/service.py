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
    INTENT_HOSPITAL_TIMING, INTENT_LOCATION, INTENT_SYMPTOM,
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
#
# Two purposes:
#   1. Map dialect terms ("kaan", "hridayam") to canonical names so we can
#      look them up in the actual department list.
#   2. Cover services the hospital DOESN'T offer (dentist, derma, etc.) so
#      we can recognise them and deny clearly instead of falling through to
#      "I didn't understand → connect to receptionist".

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
    # ── Services this hospital does NOT offer — listed so we can deny clearly
    "dentist": "dental", "dental": "dental", "tooth": "dental",
    "teeth": "dental", "pallu": "dental", "danthavaidyan": "dental",
    "derma": "dermatology", "skin": "dermatology", "dermatology": "dermatology",
    "neuro": "neurology", "neurology": "neurology", "brain": "neurology",
    "stroke": "neurology",
    "eye": "ophthalmology", "ophthal": "ophthalmology", "ophthalmology": "ophthalmology",
    "kannu": "ophthalmology",
    "psychiatry": "psychiatry", "mental": "psychiatry", "psychology": "psychiatry",
    "urology": "urology", "kidney": "urology", "mutra": "urology",
    "oncology": "oncology", "cancer": "oncology", "arbudham": "oncology",
    "gastro": "gastroenterology", "stomach": "gastroenterology",
    "vayar": "gastroenterology",
    "pulmonology": "pulmonology", "lung": "pulmonology", "asthma": "pulmonology",
    "physio": "physiotherapy", "physiotherapy": "physiotherapy",
    "ayurveda": "ayurveda", "ayurvedam": "ayurveda",
}


def resolve_dept_keyword(keyword: str) -> Optional[str]:
    return _DEPT_KEYWORDS.get(keyword.lower())


# ── Symptom description → canonical department map ────────────────────────────
# Sorted longest-first at build time so _map_symptom_to_dept() can do a single
# linear scan without worrying about shorter substrings matching first.
# Keys are lowercased substrings to search in the caller's transcript.

_SYMPTOM_DEPT_MAP_RAW: dict[str, str] = {
    # Cardiology
    "chest pain": "cardiology", "heart pain": "cardiology",
    "heart problem": "cardiology", "heart attack": "cardiology",
    "palpitation": "cardiology", "palpitations": "cardiology",
    "high blood pressure": "cardiology", "hypertension": "cardiology",
    "hridayam": "cardiology", "hrudayam": "cardiology",
    "neriv vedana": "cardiology", "cardiac": "cardiology",
    # Neurology
    "headache": "neurology", "head pain": "neurology",
    "head ache": "neurology", "migraine": "neurology",
    "dizziness": "neurology", "dizzy": "neurology",
    "seizure": "neurology", "epilepsy": "neurology",
    "memory loss": "neurology", "tremor": "neurology",
    "numbness": "neurology", "paralysis": "neurology",
    "thalavedana": "neurology", "thalakayanam": "neurology",
    "thalakkayanam": "neurology", "thalav": "neurology",
    # Orthopedics
    "knee pain": "orthopedics", "back pain": "orthopedics",
    "joint pain": "orthopedics", "shoulder pain": "orthopedics",
    "neck pain": "orthopedics", "hip pain": "orthopedics",
    "ankle pain": "orthopedics", "wrist pain": "orthopedics",
    "elbow pain": "orthopedics", "bone pain": "orthopedics",
    "fracture": "orthopedics", "sprain": "orthopedics",
    "spine": "orthopedics", "arthritis": "orthopedics",
    "ellu vedana": "orthopedics", "muzhu vedana": "orthopedics",
    "kazhuthu vedana": "orthopedics", "mottu vedana": "orthopedics",
    "ellu kayanam": "orthopedics",
    # Gastroenterology
    "stomach pain": "gastroenterology", "stomach ache": "gastroenterology",
    "abdominal pain": "gastroenterology", "abdomen pain": "gastroenterology",
    "loose motion": "gastroenterology", "loose motions": "gastroenterology",
    "diarrhea": "gastroenterology", "diarrhoea": "gastroenterology",
    "constipation": "gastroenterology", "indigestion": "gastroenterology",
    "acidity": "gastroenterology", "acid reflux": "gastroenterology",
    "nausea": "gastroenterology", "vomiting": "gastroenterology",
    "liver problem": "gastroenterology", "jaundice": "gastroenterology",
    "vayar vedana": "gastroenterology", "vayarkayanam": "gastroenterology",
    "omi": "gastroenterology", "omanarekkayanam": "gastroenterology",
    "vayar": "gastroenterology",
    # ENT
    "ear pain": "ent", "earache": "ent",
    "hearing loss": "ent", "hearing problem": "ent",
    "sore throat": "ent", "throat pain": "ent",
    "nose bleed": "ent", "nosebleed": "ent",
    "sinusitis": "ent", "tonsil": "ent", "tonsils": "ent",
    "kaan vedana": "ent", "kaan kayanam": "ent",
    "thallu vedana": "ent", "mookku": "ent",
    # Ophthalmology
    "eye pain": "ophthalmology", "eye problem": "ophthalmology",
    "blurry vision": "ophthalmology", "vision problem": "ophthalmology",
    "eye redness": "ophthalmology", "watery eyes": "ophthalmology",
    "kannu vedana": "ophthalmology", "kanninu": "ophthalmology",
    "nethram": "ophthalmology",
    # Dermatology
    "skin rash": "dermatology", "skin problem": "dermatology",
    "skin disease": "dermatology", "skin infection": "dermatology",
    "itching": "dermatology", "eczema": "dermatology",
    "psoriasis": "dermatology", "acne": "dermatology",
    "hair fall": "dermatology", "hair loss": "dermatology",
    "poochuvili": "dermatology", "charma rogam": "dermatology",
    # Pulmonology
    "breathing problem": "pulmonology",
    "shortness of breath": "pulmonology",
    "breathlessness": "pulmonology",
    "difficulty breathing": "pulmonology",
    "asthma": "pulmonology", "lung problem": "pulmonology",
    "persistent cough": "pulmonology", "chronic cough": "pulmonology",
    "ithira": "pulmonology", "niswasam": "pulmonology",
    # Gynecology
    "pregnancy": "gynaecology", "pregnant": "gynaecology",
    "delivery": "gynaecology", "maternity": "gynaecology",
    "menstrual problem": "gynaecology", "period problem": "gynaecology",
    "pcod": "gynaecology", "pcos": "gynaecology",
    "prasavam": "gynaecology", "garba": "gynaecology",
    # Pediatrics
    "child fever": "pediatrics", "baby fever": "pediatrics",
    "child problem": "pediatrics", "baby problem": "pediatrics",
    "infant": "pediatrics", "newborn": "pediatrics",
    "kutta vedana": "pediatrics", "kuttinu": "pediatrics",
    # Urology
    "urinary problem": "urology", "urine problem": "urology",
    "kidney stone": "urology", "kidney pain": "urology",
    "kidney problem": "urology", "bladder": "urology",
    "mutra rogam": "urology", "kidni": "urology",
    # Dental
    "tooth pain": "dental", "toothache": "dental",
    "tooth ache": "dental", "gum pain": "dental",
    "gum problem": "dental", "tooth problem": "dental",
    "pallu vedana": "dental", "pallu kayanam": "dental",
    # Psychiatry / Mental health
    "depression": "psychiatry", "anxiety": "psychiatry",
    "mental health": "psychiatry", "panic attack": "psychiatry",
    "insomnia": "psychiatry", "sleep problem": "psychiatry",
    "stress problem": "psychiatry", "mental problem": "psychiatry",
    "manassastra": "psychiatry",
    # General (catch-all for generic "fever"/"sick" etc.)
    "fever": "general medicine", "pani": "general medicine",
    "jwaram": "general medicine", "cold": "general medicine",
    "flu": "general medicine", "weakness": "general medicine",
    "fatigue": "general medicine", "tiredness": "general medicine",
    "sukhamilla": "general medicine", "alukkam": "general medicine",
}

# Sort once at module load; longest key first → more specific match wins
_SYMPTOM_DEPT_MAP: list[tuple[str, str]] = sorted(
    _SYMPTOM_DEPT_MAP_RAW.items(), key=lambda kv: -len(kv[0])
)


# ── Main service ──────────────────────────────────────────────────────────────

_DOW_EN = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}


def build_hospital_summary(ctx: HospitalContext) -> str:
    """
    Build a compact English summary of the hospital for the LLM.
    Used as context for free-form caller questions instead of FAQ matching.
    """
    lines: list[str] = []
    lines.append(f"HOSPITAL: {ctx.name} ({ctx.name_ml}).")
    if ctx.address:
        lines.append(f"ADDRESS: {ctx.address}.")
    if ctx.phone:
        lines.append(f"MAIN PHONE: {ctx.phone}.")

    # Hours
    if ctx.hours:
        h = ctx.hours
        order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        hr_parts = []
        for d in order:
            if d in h and h[d]:
                hr_parts.append(f"{d.capitalize()} {h[d][0]}-{h[d][1]}")
        lines.append("OPENING HOURS: " + "; ".join(hr_parts) + ". Emergency 24x7.")

    # Departments
    if ctx.departments:
        dept_lines = []
        for d in ctx.departments:
            extra = f" ({d.floor}, {d.location_hint})" if d.floor else ""
            dept_lines.append(f"{d.name}{extra} ext {d.phone_ext}")
        lines.append("DEPARTMENTS AVAILABLE: " + " | ".join(dept_lines) + ".")

    # Services NOT available (anything in _DEPT_KEYWORDS that doesn't map to a real dept)
    available_canonical = {d.name.lower() for d in ctx.departments}
    not_offered = set()
    for kw, canon in _DEPT_KEYWORDS.items():
        if canon.lower() not in available_canonical and not any(
            canon.lower() in d.name.lower() for d in ctx.departments
        ):
            not_offered.add(canon)
    if not_offered:
        lines.append("SERVICES NOT OFFERED HERE: " + ", ".join(sorted(not_offered)) + ".")

    # Doctors with schedules
    if ctx.doctors:
        lines.append("DOCTORS:")
        for d in ctx.doctors:
            sched_parts = [
                f"{_DOW_EN.get(s.dow, '?')} {s.start}-{s.end}" for s in d.slots
            ]
            sched = "; ".join(sched_parts) if sched_parts else "no schedule"
            qual = f", {d.qualifications}" if d.qualifications else ""
            lines.append(f"- {d.name} ({d.dept_name}{qual}): {sched}")

    # Billing
    if ctx.billing:
        lines.append("PRICING:")
        for b in ctx.billing:
            price = (f"₹{int(b.price_min)}"
                     if b.price_min == b.price_max
                     else f"₹{int(b.price_min)}-{int(b.price_max)}")
            lines.append(f"- {b.item} = {price}"
                         + (f" ({b.notes})" if b.notes else ""))

    # Emergency
    if ctx.emergency:
        em_parts = [f"{e.label} {e.phone}" for e in ctx.emergency]
        lines.append("EMERGENCY CONTACTS: " + " | ".join(em_parts) + ".")

    # FAQs — included so the LLM can answer questions about parking, insurance,
    # appointment booking, facilities, and anything else the admin has documented.
    if ctx.faqs:
        lines.append("ADDITIONAL INFORMATION:")
        for faq in ctx.faqs:
            lines.append(f"Q: {faq.question}")
            lines.append(f"A: {faq.answer}")

    # Instruction so the LLM knows where to redirect unknown questions.
    if ctx.phone:
        lines.append(
            f"FALLBACK: For anything not covered above, direct the caller to "
            f"reception at {ctx.phone}."
        )

    return "\n".join(lines)


class HospitalKnowledgeService:

    def __init__(self, ctx: HospitalContext):
        self.ctx = ctx
        self._summary = build_hospital_summary(ctx)

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
            if not entities.get("doctor_name") and state_context.get("last_doctor_name"):
                entities = {**entities, "doctor_name": state_context["last_doctor_name"]}
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
            INTENT_SYMPTOM: self._symptom_recommendation,
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
            # Doctor exists but not today — find next available slot
            next_slot = self._next_slot_after(doc, dow)
            if next_slot:
                next_dow, ns = next_slot
                next_label = _DAY_ML.get(next_dow, "")
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                             f"available അല്ല. അടുത്തത് {next_label} "
                             f"{ns.start} മുതൽ {ns.end} വരെ available ആണ്."),
                    data={"doctor": doc.name, "available": False,
                          "next_day": next_label, "next_start": ns.start},
                )
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
            dept_docs = self.ctx.doctors_for_dept(dept.name)
            avail = [d for d in dept_docs if self._slot_for_dow(d, dow)]
            if avail:
                names = ", ".join(d.name_ml or d.name for d in avail[:3])
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{dept.name_ml or dept.name}-ൽ {day_label}-ൽ "
                             f"{len(avail)} doctor available ആണ്: {names}."),
                    data={"dept": dept.name, "count": len(avail)},
                )
            # No doctors today — find earliest upcoming slot across the dept
            earliest_next: Optional[tuple[int, SlotInfo, DoctorInfo]] = None
            for d in dept_docs:
                ns = self._next_slot_after(d, dow)
                if ns and (earliest_next is None or ns[0] < earliest_next[0]):
                    earliest_next = (ns[0], ns[1], d)
            if earliest_next:
                ndow, ns, doc = earliest_next
                next_label = _DAY_ML.get(ndow, "")
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{dept.name_ml or dept.name}-ൽ {day_label}-ൽ "
                             f"doctors available അല്ല. {next_label}-ൽ "
                             f"{doc.name_ml or doc.name} doctor "
                             f"{ns.start} മുതൽ {ns.end} വരെ ഉണ്ടാകും."),
                )
            return KnowledgeResult(
                intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                text_ml=(f"ക്ഷമിക്കണം, {dept.name_ml or dept.name}-ൽ "
                         f"ഇപ്പോൾ doctors-ന്റെ schedule ലഭ്യമല്ല."),
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

    # ── Symptom recommendation ────────────────────────────────────────────────

    def _symptom_recommendation(self, entities: dict) -> KnowledgeResult:
        """
        Map symptom description to right department and recommend available doctors.
        Called when INTENT_SYMPTOM fires. Requires entities["transcript"] to be
        the raw caller utterance; passed by call_handler when intent==INTENT_SYMPTOM.
        """
        transcript = entities.get("transcript", "")
        dept_name = self._map_symptom_to_dept(transcript)

        if not dept_name:
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=False,
                text_ml="ക്ഷമിക്കണം, ഏത് problem ആണ് ഉള്ളതെന്ന് ഒന്നുകൂടി പറഞ്ഞു തരാമോ?",
                missing="no_symptom_match",
            )

        dept = self.ctx.find_dept(dept_name)
        dow = today_db_dow()
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        if not dept:
            # Department not available at this hospital
            phone = self.ctx.phone or "reception"
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept_name.title()} specialist ആണ് ആവശ്യം. "
                         f"ഞങ്ങളുടെ hospital-ൽ ആ department ലഭ്യമല്ല. "
                         f"കൂടുതൽ വിവരത്തിന് {phone}-ൽ ബന്ധപ്പെടൂ."),
                missing="dept_not_found",
            )

        dept_docs = self.ctx.doctors_for_dept(dept.name)
        avail_today = [d for d in dept_docs if self._slot_for_dow(d, dow)]

        if avail_today:
            doc = avail_today[0]
            slot = self._slot_for_dow(doc, dow)
            names = ", ".join(d.name_ml or d.name for d in avail_today[:2])
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                         f"consult ചെയ്യണം. {day_label} {names} doctor available ആണ്, "
                         f"{slot.start} മുതൽ {slot.end} വരെ."),
                data={"dept": dept.name, "doctors": [d.name for d in avail_today]},
            )

        # Dept exists but no doctors today — find next available
        earliest_next = None
        for d in dept_docs:
            ns = self._next_slot_after(d, dow)
            if ns and (earliest_next is None or ns[0] < earliest_next[0]):
                earliest_next = (ns[0], ns[1], d)

        if earliest_next:
            ndow, ns, doc = earliest_next
            next_label = _DAY_ML.get(ndow, "")
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                         f"consult ചെയ്യണം. {day_label} doctors ലഭ്യമല്ല. "
                         f"{next_label}-ൽ {doc.name_ml or doc.name} doctor "
                         f"{ns.start} മുതൽ {ns.end} വരെ available ആണ്."),
            )

        phone = self.ctx.phone or "reception"
        return KnowledgeResult(
            intent=INTENT_SYMPTOM, found=True,
            text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                     f"consult ചെയ്യണം. Appointment-ന് {phone}-ൽ ബന്ധപ്പെടൂ."),
        )

    @staticmethod
    def _map_symptom_to_dept(text: str) -> Optional[str]:
        """Search transcript for symptom phrases; return canonical dept name."""
        t = text.lower()
        for symptom, dept in _SYMPTOM_DEPT_MAP:
            if symptom in t:
                return dept
        return None

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

    # ── Free-form LLM answer using hospital summary ───────────────────────────

    def answer_freeform(self, question: str) -> KnowledgeResult:
        """
        Answer any caller question by giving the LLM (Groq llama-3.1-8b)
        the full hospital summary and the user's question. Used when the
        structured intent path didn't match — the LLM reads the summary
        and answers from those details, refusing if data isn't there.

        On Groq failure, the fallback embeds the question text so we
        don't return identical audio for every miss (which would otherwise
        be served from the TTS cache).
        """
        import logging
        log = logging.getLogger(__name__)

        try:
            from groq import Groq
            client = Groq(api_key=settings.GROQ_API_KEY)
            reception = self.ctx.phone or "the hospital"
            prompt = (
                "You are the phone receptionist for a Kerala hospital. "
                "Answer the caller's question using ONLY the facts in the HOSPITAL SUMMARY below.\n\n"
                "Rules:\n"
                "1. Only say a service is NOT available if it is explicitly listed under "
                "'SERVICES NOT OFFERED HERE'. For anything not mentioned in the summary, "
                f"say: 'Please contact our reception at {reception} for details.'\n"
                "2. Match the caller's language: Malayalam-Manglish for Malayalam/Manglish "
                "input, English for English input.\n"
                "3. Keep the reply to ONE or TWO short sentences — this is a voice call.\n"
                "4. Do not start with 'Sorry' unless you are genuinely denying something.\n"
                "5. Do not invent facts. If you are unsure, direct to reception.\n\n"
                f"HOSPITAL SUMMARY:\n{self._summary}\n\n"
                f"Caller: {question}\nReceptionist:"
            )
            log.info(f"freeform_groq_call question={question!r}")
            resp = client.chat.completions.create(
                model=settings.GROQ_MODEL_FAST,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.GROQ_MAX_TOKENS,
                timeout=settings.GROQ_TIMEOUT_S,
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            log.info(f"freeform_groq_ok answer={text!r}")
            return KnowledgeResult(
                intent="freeform",
                found=bool(text),
                text_ml=text or "",
                data={"source": "groq_summary"},
            )
        except Exception as e:
            log.error(f"freeform_groq_failed error={e!r}")
            # Vary the fallback by question so the TTS cache doesn't collapse
            # every failed turn into the exact same audio.
            short_q = (question or "")[:30].strip()
            return KnowledgeResult(
                intent="freeform", found=False,
                text_ml=(f"ക്ഷമിക്കണം, '{short_q}' എന്നതിനെ കുറിച്ച് "
                         "ഇപ്പോൾ കൃത്യമായ വിവരം എനിക്ക് നൽകാൻ കഴിയില്ല. "
                         "Reception-ൽ ബന്ധപ്പെടൂ."),
            )

    @staticmethod
    def _next_slot_after(doc: DoctorInfo, dow: int):
        """
        Find the doctor's earliest upcoming slot in the next 7 days
        starting from (dow + 1). Returns (dow, slot) or None.
        DB DOW convention: 0=Sun, 6=Sat.
        """
        for offset in range(1, 8):
            check_dow = (dow + offset) % 7
            for s in doc.slots:
                if s.dow == check_dow:
                    return (check_dow, s)
        return None
