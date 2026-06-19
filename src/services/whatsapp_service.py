"""
WhatsApp / messaging channel.

Vobiz is a SIP trunk provider and does not support WhatsApp or SMS.
get_messenger() returns a plain SMSService (currently a no-op) so
callers don't need to change when a carrier is added later.
"""
from __future__ import annotations

from src.services.sms_service import SMSService


def get_messenger() -> SMSService:
    """Return the configured patient-notification channel."""
    return SMSService()
