"""
Outbound Call API — endpoints for scheduling reminder calls and checking status.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Form, Response
from pydantic import BaseModel

from src.api.security import rate_limit, require_api_key
from src.config.settings import settings
from src.services.outbound_calls import OutboundCallService

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/outbound",
    tags=["outbound"],
    dependencies=[Depends(require_api_key), Depends(rate_limit(30))],
)

# Separate router for Plivo callbacks (no auth — Plivo posts from its own IPs)
callback_router = APIRouter(tags=["outbound"])

_outbound_service = OutboundCallService()


class AppointmentReminderRequest(BaseModel):
    patient_phone: str
    patient_name: str
    doctor_name: str
    appointment_date: str  # YYYY-MM-DD
    appointment_time: str  # HH:MM
    hospital_id: str = settings.HOSPITAL_ID
    tenant_slug: str = "default"


@router.post("/reminder")
async def schedule_reminder(request: AppointmentReminderRequest):
    """Schedule an outbound appointment reminder call to the patient."""
    success = await _outbound_service.schedule_reminder(
        patient_phone=request.patient_phone,
        patient_name=request.patient_name,
        doctor_name=request.doctor_name,
        appointment_date=request.appointment_date,
        appointment_time=request.appointment_time,
        hospital_id=request.hospital_id,
        tenant_slug=request.tenant_slug,
    )
    if success:
        masked_phone = request.patient_phone[-4:] + "****"
        return {"status": "scheduled", "phone": masked_phone}
    return {"status": "failed", "error": "Could not schedule outbound call"}


@router.get("/health")
async def health_check():
    """Health check for the outbound calls service."""
    return {"status": "ok", "service": "outbound_calls"}


@callback_router.post("/api/v1/call/status")
async def call_status_callback(
    CallUUID: str = Form(default=""),
    RequestUUID: str = Form(default=""),
    Status: str = Form(default=""),
    Duration: str = Form(default="0"),
    From: str = Form(default=""),
    To: str = Form(default=""),
):
    """Plivo hangup callback — called when a call ends."""
    logger.info(
        "call_status_callback",
        call_uuid=CallUUID[-8:] if CallUUID else "?",
        status=Status,
        duration=Duration,
    )
    return Response(status_code=200)
