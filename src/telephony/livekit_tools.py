"""
LiveKit function tools for the Arteq hospital voice agent.

Each tool is a Python async function decorated with @function_tool.
The LLM (Groq LLaMA 70B) calls them when it decides an action is needed.
Session state (hospital_id, caller_phone, call_id) is accessed via context.userdata.

Tool catalogue:
  book_appointment          — multi-turn appointment booking with DB write + SMS + staff alert
  cancel_appointment        — cancel an existing appointment
  request_callback          — register a call-back request
  get_doctor_schedule       — return a doctor's consulting schedule
  get_department_info       — floor, hours, extension for a department
  send_location_sms         — maps link to caller's phone
  alert_emergency           — IMMEDIATE emergency alert to staff + flag for transfer
  transfer_to_department    — signal call hand-off to a department
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date as _date
from datetime import datetime
from datetime import time as _time
from typing import Optional

import pytz
import structlog

logger = structlog.get_logger(__name__)

_IST = pytz.timezone("Asia/Kolkata")

# Relative day words → offset from today. The LLM is told to send YYYY-MM-DD, but
# on live calls it (and the caller, via the transcript) often pass natural words —
# "tomorrow", "നാളെ" — so we resolve them rather than failing the booking.
_REL_DAYS = {
    "today": 0, "tonight": 0, "ഇന്ന്": 0, "ഇന്ന": 0, "ഇപ്പോൾ": 0, "ഇപ്പോള്": 0,
    "tomorrow": 1, "tmrw": 1, "നാളെ": 1,
    "day after tomorrow": 2, "day after": 2, "overmorrow": 2,
    "മറ്റന്നാൾ": 2, "മറ്റന്നാള്": 2, "മറ്റന്നാള": 2,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
# Malayalam time-of-day words → hint for am/pm when the number alone is ambiguous.
_ML_MERIDIEM = {
    "രാവിലെ": "am", "പുലർച്ച": "am", "പുലര്‍ച്ച": "am",   # morning / dawn
    "ഉച്ച": "pm", "ഉച്ചയ്ക്ക്": "pm",                      # noon
    "വൈകുന്നേരം": "pm", "വൈകീട്ട്": "pm", "ഉച്ചതിരിഞ്ഞ്": "pm",  # evening / afternoon
    "രാത്രി": "pm", "രാത്രിയിൽ": "pm",                     # night
}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%d %B", "%d %b")


def _parse_date(date_str: str) -> Optional[_date]:
    s = (date_str or "").strip()
    if not s:
        return None
    today = datetime.now(_IST).date()
    low = s.lower()
    for word, off in _REL_DAYS.items():
        if word in low:
            return _date.fromordinal(today.toordinal() + off)
    for name, wd in _WEEKDAYS.items():
        if name in low:
            ahead = (wd - today.weekday()) % 7
            ahead = ahead or 7  # "monday" said on a Monday means next Monday
            return _date.fromordinal(today.toordinal() + ahead)
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            # Formats without a year default to 1900; roll to this year (or next).
            if d.year == 1900:
                d = d.replace(year=today.year)
                if d < today:
                    d = d.replace(year=today.year + 1)
            return d
        except ValueError:
            continue
    return None


def _parse_time(time_str: str) -> Optional[_time]:
    s = (time_str or "").strip()
    if not s:
        return None
    low = s.lower()
    meridiem = None
    if "pm" in low or "p.m" in low:
        meridiem = "pm"
    elif "am" in low or "a.m" in low:
        meridiem = "am"
    for word, m in _ML_MERIDIEM.items():
        if word in s:
            meridiem = meridiem or m
            break
    m = re.search(r"(\d{1,2})\s*[:.]?\s*(\d{2})?", s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return _time(hour, minute)


def _parse_slot(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse a date + time into an IST-aware datetime, or None on failure.

    Accepts the canonical 'YYYY-MM-DD' + 'HH:MM' the LLM is told to send, plus the
    loose natural forms it and callers actually use: relative days ('tomorrow',
    'നാളെ'), weekday names, common date layouts, and 12-hour / Malayalam times
    ('രാവിലെ 10 മണി', '3 pm'). Returns None only when no time or date is found."""
    d = _parse_date(date_str)
    t = _parse_time(time_str)
    if d is None or t is None:
        return None
    return _IST.localize(datetime.combine(d, t))

