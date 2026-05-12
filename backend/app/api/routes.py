import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.config import settings
from app.graph.nodes import query_cache
from app.graph.workflow import run_intent_report, run_refresh_agent
from app.services.report_service import report_service
from app.services.session_store import session_store

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Existing models (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the KRA data")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "Show all active KRAs with employee names and current progress"
            }
        }


class QueryResponse(BaseModel):
    # Intent outcome — lets frontend handle off_topic / clarification_needed gracefully
    status: str = Field("success", description="success | off_topic | clarification_needed | error")
    message: Optional[str] = Field(None, description="Populated for off_topic responses")
    suggestions: Optional[List[str]] = Field(None, description="Example prompts (off_topic only)")
    follow_up_question: Optional[str] = Field(None, description="Clarification question (clarification_needed only)")
    follow_up_options: Optional[List[str]] = Field(None, description="Answer options for follow-up question")

    # SQL result fields
    sql_query: Optional[str] = Field(None, description="Generated MySQL SELECT statement")
    explanation: Optional[str] = Field(None, description="One-sentence description of what the query returns")
    dimensions: List[str] = Field(default_factory=list)
    recommended_column_filters: List[str] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(0)
    execution_time: float = Field(0.0)
    error: Optional[str] = Field(None)
    cache_hit: bool = Field(False)

    # Session ID — use with /report/refresh/{session_id} and /report/stream/{session_id}
    session_id: Optional[str] = Field(None, description="Use this for refresh/stream endpoints")


# ─────────────────────────────────────────────────────────────────────────────
# Intent-aware endpoint models
# ─────────────────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    query: str = Field(..., description="Natural language question about KRA data")
    user_id: str = Field("demo_user", description="User identifier")
    user_role: str = Field(
        "employee",
        description="Role of the requesting user: employee | lead | manager | hr",
    )

    class Config:
        json_schema_extra = {
            "example": {"query": "show my goals", "user_id": "user_42", "user_role": "employee"}
        }


class ClarifyRequest(BaseModel):
    session_id: str = Field(..., description="Session ID returned by /report/generate")
    user_answer: str = Field(..., description="User's answer to the follow-up question")
    user_id: str = Field("demo_user")
    user_role: str = Field("employee")

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "sess_abc123",
                "user_answer": "Q1 2025",
                "user_id": "user_42",
                "user_role": "employee",
            }
        }


