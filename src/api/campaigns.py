"""
Campaign API — launch outbound health campaigns via Arya.

POST /api/v1/campaigns/launch
  Accepts a list of patient phones + campaign type + message.
  Creates a campaign record, bulk-inserts recipients, then dials each
  patient in a background task (2 s between calls to respect Exotel limits).

Protected by require_api_key (x-api-key header or dev passthrough).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.security import require_api_key
from src.config.settings import settings
from src.db.queries import get_pool
from src.observability.logger import get_logger
from src.services.outbound_calls import OutboundCallService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/campaigns",
    tags=["campaigns"],
    dependencies=[Depends(require_api_key)],
)

_MAX_PHONES_PER_CAMPAIGN = 500
_CALL_INTERVAL_SECONDS = 2.0

_INSERT_CAMPAIGN = """
    INSERT INTO campaigns
        (id, hospital_id, campaign_type, message_template, status, total_recipients)
    VALUES ($1, $2, $3, $4, 'running', $5)
    RETURNING id
"""

_INSERT_RECIPIENTS = """
    INSERT INTO campaign_recipients (id, campaign_id, phone)
    VALUES ($1, $2, $3)
"""

_MARK_RECIPIENT_CALLED = """
    UPDATE campaign_recipients
    SET call_status = 'called', called_at = now()
    WHERE campaign_id = $1 AND phone = $2
"""

_MARK_RECIPIENT_FAILED = """
    UPDATE campaign_recipients
    SET call_status = 'failed'
    WHERE campaign_id = $1 AND phone = $2
"""

_MARK_CAMPAIGN_COMPLETE = """
    UPDATE campaigns SET status = 'completed', updated_at = now() WHERE id = $1
"""

_INCREMENT_PLACED = """
    UPDATE campaigns SET calls_placed = calls_placed + 1, updated_at = now() WHERE id = $1
"""


class CampaignLaunchRequest(BaseModel):
    phones: list[str] = Field(..., min_length=1, description="Patient phone numbers")
    campaign_type: str = Field(..., description="health_camp | vaccination | checkup_reminder | custom")
    message: str = Field(..., max_length=500, description="Message Arya will deliver on the call")
    hospital_id: Optional[str] = None
    tenant_slug: str = "default"
    campaign_id: Optional[str] = None


class CampaignLaunchResponse(BaseModel):
    campaign_id: str
    total_recipients: int
    status: str
    message: str


async def _dial_campaign(
    campaign_id: str,
    phones: list[str],
    campaign_type: str,
    message: str,
    hospital_id: str,
    tenant_slug: str,
) -> None:
    """Background task: dial each recipient, respecting rate limits."""
    service = OutboundCallService()
    try:
        pool = await get_pool()
    except Exception as exc:
        logger.error("campaign_dial_pool_failed", campaign_id=campaign_id, error=str(exc))
        return

    for phone in phones:
        try:
            ok = await service.schedule_campaign_call(
                patient_phone=phone,
                patient_name="",
                campaign_type=campaign_type,
                campaign_message=message,
                hospital_id=hospital_id,
                campaign_id=campaign_id,
                tenant_slug=tenant_slug,
            )
            async with pool.acquire() as conn:
                if ok:
                    await conn.execute(_MARK_RECIPIENT_CALLED, campaign_id, phone)
                    await conn.execute(_INCREMENT_PLACED, campaign_id)
                else:
                    await conn.execute(_MARK_RECIPIENT_FAILED, campaign_id, phone)
        except Exception as exc:
            logger.error("campaign_dial_item_failed",
                         campaign_id=campaign_id, phone=phone[-4:], error=str(exc))

        await asyncio.sleep(_CALL_INTERVAL_SECONDS)

    try:
        async with pool.acquire() as conn:
            await conn.execute(_MARK_CAMPAIGN_COMPLETE, campaign_id)
        logger.info("campaign_completed", campaign_id=campaign_id, phones=len(phones))
    except Exception as exc:
        logger.error("campaign_complete_mark_failed", campaign_id=campaign_id, error=str(exc))


@router.post("/launch", response_model=CampaignLaunchResponse, status_code=status.HTTP_202_ACCEPTED)
async def launch_campaign(
    request: CampaignLaunchRequest,
    background_tasks: BackgroundTasks,
) -> CampaignLaunchResponse:
    """
    Launch a health campaign: creates the campaign record, enqueues outbound calls.

    Returns immediately (202 Accepted) while calls are placed in the background.
    Cap: 500 phones per launch.
    """
    if len(request.phones) > _MAX_PHONES_PER_CAMPAIGN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {_MAX_PHONES_PER_CAMPAIGN} phones per campaign launch",
        )

    hospital_id = request.hospital_id or settings.HOSPITAL_ID
    campaign_id = request.campaign_id or str(uuid.uuid4())

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Reject unknown hospitals up front (404) instead of surfacing the
            # FK violation as a 500 — and so a typo can't dial 500 patients on
            # behalf of a hospital that doesn't exist.
            known = await conn.fetchval(
                "SELECT 1 FROM hospitals WHERE id = $1", hospital_id
            )
            if not known:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unknown hospital_id {hospital_id}",
                )
            await conn.execute(
                _INSERT_CAMPAIGN,
                campaign_id,
                hospital_id,
                request.campaign_type,
                request.message,
                len(request.phones),
            )
            await conn.executemany(
                _INSERT_RECIPIENTS,
                [(str(uuid.uuid4()), campaign_id, phone) for phone in request.phones],
            )
        logger.info("campaign_created", campaign_id=campaign_id,
                    total=len(request.phones), type=request.campaign_type)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("campaign_create_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create campaign record",
        )

    background_tasks.add_task(
        _dial_campaign,
        campaign_id=campaign_id,
        phones=request.phones,
        campaign_type=request.campaign_type,
        message=request.message,
        hospital_id=hospital_id,
        tenant_slug=request.tenant_slug,
    )

    return CampaignLaunchResponse(
        campaign_id=campaign_id,
        total_recipients=len(request.phones),
        status="running",
        message=f"Campaign queued — {len(request.phones)} calls will be placed over ~{len(request.phones) * _CALL_INTERVAL_SECONDS:.0f}s",
    )


@router.get("/{campaign_id}/status")
async def get_campaign_status(campaign_id: str) -> dict:
    """Fetch current status and call counts for a campaign."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, campaign_type, status, total_recipients,
                       calls_placed, calls_answered, created_at, updated_at
                FROM campaigns WHERE id = $1
                """,
                campaign_id,
            )
        if not row:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return dict(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
