"""
backend-additions/routes/qa_api.py
====================================
Quality-assurance endpoints for the Arteq Hospital Voice Agent admin dashboard.

Router prefix : /admin
Tags          : qa
Auth          : Bearer JWT via `require_auth` (see deps.py)

Endpoints
---------
GET /admin/hospitals/{hospital_id}/calls/{call_id}
    Full call detail with parsed transcript + intents.

GET /admin/hospitals/{hospital_id}/feedback?min_rating&max_rating
    Post-call feedback joined to call metadata, newest first.

GET /admin/hospitals/{hospital_id}/missed-questions?language
    Unanswered questions logged by the agent, newest first.

Note on FAQ promotion
---------------------
To promote a missed question to a FAQ, use the existing endpoint:
    POST /admin/hospitals/{hospital_id}/faqs
(defined in dashboard/routes/admin_api.py — no new endpoint is needed here.)
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..deps import AuthDep, PoolDep, require_auth

router = APIRouter(prefix="/admin", tags=["qa"])

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class CallDetail(BaseModel):
    """Full call_logs row with transcript and intents parsed into objects."""

    id: str
    hospital_id: str
    call_id: str
    caller: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    total_turns: Optional[int] = None
    latency_avg_ms: Optional[float] = None
    cost_paise: Optional[int] = None
    # Parsed from JSON text columns
    transcript: Any = Field(
        None,
        description="Call transcript parsed from JSON, or raw string if parsing fails",
    )
    intents: Any = Field(
        None,
        description="Intents array parsed from JSON, or raw string if parsing fails",
    )
    outcome: Optional[str] = None
    created_at: Optional[str] = None


class FeedbackItem(BaseModel):
    """call_feedback row joined with caller/started_at from call_logs."""

    id: str
    call_id: str
    hospital_id: str
    rating: int = Field(..., ge=1, le=5)
    comments: Optional[str] = None
    created_at: Optional[str] = None
    # From joined call_logs
    caller: Optional[str] = None
    started_at: Optional[str] = None


class MissedQuestion(BaseModel):
    """Row from the missed_questions table."""

    id: str
    hospital_id: str
    call_id: Optional[str] = None
    question: str
    language: Optional[str] = None
    context: Optional[str] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_parse_json(value: Optional[str]) -> Any:
    """
    Attempt to parse a JSON string column.
    Returns the parsed object on success, the original string on failure,
    or None if the value is None/empty.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        # asyncpg may already deserialize JSONB columns
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value  # Return raw string rather than dropping the data


def _row_to_str(value: Any) -> Optional[str]:
    """Convert a datetime or other non-string DB value to a string for JSON."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/hospitals/{hospital_id}/calls/{call_id}",
    response_model=CallDetail,
    summary="Full call detail with parsed transcript and intents",
)
async def get_call_detail(
    hospital_id: str,
    call_id: str,
    pool: PoolDep,
    _auth: AuthDep,
) -> CallDetail:
    """
    Return the full call_logs row for a specific call.

    `transcript` and `intents` are stored as TEXT (JSON string) in the DB;
    this endpoint parses them into native objects so the frontend doesn't
    have to double-decode.

    Note: `call_id` here is the unique business identifier (e.g. the Plivo
    Call-UUID), not the internal row `id`.  The hospital_id scoping ensures
    tenants cannot access each other's calls.
    """
    sql = """
        SELECT
            id::text,
            hospital_id::text,
            call_id,
            caller,
            started_at,
            ended_at,
            total_turns,
            latency_avg_ms,
            cost_paise,
            transcript,
            intents,
            outcome,
            created_at
        FROM call_logs
        WHERE hospital_id = $1
          AND call_id = $2
        LIMIT 1
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, hospital_id, call_id)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found for hospital '{hospital_id}'.",
        )

    return CallDetail(
        id=row["id"],
        hospital_id=row["hospital_id"],
        call_id=row["call_id"],
        caller=row["caller"],
        started_at=_row_to_str(row["started_at"]),
        ended_at=_row_to_str(row["ended_at"]),
        total_turns=row["total_turns"],
        latency_avg_ms=(
            float(row["latency_avg_ms"]) if row["latency_avg_ms"] is not None else None
        ),
        cost_paise=row["cost_paise"],
        transcript=_try_parse_json(row["transcript"]),
        intents=_try_parse_json(row["intents"]),
        outcome=row["outcome"],
        created_at=_row_to_str(row["created_at"]),
    )


