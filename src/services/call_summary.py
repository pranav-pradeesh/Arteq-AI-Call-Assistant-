"""
Call Summary — generates AI summary of completed calls using Groq.
Stored in call_logs table. Optionally prints to console for staff visibility.
"""
from __future__ import annotations

import structlog
from groq import AsyncGroq

from src.config.settings import settings

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are summarizing a hospital reception call. Be concise (3-4 bullet points max). "
    "Include: main enquiry, information provided, any transfers or actions taken, caller language."
)


class CallSummaryService:
    """Generates and stores brief AI summaries of completed calls using Groq."""

    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.GROQ_API_KEY)

    async def generate(
        self,
        conversation: list[dict],
        caller_number: str,
        hospital_name: str,
        outcome: str,
    ) -> str:
        """
        Generate a short summary of the call conversation.

        Args:
            conversation: List of {"role": "caller"|"assistant", "text": "..."} dicts.
            caller_number: Caller's phone number (used only for logging, last 4 digits).
            hospital_name: Name of the hospital for context.
            outcome: Short outcome label (e.g. "transferred", "info_provided", "dropped").

        Returns:
            Bullet-point summary string, or a fallback string on error.
        """
        formatted_lines = [
            f"{turn['role'].capitalize()}: {turn['text']}"
            for turn in conversation
            if turn.get("text")
        ]
        formatted_conversation = "\n".join(formatted_lines) if formatted_lines else "(no transcript)"

        user_content = (
            f"Hospital: {hospital_name}\n"
            f"Outcome: {outcome}\n"
            f"Conversation:\n{formatted_conversation}"
        )

        try:
            response = await self._client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=200,
                temperature=0.1,
            )
            summary = response.choices[0].message.content or ""
            logger.info("call_summary_generated", length=len(summary), caller=caller_number[-4:])
            return summary.strip()
        except Exception as exc:
            logger.error("call_summary_failed", error=str(exc), caller=caller_number[-4:])
            return f"Call completed. Outcome: {outcome}"

    async def notify_staff(
        self,
        summary: str,
        caller_number: str,
        hospital_id: str,
    ) -> None:
        """
        Notify hospital staff of the call summary.

        MVP: logs the summary for staff visibility.
        Future: send to hospital WhatsApp/email.
        """
        logger.info(
            "staff_notification",
            summary=summary[:200],
            caller=caller_number[-4:],
            hospital_id=hospital_id,
        )