# ── helpers ──────────────────────────────────────────────────────────────────

def _ud(context, key: str, default=None):
    """Safe userdata getter — works for both context.userdata and context.session.userdata."""
    try:
        return context.userdata.get(key, default)
    except AttributeError:
        try:
            return context.session.userdata.get(key, default)
        except AttributeError:
            return default


def _mark_intent(context, intent: str) -> None:
    """Record that a tool ran this call, so the call log's `intents` column
    reflects what actually happened (booked / cancelled / emergency / …)."""
    try:
        ud = context.userdata
    except AttributeError:
        try:
            ud = context.session.userdata
        except AttributeError:
            return
    try:
        intents = ud.setdefault("intents", [])
        if intent not in intents:
            intents.append(intent)
    except Exception:
        pass


# Honorifics callers prefix to a doctor's name, in English + Malayalam. Stripped
# before matching so "Doctor Anil" / "ഡോക്ടർ അനില്" resolve to "Anil".
_HONORIFICS = (
    "doctor", "dr.", "dr", "ഡോക്ടർ", "ഡോക്ടര്‍", "ഡോക്റ്റർ", "ഡോ.", "ഡോ",
)


def _strip_honorifics(s: str) -> str:
    s = (s or "").strip().lower()
    for h in _HONORIFICS:
        if s.startswith(h):
            s = s[len(h):].lstrip(" .‌")
            break
    return s.strip(" .")


def _fuzzy_find_doctor(hospital_ctx, name: str):
    """Honorific-tolerant, bidirectional name match. Returns (doctor_id, dept_id, full_name).

    Handles "Doctor Anil", "ഡോക്ടർ അനില്", a bare first name, or a fuller name than
    is stored — matches if either string contains the other, or any name token
    overlaps. A bare department word ("cardiology") deliberately does NOT match a
    doctor here; the caller's tools fall back to department resolution instead.
    """
    q = _strip_honorifics(name)
    if not q:
        return None, None, name
    q_tokens = {t for t in q.split() if len(t) > 1}
    for doc in (hospital_ctx.doctors if hospital_ctx else []):
        for cand in (doc.name, getattr(doc, "name_ml", "") or ""):
            c = _strip_honorifics(cand)
            if not c:
                continue
            if q in c or c in q or (q_tokens & {t for t in c.split() if len(t) > 1}):
                dept_id = getattr(doc, "dept_id", None)
                return str(doc.id), str(dept_id) if dept_id else None, doc.name
    return None, None, name


# ── tools ──────────────────────────────────────────────────────────────────