@router.get(
    "/hospitals/{hospital_id}/feedback",
    response_model=List[FeedbackItem],
    summary="Post-call feedback with optional rating filter",
)
async def list_feedback(
    hospital_id: str,
    pool: PoolDep,
    _auth: AuthDep,
    min_rating: Annotated[
        Optional[int],
        Query(ge=1, le=5, description="Minimum rating (inclusive)"),
    ] = None,
    max_rating: Annotated[
        Optional[int],
        Query(ge=1, le=5, description="Maximum rating (inclusive)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> List[FeedbackItem]:
    """
    Return post-call feedback rows for a hospital, newest first.

    Optionally filter by rating range using `min_rating` and/or `max_rating`.
    Each row is joined to call_logs to include the caller phone number and
    call start time when available.
    """
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="min_rating must be <= max_rating.",
        )

    # Build parameterized WHERE clause for optional rating bounds
    params: list[Any] = [hospital_id]
    conditions = ["cf.hospital_id = $1"]

    if min_rating is not None:
        params.append(min_rating)
        conditions.append(f"cf.rating >= ${len(params)}")
    if max_rating is not None:
        params.append(max_rating)
        conditions.append(f"cf.rating <= ${len(params)}")

    params.append(limit)
    limit_placeholder = f"${len(params)}"

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT
            cf.id::text,
            cf.call_id,
            cf.hospital_id::text,
            cf.rating,
            cf.comments,
            cf.created_at,
            cl.caller,
            cl.started_at
        FROM call_feedback cf
        LEFT JOIN call_logs cl
            ON cl.call_id = cf.call_id
           AND cl.hospital_id = cf.hospital_id
        WHERE {where_clause}
        ORDER BY cf.created_at DESC
        LIMIT {limit_placeholder}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return [
        FeedbackItem(
            id=row["id"],
            call_id=row["call_id"],
            hospital_id=row["hospital_id"],
            rating=row["rating"],
            comments=row["comments"],
            created_at=_row_to_str(row["created_at"]),
            caller=row["caller"],
            started_at=_row_to_str(row["started_at"]),
        )
        for row in rows
    ]


@router.get(
    "/hospitals/{hospital_id}/missed-questions",
    response_model=List[MissedQuestion],
    summary="Questions the agent could not answer, newest first",
)
async def list_missed_questions(
    hospital_id: str,
    pool: PoolDep,
    _auth: AuthDep,
    language: Annotated[
        Optional[str],
        Query(
            description=(
                "Filter by language code (e.g. 'ml', 'hi', 'en'). "
                "Case-insensitive prefix match."
            )
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> List[MissedQuestion]:
    """
    Return questions that the agent logged as unanswerable for a hospital.

    Results are ordered newest-first so operators can triage fresh gaps.
    Optionally filter by `language` code (case-insensitive; partial prefix
    match, so 'ml' matches 'ml', 'malayalam', etc.).

    To promote a question to a FAQ, POST to:
        /admin/hospitals/{hospital_id}/faqs
    (existing endpoint in dashboard/routes/admin_api.py — no new endpoint needed)
    """
    params: list[Any] = [hospital_id]
    conditions = ["hospital_id = $1"]

    if language is not None:
        lang = language.strip().lower()
        if not lang:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="language filter must not be empty.",
            )
        params.append(f"{lang}%")
        conditions.append(f"LOWER(language) LIKE ${len(params)}")

    params.append(limit)
    limit_placeholder = f"${len(params)}"
    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT
            id::text,
            hospital_id::text,
            call_id,
            question,
            language,
            context,
            created_at
        FROM missed_questions
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT {limit_placeholder}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)

    return [
        MissedQuestion(
            id=row["id"],
            hospital_id=row["hospital_id"],
            call_id=row["call_id"],
            question=row["question"],
            language=row["language"],
            context=row["context"],
            created_at=_row_to_str(row["created_at"]),
        )
        for row in rows
    ]
