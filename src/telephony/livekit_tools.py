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
import uuid
from datetime import datetime
from typing import Optional

import pytz
import structlog

logger = structlog.get_logger(__name__)

_IST = pytz.timezone("Asia/Kolkata")

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


def _parse_slot(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD' + 'HH:MM' → IST-aware datetime, or None on failure."""
    try:
        naive = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
        return _IST.localize(naive)
    except Exception:
        return None


def _fuzzy_find_doctor(hospital_ctx, name: str):
    """Case-insensitive partial match on doctor name. Returns (doctor_id, dept_id, full_name)."""
    name_l = name.lower()
    for doc in (hospital_ctx.doctors if hospital_ctx else []):
        if name_l in doc.name.lower() or (doc.name_ml and name_l in doc.name_ml.lower()):
            dept_id = getattr(doc, 'dept_id', None)
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
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", str(uuid.uuid4()))
        hospital_ctx = _ud(context, "hospital_ctx")
        hospital_name = _ud(context, "hospital_name", "the hospital")

        doctor_id, dept_id, resolved_name = _fuzzy_find_doctor(hospital_ctx, doctor_name)
        slot = _parse_slot(appointment_date, appointment_time)

        # Try HIS first (if configured). Failure falls through to local DB only.
        his_appt_id: Optional[str] = None
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
                logger.info("his_appointment_created", his_appt_id=his_appt_id)
        except Exception as exc:
            logger.warning("his_book_failed_fallback_to_db", error=str(exc))

        try:
            from src.db.queries import create_appointment
            appt_id = await create_appointment(
                hospital_id=hospital_id,
                patient_name=patient_name,
                patient_phone=caller_phone,
                doctor_id=doctor_id,
                dept_id=dept_id,
                slot_time=slot,
                notes=notes,
                call_id=call_id,
                his_appointment_id=his_appt_id,
            )
            logger.info("tool_book_appointment", appt_id=appt_id, doctor=resolved_name,
                        his_synced=bool(his_appt_id))
        except Exception as exc:
            logger.error("tool_book_appointment_failed", error=str(exc))
            return "Booking system temporarily unavailable — please call the front desk."

        # Fire-and-forget: SMS + staff alert
        async def _side_effects():
            try:
                from src.services.sms_service import SMSService
                from src.services.staff_alert import StaffAlertService
                sms = SMSService()
                await sms.send_appointment_confirmation(
                    phone=caller_phone,
                    hospital_name=hospital_name,
                    patient_name=patient_name,
                    doctor_name=resolved_name,
                    date=appointment_date,
                    time=appointment_time,
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
        return (
            f"Appointment booked for {patient_name} with Dr. {resolved_name} "
            f"on {slot_readable}. Confirmation SMS sent."
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
        hospital_id  = _ud(context, "hospital_id", "")
        caller_phone = _ud(context, "caller_phone", "")
        call_id      = _ud(context, "call_id", "")
        hospital_ctx = _ud(context, "hospital_ctx")
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

            # Cancel in HIS if appointment was synced there
            his_appt_id = target.get("his_appointment_id")
            if his_appt_id:
                try:
                    from src.integrations.his.service import get_his_adapter
                    his = await get_his_adapter(hospital_id)
                    if his:
                        await his.cancel_appointment(his_appt_id)
                        logger.info("his_appointment_cancelled", his_appt_id=his_appt_id)
                except Exception as exc:
                    logger.warning("his_cancel_failed", error=str(exc))

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

        logger.info("tool_transfer_signal_only", department=department)
        return f"Transferring you to {department}. Please hold."


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
        hospital_id  = _ud(context, "hospital_id", "")
        hospital_ctx = _ud(context, "hospital_ctx")

        doctor_id, _dept_id, resolved_name = _fuzzy_find_doctor(hospital_ctx, doctor_name)
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

            # Reschedule in HIS if this appointment was synced there.
            his_appt_id = target.get("his_appointment_id")
            if his_appt_id:
                try:
                    from src.integrations.his.service import get_his_adapter
                    his = await get_his_adapter(hospital_id)
                    if his:
                        await his.reschedule_appointment(his_appt_id, new_date, new_time)
                        logger.info("his_appointment_rescheduled", his_appt_id=his_appt_id)
                except Exception as exc:
                    logger.warning("his_reschedule_failed", error=str(exc))

            ok = await reschedule_appointment_by_id(str(target["id"]), new_slot, hospital_id)
            if not ok:
                return "Could not reschedule that appointment — please contact the front desk."

            doc = target.get("doctor_name") or doctor_name or "the doctor"
        except Exception as exc:
            logger.error("tool_reschedule_failed", error=str(exc))
            return "Unable to reschedule right now — please contact the front desk."

        async def _side_effects():
            try:
                from src.services.sms_service import SMSService
                from src.services.staff_alert import StaffAlertService
                await SMSService().send_appointment_confirmation(
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


    # Full tool set for hospital tier
    ALL_TOOLS = [
        book_appointment,
        check_availability,
        reschedule_appointment,
        cancel_appointment,
        request_callback,
        get_doctor_schedule,
        get_department_info,
        send_location_sms,
        alert_emergency,
        transfer_to_department,
    ]

    # Reduced tool set for clinic tier (no transfer, no complex routing)
    CLINIC_TOOLS = [
        book_appointment,
        check_availability,
        reschedule_appointment,
        cancel_appointment,
        request_callback,
        get_doctor_schedule,
        send_location_sms,
        alert_emergency,
    ]

except ImportError:
    # livekit-agents not installed — tools unavailable (unit-test safe)
    ALL_TOOLS = []
    CLINIC_TOOLS = []
    logger.warning("livekit_not_installed_tools_unavailable")