class ReportResponse(BaseModel):
    status: str = Field(..., description="off_topic | clarification_needed | success | error")

    # Track A
    message: Optional[str] = None
    suggestions: Optional[List[str]] = None

    # Track B
    follow_up_question: Optional[str] = None
    follow_up_options: Optional[List[str]] = None
    clarification_round: Optional[int] = None
    original_prompt: Optional[str] = None

    # Track C / success
    enriched_prompt: Optional[str] = None
    sql_query: Optional[str] = None
    explanation: Optional[str] = None
    dimensions: Optional[List[str]] = None
    recommended_column_filters: Optional[List[str]] = None
    data: Optional[List[Dict[str, Any]]] = None
    row_count: Optional[int] = None
    execution_time: Optional[float] = None
    error: Optional[str] = None
    cache_hit: Optional[bool] = None

    # Refresh metadata
    refresh_mode: Optional[bool] = None
    refreshed_at: Optional[str] = None

    # Error codes (SESSION_EXPIRED | ACCESS_DENIED | SCHEMA_CHANGED)
    error_code: Optional[str] = None

    # Always present
    session_id: str = Field(...)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_report_response(
    status: str,
    result: Dict[str, Any],
    session_id: str,
    original_prompt: str,
) -> ReportResponse:
    if status == "off_topic":
        return ReportResponse(
            status="off_topic",
            message=result.get("message", ""),
            suggestions=result.get("suggestions", []),
            session_id=session_id,
        )
    if status == "clarification_needed":
        return ReportResponse(
            status="clarification_needed",
            follow_up_question=result.get("follow_up_question", ""),
            follow_up_options=result.get("follow_up_options", []),
            clarification_round=result.get("clarification_round", 0),
            original_prompt=result.get("original_prompt", original_prompt),
            session_id=session_id,
        )
    # success (may carry an error field if SQL execution failed)
    return ReportResponse(
        status="success",
        enriched_prompt=result.get("enriched_prompt", ""),
        sql_query=result.get("sql_query"),
        explanation=result.get("explanation"),
        dimensions=result.get("dimensions", []),
        recommended_column_filters=result.get("recommended_column_filters", []),
        data=result.get("data", []),
        row_count=result.get("row_count", 0),
        execution_time=result.get("execution_time", 0.0),
        error=result.get("error"),
        cache_hit=result.get("cache_hit", False),
        session_id=session_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Existing endpoint (unchanged behaviour)
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Natural Language → SQL → Result",
    description=(
        "Submit a plain-English question. The AI agent:\n\n"
        "1. Converts it to a MySQL SELECT via **GPT-4o-mini**\n"
        "2. Validates the SQL (safety + LIMIT enforcement)\n"
        "3. Executes it against the **vthink_kra** database\n"
        "4. Returns results with `dimensions` and `recommended_column_filters`"
    ),
)
async def run_query(request: QueryRequest):
    try:
        # Create a session so validated SQL is cached in Redis,
        # enabling /report/refresh and /report/stream for this query.
        session_id = session_store.create({
            "original_prompt": request.query,
            "user_id": "demo_user",
            "user_role": "employee",
            "clarification_round": 0,
            "prior_followup": "",
        })

        result = await report_service.generate(
            user_id="demo_user",
            query=request.query,
            debug=False,
            session_id=session_id,
        )

        status = result.get("status", "success")
        return QueryResponse(
            status=status,
            message=result.get("message"),
            suggestions=result.get("suggestions"),
            follow_up_question=result.get("follow_up_question"),
            follow_up_options=result.get("follow_up_options"),
            sql_query=result.get("sql_query"),
            explanation=result.get("explanation"),
            dimensions=result.get("dimensions", []),
            recommended_column_filters=result.get("recommended_column_filters", []),
            data=result.get("data", []),
            row_count=result.get("row_count", 0),
            execution_time=result.get("execution_time", 0.0),
            error=result.get("error"),
            cache_hit=result.get("cache_hit", False),
            session_id=session_id,
        )
    except Exception as exc:
        logger.exception(f"query error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Intent-aware endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/report/generate",
    response_model=ReportResponse,
    summary="Intent-Aware Report Generation",
    description=(
        "Submit a natural language query. Returns one of:\n\n"
        "- **off_topic** — not a KRA question\n"
        "- **clarification_needed** — missing info; one follow-up question returned\n"
        "- **success** — SQL executed, data returned\n\n"
        "The returned `session_id` enables `/report/clarify`, "
        "`/report/refresh/{session_id}`, and `/report/stream/{session_id}`."
    ),
)
async def generate_report(request: GenerateRequest) -> ReportResponse:
    try:
        session_data = {
            "original_prompt": request.query,
            "user_id": request.user_id,
            "user_role": request.user_role,
            "clarification_round": 0,
            "prior_followup": "",
        }
        session_id = session_store.create(session_data)

        result = await run_intent_report(
            user_id=request.user_id,
            query=request.query,
            user_role=request.user_role,
            clarification_round=0,
            prior_followup="",
            session_id=session_id,
        )

        status = result.get("status", "success")

        if status == "clarification_needed":
            session_store.update(session_id, {
                "prior_followup": result.get("follow_up_question", ""),
                "clarification_round": 0,
            })

        logger.info(f"[generate] user={request.user_id} status={status} session={session_id}")
        return _build_report_response(status, result, session_id, request.query)

    except Exception as exc:
        logger.exception(f"[generate] error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/report/clarify",
    response_model=ReportResponse,
    summary="Submit Answer to Follow-Up Clarification",
    description=(
        "Called after `/report/generate` returns `clarification_needed`. "
        "Merges the user's answer with the original prompt and re-evaluates. "
        "Maximum 2 clarification rounds — round 2 always proceeds to SQL generation."
    ),
)
async def clarify_report(request: ClarifyRequest) -> ReportResponse:
    try:
        session = session_store.get(request.session_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found or expired. Start a new request via /report/generate.",
            )

        original_prompt = session.get("original_prompt", "")
        prior_round = session.get("clarification_round", 0)
        prior_followup = session.get("prior_followup", "")
        new_round = prior_round + 1

        merged_query = f"{original_prompt} — {request.user_answer}"

        result = await run_intent_report(
            user_id=request.user_id,
            query=merged_query,
            user_role=request.user_role,
            clarification_round=new_round,
            prior_followup=prior_followup,
            session_id=request.session_id,
        )

        status = result.get("status", "success")

        if status == "clarification_needed":
            session_store.update(request.session_id, {
                "original_prompt": merged_query,
                "prior_followup": result.get("follow_up_question", ""),
                "clarification_round": new_round,
            })

        logger.info(
            f"[clarify] user={request.user_id} session={request.session_id} "
            f"round={new_round} status={status}"
        )
        return _build_report_response(status, result, request.session_id, merged_query)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[clarify] error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# HTTP polling refresh (single cycle)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/report/refresh/{session_id}",
    response_model=ReportResponse,
    summary="Single-Cycle Report Refresh (HTTP Polling)",
    description=(
        "Executes the cached SQL directly — no LLM, no validator, no RBAC re-evaluation.\n\n"
        "**Error codes returned as HTTP status:**\n"
        "- `404 SESSION_EXPIRED` — cache missing or TTL expired\n"
        "- `403 ACCESS_DENIED` — user_id does not match session owner\n"
        "- `409 SCHEMA_CHANGED` — schema drift detected; stale cache deleted"
    ),
)
async def refresh_report(
    session_id: str,
    user_id: str = Query(..., description="Must match the user_id from /report/generate"),
    user_role: str = Query("employee"),
) -> ReportResponse:
    try:
        result = await run_refresh_agent(session_id=session_id, user_id=user_id)
        error_code = result.get("error_code")

        if error_code == "SESSION_EXPIRED":
            raise HTTPException(status_code=404, detail="SESSION_EXPIRED: Please regenerate the report.")
        if error_code == "ACCESS_DENIED":
            raise HTTPException(status_code=403, detail="ACCESS_DENIED: User does not match session owner.")
        if error_code == "SCHEMA_CHANGED":
            raise HTTPException(status_code=409, detail="SCHEMA_CHANGED: Schema has changed. Please regenerate.")
        if error_code:
            raise HTTPException(status_code=500, detail=result.get("message", error_code))

        return ReportResponse(
            status=result.get("status", "success"),
            explanation=result.get("explanation"),
            dimensions=result.get("dimensions", []),
            recommended_column_filters=result.get("recommended_column_filters", []),
            data=result.get("data", []),
            row_count=result.get("row_count", 0),
            execution_time=result.get("execution_time", 0.0),
            error=result.get("error"),
            refresh_mode=True,
            refreshed_at=result.get("refreshed_at"),
            session_id=session_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[refresh] error session={session_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket streaming (continuous auto-refresh)
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/report/stream/{session_id}")
async def stream_report(
    websocket: WebSocket,
    session_id: str,
    user_id: str = Query(...),
    user_role: str = Query("employee"),
    interval: Optional[int] = Query(None, description="Override refresh interval in seconds"),
):
    """
    Streams live refreshed report data over WebSocket.

    - Executes an immediate refresh on connect, then loops every N seconds.
    - Sends the same ReportResponse JSON structure as /report/refresh.
    - Closes cleanly on WebSocketDisconnect, SESSION_EXPIRED, SCHEMA_CHANGED,
      or ACCESS_DENIED — never leaves a dangling async loop.
    """
    await websocket.accept()
    refresh_interval = interval if interval is not None else settings.STREAM_REFRESH_INTERVAL_SECONDS
    logger.info(f"[stream] connected session={session_id[:16]} user={user_id} interval={refresh_interval}s")

    try:
        while True:
            result = await run_refresh_agent(session_id=session_id, user_id=user_id)
            await websocket.send_json(result)

            error_code = result.get("error_code")
            if error_code in ("SESSION_EXPIRED", "SCHEMA_CHANGED", "ACCESS_DENIED"):
                logger.info(f"[stream] terminating session={session_id[:16]} reason={error_code}")
                await websocket.close(code=1008)  # 1008 = Policy Violation
                return

            await asyncio.sleep(refresh_interval)

    except WebSocketDisconnect:
        logger.info(f"[stream] client disconnected session={session_id[:16]} user={user_id}")
    except Exception as exc:
        logger.exception(f"[stream] unexpected error session={session_id[:16]}: {exc}")
        try:
            await websocket.close(code=1011)  # 1011 = Internal Error
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Cache management endpoints (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/cache/stats", summary="Cache statistics")
async def get_cache_stats():
    return query_cache.stats()


@router.delete("/cache", summary="Clear all cached query results")
async def clear_cache():
    cleared = query_cache.clear()
    return {"cleared": cleared, "message": f"Removed {cleared} cached entries"}
