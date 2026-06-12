"""
Outbound Call API — endpoints for scheduling reminder calls and checking status.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from src.api.security import plivo_webhook_authentic, rate_limit, require_api_key
from src.config.settings import settings
from src.services.outbound_calls import OutboundCallService

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/outbound",
    tags=["outbound"],
    dependencies=[Depends(require_api_key), Depends(rate_limit(30))],
)

# Separate router for Plivo callbacks — authenticated by Plivo's webhook
# signature (when PLIVO_AUTH_TOKEN is set) rather than the internal API key.
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
async def call_status_callback(request: Request):
    """Plivo hangup callback — called when a call ends.

    Signature-verified when PLIVO_AUTH_TOKEN is configured (fail closed), so
    call-status updates can't be forged by anyone who knows the URL.
    """
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    full_url = f"{settings.PUBLIC_BASE_URL}/api/v1/call/status"
    if not plivo_webhook_authentic(request, full_url, params):
        logger.warning("call_status_signature_rejected")
        return Response(status_code=403)

    call_uuid = params.get("CallUUID", "")
    logger.info(
        "call_status_callback",
        call_uuid=call_uuid[-8:] if call_uuid else "?",
        status=params.get("Status", ""),
        duration=params.get("Duration", "0"),
    )
    return Response(status_code=200)