try:
    from livekit.agents import function_tool, RunContext

    @function_tool
    async def book_appointment(
        context: RunContext,
        patient_name: str,
        doctor_name: str,
        appointment_date: str,
        appointment_time: str,
        notes: str = "",
    ) -> str:
        """
        Book a hospital appointment for the caller. Use this once you have collected
        the patient's name, preferred doctor (or department), date (YYYY-MM-DD), and
        time (HH:MM 24-hour). Returns confirmation text to speak to the caller.
        """
        _mark_intent(context, "book_appointment")
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", str(uuid.uuid4()))
        hospital_ctx = _ud(context, "hospital_ctx")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        slot = _parse_slot(appointment_date, appointment_time)
        if slot is None:
            # Never write a booking with an unparseable time — ask, don't guess.
            logger.warning("tool_book_bad_slot", date=appointment_date, time=appointment_time)
            return "I didn't quite catch the date and time. Which day and what time would you like?"

        doctor_id, dept_id, resolved_name = _fuzzy_find_doctor(hospital_ctx, doctor_name)

        # Load balancing: when the caller named a department (or "any doctor")
        # rather than a specific doctor, pick the least-loaded doctor who
        # consults that day instead of funnelling everyone to one name.
        if not doctor_id and hospital_ctx:
            dept = hospital_ctx.find_dept(doctor_name)
            if dept:
                try:
                    from src.db.queries import get_least_loaded_doctor
                    chosen = await get_least_loaded_doctor(
                        hospital_id, dept.id, slot.date().isoformat()
                    )
                    if chosen:
                        doctor_id = chosen["id"]
                        dept_id = chosen["dept_id"]
                        resolved_name = chosen["name"]
                        logger.info("tool_book_load_balanced", dept=dept.name, doctor=resolved_name)
                except Exception as exc:
                    logger.warning("tool_book_load_balance_failed", error=str(exc))

        # Queue priority — emergency / senior get seen earlier once paid.
        from src.services.priority import compute_priority, extract_age
        priority = compute_priority(age=extract_age(notes), notes=notes)

        # Try HIS first (if configured). Failure falls through to local DB, but
        # is recorded as his_sync_status='failed' so staff can reconcile — a
        # silent fallback left the HIS and Arteq permanently out of sync.
        his_appt_id: Optional[str] = None
        his_sync_status: Optional[str] = None
        try:
            from src.integrations.his.service import get_his_adapter
            his = await get_his_adapter(hospital_id)
            if his:
                patient = await his.search_patient(caller_phone)
                his_patient_id = patient["his_patient_id"] if patient else None
                his_appt_id = await his.create_appointment(
                    his_patient_id=his_patient_id,
                    patient_name=patient_name,
                    patient_phone=caller_phone,
                    his_doctor_id=doctor_id or doctor_name,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    notes=notes,
                )
                his_sync_status = "synced" if his_appt_id else "failed"
                logger.info("his_appointment_created", his_appt_id=his_appt_id)
        except Exception as exc:
            his_sync_status = "failed"
            logger.error("his_book_failed_fallback_to_db", error=str(exc))

        try:
            from src.db.queries import create_appointment
            result = await create_appointment(
                hospital_id=hospital_id,
                patient_name=patient_name,
                patient_phone=caller_phone,
                doctor_id=doctor_id,
                dept_id=dept_id,
                slot_time=slot,
                notes=notes,
                call_id=call_id,
                his_appointment_id=his_appt_id,
                priority=priority,
                his_sync_status=his_sync_status,
            )
            appt_id = result["id"]
            confirmation_code = result["confirmation_code"]
            logger.info("tool_book_appointment", appt_id=appt_id, doctor=resolved_name,
                        his_synced=bool(his_appt_id))
        except Exception as exc:
            err = str(exc).lower()
            if ("unique" in err or "duplicate" in err
                    or "ix_appt_no_double_book" in err or "slot_already_booked" in err):
                logger.warning("tool_book_slot_conflict", doctor=resolved_name, slot=slot)
                return (
                    f"That slot with Dr. {resolved_name} is already fully booked. "
                    "Would you like a different time or another doctor?"
                )
            logger.error("tool_book_appointment_failed", error=str(exc))
            return "Booking system temporarily unavailable — please call the front desk."

        # Fire-and-forget: patient notification (WhatsApp/SMS) + staff alert
        async def _side_effects():
            try:
                from src.services.staff_alert import StaffAlertService
                from src.services.whatsapp_service import get_messenger
                await get_messenger().send_appointment_confirmation(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    patient_name=patient_name,
                    doctor_name=resolved_name,
                    date=appointment_date,
                    time=appointment_time,
                    code=confirmation_code,
                )
                alerts = StaffAlertService()
                await alerts.alert_new_booking(
                    patient_name=patient_name,
                    patient_phone=caller_phone,
                    doctor_name=resolved_name,
                    date=appointment_date,
                    time=appointment_time,
                    call_id=call_id,
                )
            except Exception as exc:
                logger.warning("tool_book_sms_alert_failed", error=str(exc))

        asyncio.create_task(_side_effects())

        slot_readable = slot.strftime("%d %B %Y at %I:%M %p") if slot else f"{appointment_date} {appointment_time}"
        spoken_code = " ".join(confirmation_code.replace("ARYA-", ""))  # spell out for clarity
        return (
            f"Appointment booked for {patient_name} with Dr. {resolved_name} "
            f"on {slot_readable}. Your booking code is {spoken_code}. "
            "Please pay the consultation fee at the hospital to activate your "
            "queue token. I've sent the details to your phone."
        )


    @function_tool
    async def cancel_appointment(
        context: RunContext,
        patient_name: str,
        doctor_name: str = "",
        appointment_date: str = "",
    ) -> str:
        """
        Cancel an existing appointment for the caller. Provide patient_name and
        optionally doctor_name or appointment_date to identify which appointment.
        """
        _mark_intent(context, "cancel_appointment")
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", "")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        try:
            from src.db.queries import get_appointments_by_phone, cancel_appointment_by_id
            appts = await get_appointments_by_phone(caller_phone, hospital_id)
            if not appts:
                return "No active appointments found for this number."

            # Pick the first (most recent) — if doctor_name given, try to match
            target = appts[0]
            if doctor_name:
                for a in appts:
                    if doctor_name.lower() in (a.get("doctor_name") or "").lower():
                        target = a
                        break

            # Cancel in HIS if appointment was synced there; a failed HIS
            # cancel is flagged for manual reconciliation, not swallowed.
            his_appt_id = target.get("his_appointment_id")
            if his_appt_id:
                try:
                    from src.integrations.his.service import get_his_adapter
                    his = await get_his_adapter(hospital_id)
                    if his:
                        cancelled = await his.cancel_appointment(his_appt_id)
                        if cancelled:
                            logger.info("his_appointment_cancelled", his_appt_id=his_appt_id)
                        else:
                            raise RuntimeError("HIS returned failure")
                except Exception as exc:
                    logger.error("his_cancel_failed", error=str(exc))
                    from src.db.queries import set_his_sync_status
                    await set_his_sync_status(str(target["id"]), "failed")

            ok = await cancel_appointment_by_id(str(target["id"]), hospital_id)
            if not ok:
                return "Could not cancel that appointment — please contact the front desk."

            doc  = target.get("doctor_name") or doctor_name or "the doctor"
            date = target["slot_time"].strftime("%d %B") if target.get("slot_time") else appointment_date

            async def _side_effects():
                try:
                    from src.services.sms_service import SMSService
                    from src.services.staff_alert import StaffAlertService
                    await SMSService().send_appointment_cancellation(
                        phone=caller_phone,
                        hospital_name=hospital_name,
                        patient_name=patient_name,
                        doctor_name=doc,
                        date=date,
                    )
                    await StaffAlertService().alert_cancellation(
                        patient_name=patient_name,
                        patient_phone=caller_phone,
                        doctor_name=doc,
                        date=date,
                        call_id=call_id,
                    )
                except Exception as exc:
                    logger.warning("tool_cancel_sms_failed", error=str(exc))

            asyncio.create_task(_side_effects())
            return f"Appointment with Dr. {doc} on {date} has been cancelled. Confirmation SMS sent."

        except Exception as exc:
            logger.error("tool_cancel_appointment_failed", error=str(exc))
            return "Unable to cancel right now — please contact the front desk."


    @function_tool
    async def request_callback(
        context: RunContext,
        patient_name: str,
        reason: str,
        preferred_time: str = "",
    ) -> str:
        """
        Register a callback request when the caller cannot talk now or needs
        to be called back later. Collect patient_name, reason, and optionally
        preferred_time (e.g. 'tomorrow morning', '3pm').
        """
        _mark_intent(context, "request_callback")
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", "")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        try:
            from src.db.queries import create_callback
            cb_id = await create_callback(
                hospital_id=hospital_id,
                patient_phone=caller_phone,
                patient_name=patient_name,
                reason=reason,
                preferred_time=preferred_time,
                call_id=call_id,
            )
            logger.info("tool_callback_registered", cb_id=cb_id)
        except Exception as exc:
            logger.error("tool_callback_failed", error=str(exc))
            return "Callback registration failed — we'll try to reach you soon."

        async def _sms():
            try:
                from src.services.sms_service import SMSService
                await SMSService().send_callback_confirmation(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    preferred_time=preferred_time or "soon",
                )
            except Exception as exc:
                logger.warning("tool_callback_sms_failed", error=str(exc))

        asyncio.create_task(_sms())
        return (
            f"Callback registered for {patient_name}. "
            f"We will call you back {preferred_time or 'soon'}. SMS confirmation sent."
        )


    @function_tool
    async def get_doctor_schedule(
        context: RunContext,
        doctor_name: str,
        date: str = "",
    ) -> str:
        """
        Return the consulting schedule for a doctor. Use when caller asks
        'when is Dr. X available?' or 'what days does Dr. X see patients?'
        date is optional (YYYY-MM-DD); omit for the weekly schedule.
        """
        hospital_ctx = _ud(context, "hospital_ctx")
        if not hospital_ctx:
            return "Schedule information temporarily unavailable."

        name_l = doctor_name.lower()
        for doc in hospital_ctx.doctors:
            if name_l not in doc.name.lower():
                continue
            if not doc.slots:
                return f"Dr. {doc.name}'s schedule is not listed. Please contact reception."

            _DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
            lines = []
            for s in doc.slots:
                day = _DAYS[s.dow] if 0 <= s.dow <= 6 else str(s.dow)
                room = f" Room {s.room}" if s.room else ""
                lines.append(f"{day} {s.start}–{s.end}{room}")
            return f"Dr. {doc.name} ({doc.specialty or 'General'}): {', '.join(lines)}."

        return f"No schedule found for '{doctor_name}'. Please check with reception."


    @function_tool
    async def get_department_info(
        context: RunContext,
        department_name: str,
    ) -> str:
        """
        Return location, phone extension, and timing for a hospital department.
        Use when caller asks 'where is cardiology?', 'pharmacy timings?' etc.
        """
        hospital_ctx = _ud(context, "hospital_ctx")
        if not hospital_ctx:
            return "Department information temporarily unavailable."

        dept = hospital_ctx.find_dept(department_name)
        if not dept:
            # Try listing all departments
            names = [d.name for d in hospital_ctx.departments]
            return f"Department '{department_name}' not found. Available: {', '.join(names)}."

        parts = [dept.name]
        if dept.floor:
            parts.append(f"Floor {dept.floor}")
        if dept.location_hint:
            parts.append(dept.location_hint)
        if dept.phone_ext:
            parts.append(f"Ext {dept.phone_ext}")
        return " — ".join(parts) + "."


    @function_tool
    async def send_location_sms(context: RunContext) -> str:
        """
        Send the hospital's address and Google Maps link to the caller's phone.
        Use when the caller asks for directions or location.
        """
        caller_phone = _ud(context, "caller_phone", "")
        hospital_ctx = _ud(context, "hospital_ctx")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        if not caller_phone:
            return "Cannot send SMS — caller phone not available."

        try:
            from src.services.sms_service import SMSService
            address = hospital_ctx.address if hospital_ctx else ""
            await SMSService().send_maps_link(
                phone=caller_phone,
                hospital_name=hospital_name,
                address=address,
            )
            return "Location SMS sent to your phone."
        except Exception as exc:
            logger.warning("tool_location_sms_failed", error=str(exc))
            return "Could not send SMS right now — please note the address."


    @function_tool
    async def alert_emergency(
        context: RunContext,
        emergency_description: str,
    ) -> str:
        """
        IMMEDIATELY alert the emergency department. Call this for: chest pain,
        severe bleeding, loss of consciousness, difficulty breathing, stroke,
        poisoning, or any life-threatening situation. Do NOT ask follow-up
        questions first — alert immediately, then reassure the caller.
        """
        _mark_intent(context, "emergency")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", "")
        hospital_ctx = _ud(context, "hospital_ctx")

        # Mark session for transfer
        try:
            ctx = context.session if hasattr(context, "session") else context
            ud = getattr(ctx, "userdata", {})
            ud["transfer_requested"] = True
            ud["transfer_destination"] = "emergency"
        except Exception:
            pass

        # Fire SMS alert to duty manager
        async def _alert():
            try:
                from src.services.staff_alert import StaffAlertService
                await StaffAlertService().alert_emergency(
                    patient_phone=caller_phone,
                    transcript_snippet=emergency_description[:80],
                    call_id=call_id,
                )
            except Exception as exc:
                logger.warning("tool_emergency_alert_failed", error=str(exc))

        asyncio.create_task(_alert())

        # Provide emergency contact numbers if available
        em_phone = ""
        if hospital_ctx and hospital_ctx.emergency:
            em_phone = hospital_ctx.emergency[0].phone

        logger.warning("emergency_detected", caller=caller_phone[-4:], desc=emergency_description[:50])
        return (
            f"Emergency alert sent to hospital staff. "
            f"{'Emergency number: ' + em_phone + '.' if em_phone else ''} "
            "Please stay on the line."
        )


    @function_tool
    async def transfer_to_department(
        context: RunContext,
        department: str,
        reason: str = "",
    ) -> str:
        """
        Transfer the call to a hospital department or staff member.
        Use for: reception, billing, pharmacy, lab, OPD, specific doctors.
        """
        _mark_intent(context, "transfer")
        hospital_ctx = _ud(context, "hospital_ctx")
        room_name    = _ud(context, "room_name", "")

        # Mark session so post-call cleanup knows a transfer was requested
        try:
            ctx = context.session if hasattr(context, "session") else context
            ud = getattr(ctx, "userdata", {})
            ud["transfer_requested"] = True
            ud["transfer_destination"] = department.lower()
        except Exception:
            pass

        # Resolve a real phone number for the department
        dept_phone = ""
        dept_l = department.lower()
        if hospital_ctx:
            dept = hospital_ctx.find_dept(department)
            if dept and dept.phone_ext:
                ext = dept.phone_ext.strip().replace(" ", "").replace("-", "")
                # Only attempt SIP dial for full phone numbers (10+ digits or E.164)
                if ext.startswith("+") or (ext.isdigit() and len(ext) >= 10):
                    dept_phone = dept.phone_ext.strip()

            # Fall back to emergency contacts for emergency/casualty transfers
            if not dept_phone and ("emergency" in dept_l or "casualty" in dept_l):
                if hospital_ctx.emergency:
                    dept_phone = hospital_ctx.emergency[0].phone or ""

        # Attempt live SIP bridge if we have a number and a room
        if dept_phone and room_name:
            try:
                from src.services.livekit_sip import transfer_call_in_room
                ok = await transfer_call_in_room(
                    room_name=room_name,
                    to_phone=dept_phone,
                    participant_name=department.title(),
                )
                if ok:
                    logger.info("tool_transfer_sip_ok", department=department, dest=dept_phone[-4:])
                    return (
                        f"Connecting you to {department} now. Please hold while we connect."
                    )
            except Exception as exc:
                logger.warning("tool_transfer_sip_failed", error=str(exc))

        # No live bridge happened — don't promise one. Hand the caller a real
        # next step (the department's number when we have one) instead of
        # leaving them on hold with nobody coming.
        logger.warning("tool_transfer_signal_only", department=department)
        ext = ""
        if hospital_ctx:
            dept = hospital_ctx.find_dept(department)
            if dept and dept.phone_ext:
                ext = dept.phone_ext.strip()
        if ext:
            return (
                f"I couldn't connect you to {department} directly. "
                f"You can reach them at {ext} — I've noted your request for them."
            )
        return (
            f"I couldn't connect you to {department} directly. "
            "I've noted your request — they will get back to you. "
            "Is there anything else I can help with?"
        )


    @function_tool
    async def check_availability(
        context: RunContext,
        doctor_name: str,
        date: str,
    ) -> str:
        """
        Check which appointment slots are free for a doctor on a given date,
        BEFORE booking. Use this when the caller asks "is Dr. X free on …?" or
        right before book_appointment to offer real open times.
        date must be YYYY-MM-DD.
        """
        _mark_intent(context, "check_availability")
        hospital_id  = _ud(context, "hospital_id", "")
        hospital_ctx = _ud(context, "hospital_ctx")

        doctor_id, _dept_id, resolved_name = _fuzzy_find_doctor(hospital_ctx, doctor_name)

        # Caller named a department/specialty ("cardiology") rather than a doctor:
        # pick the least-loaded doctor who consults that day instead of failing.
        if not doctor_id and hospital_ctx:
            dept = hospital_ctx.find_dept(doctor_name)
            if dept:
                d = _parse_date(date)
                if d:
                    try:
                        from src.db.queries import get_least_loaded_doctor
                        chosen = await get_least_loaded_doctor(
                            hospital_id, dept.id, d.isoformat()
                        )
                        if chosen:
                            doctor_id = chosen["id"]
                            resolved_name = chosen["name"]
                    except Exception as exc:
                        logger.warning("check_avail_dept_fallback_failed", error=str(exc))

        if not doctor_id:
            return f"Could not find Dr. {doctor_name}. Please confirm the doctor's name."

        slots: list[str] = []
        # Prefer the HIS if configured (live availability), else local schedule.
        try:
            from src.integrations.his.service import get_his_adapter
            his = await get_his_adapter(hospital_id)
            if his:
                slots = await his.get_available_slots(doctor_id, date)
        except Exception as exc:
            logger.warning("his_slots_failed_fallback_to_db", error=str(exc))

        if not slots:
            try:
                from src.db.queries import get_available_slots
                slots = await get_available_slots(doctor_id, date, hospital_id)
            except Exception as exc:
                logger.warning("tool_check_availability_failed", error=str(exc))
                return "Could not check availability right now — please try a specific time."

        if not slots:
            return (
                f"Dr. {resolved_name} has no open slots on {date}. "
                "Would you like another day or another doctor?"
            )
        shown = ", ".join(slots[:6])
        return f"Dr. {resolved_name} is available on {date} at: {shown}. Which time works for you?"


    @function_tool
    async def reschedule_appointment(
        context: RunContext,
        patient_name: str,
        new_date: str,
        new_time: str,
        doctor_name: str = "",
    ) -> str:
        """
        Move an existing appointment to a new date/time. Use when the caller
        wants to change (not cancel) their appointment. Collect the new date
        (YYYY-MM-DD) and time (HH:MM 24-hour); doctor_name helps pick which
        appointment if the caller has more than one.
        """
        _mark_intent(context, "reschedule_appointment")
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", "")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        new_slot = _parse_slot(new_date, new_time)
        if not new_slot:
            return "I didn't catch the new date and time. Could you repeat them?"

        try:
            from src.db.queries import (
                get_appointments_by_phone,
                reschedule_appointment_by_id,
            )
            appts = await get_appointments_by_phone(caller_phone, hospital_id)
            if not appts:
                return "No active appointments found for this number to reschedule."

            target = appts[0]
            if doctor_name:
                for a in appts:
                    if doctor_name.lower() in (a.get("doctor_name") or "").lower():
                        target = a
                        break

            # Reschedule in HIS if this appointment was synced there. The base
            # adapter's default returns False (reschedule unsupported) — treat
            # that the same as an error and flag the row for reconciliation so
            # the HIS and the local DB can't silently diverge.
            his_appt_id = target.get("his_appointment_id")
            if his_appt_id:
                try:
                    from src.integrations.his.service import get_his_adapter
                    his = await get_his_adapter(hospital_id)
                    if his:
                        moved = await his.reschedule_appointment(his_appt_id, new_date, new_time)
                        if moved:
                            logger.info("his_appointment_rescheduled", his_appt_id=his_appt_id)
                        else:
                            raise RuntimeError("HIS reschedule unsupported or failed")
                except Exception as exc:
                    logger.error("his_reschedule_failed", error=str(exc))
                    from src.db.queries import set_his_sync_status
                    await set_his_sync_status(str(target["id"]), "failed")

            ok = await reschedule_appointment_by_id(str(target["id"]), new_slot, hospital_id)
            if not ok:
                return "Could not reschedule that appointment — please contact the front desk."

            doc = target.get("doctor_name") or doctor_name or "the doctor"
        except Exception as exc:
            logger.error("tool_reschedule_failed", error=str(exc))
            return "Unable to reschedule right now — please contact the front desk."

        async def _side_effects():
            try:
                from src.services.staff_alert import StaffAlertService
                from src.services.whatsapp_service import get_messenger
                await get_messenger().send_appointment_confirmation(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    patient_name=patient_name,
                    doctor_name=doc,
                    date=new_date,
                    time=new_time,
                )
                await StaffAlertService().alert_new_booking(
                    patient_name=patient_name,
                    patient_phone=caller_phone,
                    doctor_name=doc,
                    date=new_date,
                    time=new_time,
                    call_id=call_id,
                )
            except Exception as exc:
                logger.warning("tool_reschedule_sms_failed", error=str(exc))

        asyncio.create_task(_side_effects())
        slot_readable = new_slot.strftime("%d %B %Y at %I:%M %p")
        return (
            f"Appointment with Dr. {doc} moved to {slot_readable}. "
            "Confirmation SMS sent."
        )


    @function_tool
    async def confirm_appointment(context: RunContext) -> str:
        """Confirm the EXISTING appointment this outbound call is about, when the
        caller says yes / they will attend. Use this ONLY on an outbound
        confirmation call — do NOT use book_appointment, which would create a
        duplicate. Marks the appointment confirmed and texts the patient."""
        _mark_intent(context, "confirm_appointment")
        appt_id      = _ud(context, "appointment_id", "")
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        if not appt_id:
            # No appointment context — fall back gracefully rather than booking.
            logger.warning("tool_confirm_no_appt_id")
            return "I've noted your confirmation. Thank you."

        try:
            from src.db.queries import confirm_appointment_by_id
            ok = await confirm_appointment_by_id(str(appt_id), hospital_id)
            if not ok:
                logger.warning("tool_confirm_not_found", appt_id=appt_id)
                return "I couldn't find that appointment — please contact the front desk to confirm."
        except Exception as exc:
            logger.error("tool_confirm_failed", error=str(exc))
            return "I've noted your confirmation. Thank you."

        # Fire-and-forget confirmation SMS so the patient has it in writing.
        dname = _ud(context, "doctor_name", "") or _ud(context, "appt_doctor_name", "")
        adate = _ud(context, "appointment_date", "")
        atime = _ud(context, "appointment_time", "")
        if caller_phone:
            async def _confirm_sms():
                try:
                    from src.services.whatsapp_service import get_messenger
                    await get_messenger().send_appointment_confirmation(
                        phone=caller_phone,
                        hospital_name=hospital_name,
                        patient_name=_ud(context, "patient_name", "") or "Patient",
                        doctor_name=dname,
                        date=adate,
                        time=atime,
                    )
                except Exception as exc:
                    logger.warning("tool_confirm_sms_failed", error=str(exc))
            asyncio.create_task(_confirm_sms())

        logger.info("tool_confirm_appointment", appt_id=appt_id)
        return "Your appointment is confirmed. We've sent you a confirmation message. See you then!"


    @function_tool
    async def end_call(context: RunContext, farewell: str = "") -> str:
        """End the call. Call this the moment the caller signals they are done —
        "ok thanks", "that's all", "goodbye", "ശരി നന്ദി", "മതി" — or after you
        have finished helping and they have nothing more. Speak a short warm
        farewell; do NOT ask another question. `farewell` is the goodbye line to
        say in the caller's language."""
        room_name = _ud(context, "room_name", "")
        goodbye = (farewell or "").strip() or "Thank you for calling. Take care, goodbye!"

        # Let the farewell audio play, then drop the room (which hangs up the SIP
        # call). Fixed delay because the tool can't await TTS completion directly;
        # 7s comfortably covers a one-line goodbye.
        async def _hangup() -> None:
            await asyncio.sleep(7.0)
            try:
                from src.services.livekit_sip import delete_room
                await delete_room(room_name)
            except Exception as exc:
                logger.warning("end_call_hangup_failed", error=str(exc))

        if room_name:
            asyncio.create_task(_hangup())
        logger.info("tool_end_call", room=room_name)
        return goodbye

    # Full tool set for hospital tier
    ALL_TOOLS = [
        book_appointment,
        confirm_appointment,
        check_availability,
        reschedule_appointment,
        cancel_appointment,
        request_callback,
        get_doctor_schedule,
        get_department_info,
        send_location_sms,
        alert_emergency,
        transfer_to_department,
        end_call,
    ]

    # Reduced tool set for clinic tier (no transfer, no complex routing)
    CLINIC_TOOLS = [
        book_appointment,
        confirm_appointment,
        check_availability,
        reschedule_appointment,
        cancel_appointment,
        request_callback,
        get_doctor_schedule,
        send_location_sms,
        alert_emergency,
        end_call,
    ]

except ImportError:
    # livekit-agents not installed — tools unavailable (unit-test safe)
    ALL_TOOLS = []
    CLINIC_TOOLS = []
    logger.warning("livekit_not_installed_tools_unavailable")
