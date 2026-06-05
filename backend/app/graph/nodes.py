import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.exc import OperationalError

from app.agents.intent_agent import intent_agent
from app.agents.llm_agent import llm_agent
from app.agents.prompt_builder import prompt_builder, extract_column_hint
from app.agents.sql_agent import sql_refinement_agent
from app.cache.query_cache import QueryCache
from app.cache.redis_cache import sql_cache
from app.config import settings
from app.db.connection import db_manager
from app.db.schema_manager import schema_manager
from app.graph.state import AgentState
from app.memory.conversation_memory import memory_manager
from app.services.filter_recommender import filter_recommender
from app.validators.sql_validator import ValidationStatus, sql_validator

query_cache = QueryCache(
    ttl_seconds=settings.CACHE_TTL_SECONDS,
    max_size=settings.CACHE_MAX_SIZE,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _steps(state: AgentState) -> list:
    return list(state.get("steps", []))


# ─────────────────────────────────────────────
# Node: Cache Lookup (runs after load_context)
# ─────────────────────────────────────────────

def cache_lookup_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    key = query_cache.make_key(state["user_id"], state["user_query"])
    cached = query_cache.get(key)

    if cached is not None:
        result = dict(cached)
        result["cache_hit"] = True
        step = {
            "node": "cache_lookup",
            "status": "hit",
            "cache_key": key[:8],
            "duration_ms": round((time.time() - t0) * 1000, 1),
        }
        logger.info(f"[cache_lookup] HIT user={state['user_id']} key={key[:8]}")
        return {
            "cache_hit": True,
            "cache_key": key,
            "formatted_result": result,
            "steps": _steps(state) + [step],
        }

    step = {
        "node": "cache_lookup",
        "status": "miss",
        "cache_key": key[:8],
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[cache_lookup] MISS user={state['user_id']} key={key[:8]}")
    return {
        "cache_hit": False,
        "cache_key": key,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node: Cache Store (runs after result_formatter)
# ─────────────────────────────────────────────

def cache_store_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    key = state.get("cache_key", "")
    formatted = state.get("formatted_result", {})

    stored = False
    if key and not formatted.get("error"):
        query_cache.set(key, formatted)
        stored = True

    step = {
        "node": "cache_store",
        "status": "stored" if stored else "skipped",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[cache_store] user={state['user_id']} stored={stored}")
    return {"steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 1 — Load User Context
# ─────────────────────────────────────────────

def load_context_node(state: AgentState) -> Dict[str, Any]:
    user_id = state["user_id"]
    t0 = time.time()

    schema_str = schema_manager.get_schema_string()
    memory_ctx = memory_manager.get_context_string(user_id)

    step = {
        "node": "load_context",
        "status": "success",
        "schema_tables": len(schema_manager.get_schema()),
        "has_memory": bool(memory_ctx),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[load_context] user={user_id} tables={step['schema_tables']}")
    return {
        "schema": schema_str,
        "memory_context": memory_ctx,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node 2 — Prompt Builder
# ─────────────────────────────────────────────

def prompt_builder_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    effective_query = state.get("enriched_prompt") or state["user_query"]

    # Use the rich, description-aware relevant schema from the JSON registry.
    # Falls back to the full live-DB schema string when the registry is not loaded.
    from app.db.schema_registry import schema_registry
    if schema_registry.is_loaded():
        schema_str = schema_registry.get_relevant_schema_string(effective_query)
        retrieved_tables = schema_registry.get_relevant_tables(effective_query)
    else:
        schema_str = state.get("schema", "")
        retrieved_tables = []

    built_prompt = prompt_builder.build_prompt(
        user_query=effective_query,
        user_id=state["user_id"],
        schema_string=schema_str,
        memory_context=state.get("memory_context", ""),
        retry_feedback=state.get("retry_feedback", ""),
    )
    step = {
        "node": "prompt_builder",
        "status": "success",
        "prompt_chars": len(built_prompt),
        "retrieved_tables": retrieved_tables,
        "is_retry": bool(state.get("retry_feedback")),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(
        "[prompt_builder] user=%s tables=%s chars=%d retry=%s",
        state["user_id"], retrieved_tables, len(built_prompt), step["is_retry"],
    )
    return {"prompt": built_prompt, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 3 — LLM
# ─────────────────────────────────────────────

def llm_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    response = llm_agent.generate_sql(state["prompt"])
    duration = round((time.time() - t0) * 1000, 1)

    has_error = "error" in response and response["error"]
    step = {
        "node": "llm",
        "status": "error" if has_error else "success",
        "sql_generated": bool(response.get("sql_query")),
        "duration_ms": duration,
    }
    if has_error:
        step["error"] = response["error"]

    logger.info(f"[llm] user={state['user_id']} sql_ok={step['sql_generated']} ms={duration}")
    return {"llm_response": response, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 4 — SQL Agent (Refine)
# ─────────────────────────────────────────────

def sql_agent_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    raw_sql = state.get("llm_response", {}).get("sql_query", "")
    refined = sql_refinement_agent.refine(raw_sql)

    step = {
        "node": "sql_agent",
        "status": "success",
        "raw_length": len(raw_sql),
        "refined_length": len(refined),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[sql_agent] user={state['user_id']} refined={refined[:100]}")
    return {"refined_sql": refined, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 5 — SQL Validator
# ─────────────────────────────────────────────

def sql_validator_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    sql = state.get("refined_sql", "")
    user_id = state["user_id"]

    status, message, validated_sql = sql_validator.validate(
        sql, user_id, user_query=state.get("user_query", "")
    )

    step = {
        "node": "sql_validator",
        "status": status.value,
        "message": message,
        "sql_snippet": validated_sql[:120],
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[sql_validator] user={user_id} status={status.value} msg={message}")
    return {
        "refined_sql": validated_sql,
        "validation_status": status.value,
        "validation_message": message,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node 6a — Increment Retry (Validation failure)
# ─────────────────────────────────────────────

def increment_retry_validation_node(state: AgentState) -> Dict[str, Any]:
    new_count = state.get("retry_count", 0) + 1
    msg = state.get('validation_message', '')
    hint = extract_column_hint(msg)
    feedback = (
        f"SQL validation failed: {msg}{hint}\n"
        f"Rejected SQL was:\n{state.get('refined_sql', '')}"
    )
    logger.warning(f"[retry_validation] user={state['user_id']} attempt={new_count} reason={msg}")
    return {"retry_count": new_count, "retry_feedback": feedback}


# ─────────────────────────────────────────────
# Node 6b — Increment Retry (Execution failure)
# ─────────────────────────────────────────────

def increment_retry_execution_node(state: AgentState) -> Dict[str, Any]:
    new_count = state.get("retry_count", 0) + 1
    exec_error = state.get("execution_result", {}).get("error", "Unknown error")
    hint = extract_column_hint(str(exec_error))
    feedback = (
        f"SQL execution failed with: {exec_error}{hint}\n"
        f"Failed SQL was:\n{state.get('refined_sql', '')}"
    )
    logger.warning(f"[retry_execution] user={state['user_id']} attempt={new_count} error={exec_error}")
    return {"retry_count": new_count, "retry_feedback": feedback}


# ─────────────────────────────────────────────
# Node 7 — Execution Engine
# ─────────────────────────────────────────────

def execution_engine_node(state: AgentState) -> Dict[str, Any]:
    sql       = state.get("refined_sql", "")
    page      = state.get("page", 1)
    page_size = state.get("page_size", settings.PAGE_SIZE)
    # Bind :employee_id from the session token so the LLM never needs to
    # hardcode user IDs — the placeholder is resolved safely by the DB driver.
    bound_params = {"employee_id": state.get("user_id", "")}
    t0 = time.time()
    try:
        rows, columns, total_rows, total_pages = db_manager.execute_paginated(
            sql, page=page, page_size=page_size, params=bound_params
        )
        duration = round(time.time() - t0, 4)
        step = {
            "node": "execution_engine",
            "status": "success",
            "row_count": len(rows),
            "total_rows": total_rows,
            "page": page,
            "total_pages": total_pages,
            "duration_ms": round(duration * 1000, 1),
        }
        logger.info(
            f"[execution_engine] user={state['user_id']} rows={len(rows)} "
            f"total={total_rows} page={page}/{total_pages} ms={step['duration_ms']}"
        )
        return {
            "execution_result": {"rows": rows, "columns": columns, "error": None},
            "execution_time": duration,
            "total_rows":  total_rows,
            "total_pages": total_pages,
            "has_next_page": page < total_pages,
            "has_prev_page": page > 1,
            "steps": _steps(state) + [step],
        }
    except Exception as e:
        duration = round(time.time() - t0, 4)
        logger.error(f"[execution_engine] user={state['user_id']} ERROR: {e}")
        step = {
            "node": "execution_engine",
            "status": "error",
            "error": str(e),
            "duration_ms": round(duration * 1000, 1),
        }
        return {
            "execution_result": {"rows": [], "columns": [], "error": str(e)},
            "execution_time": duration,
            "total_rows": 0,
            "total_pages": 1,
            "has_next_page": False,
            "has_prev_page": False,
            "steps": _steps(state) + [step],
        }


# ─────────────────────────────────────────────
# Employee display post-processor
# ─────────────────────────────────────────────

_FIRST_NAME_COLS = {"firstname", "first_name", "fname"}
_LAST_NAME_COLS  = {"lastname",  "last_name",  "lname"}
_EMP_ID_COLS     = {"employee_id", "emp_id", "employeeid", "emp_code"}


def _apply_employee_format(rows: list, columns: list):
    """Merge raw first/last name + employee_id columns into a single 'employee'
    column formatted as 'First Last (EMP001)'.  No-ops when the columns are
    absent (i.e. the LLM already applied CONCAT in the SQL)."""
    first_col = next((c for c in columns if c.lower() in _FIRST_NAME_COLS), None)
    last_col  = next((c for c in columns if c.lower() in _LAST_NAME_COLS),  None)

    if not (first_col and last_col):
        return rows, columns

    id_col = next((c for c in columns if c.lower() in _EMP_ID_COLS), None)
    drop   = {c for c in [first_col, last_col, id_col] if c}

    new_columns: list = []
    inserted = False
    for col in columns:
        if col == first_col and not inserted:
            new_columns.append("employee")
            inserted = True
        if col not in drop:
            new_columns.append(col)
    if not inserted:
        new_columns.insert(0, "employee")

    def _build(row: dict) -> dict:
        first = str(row.get(first_col) or "").strip()
        last  = str(row.get(last_col)  or "").strip()
        name  = f"{first} {last}".strip()
        if id_col and row.get(id_col):
            name = f"{name} ({row[id_col]})"
        new_row = {k: v for k, v in row.items() if k not in drop}
        new_row["employee"] = name
        return new_row

    return [_build(r) for r in rows], new_columns


# ─────────────────────────────────────────────
# Node 8 — Result Formatter
# ─────────────────────────────────────────────

_METRIC_KW = frozenset({
    "count", "sum", "total", "avg", "average", "max", "min",
    "amount", "revenue", "profit", "cost", "rate", "percentage",
    "score", "salary", "budget",
})


def result_formatter_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    exec_result = state.get("execution_result", {})
    llm_resp    = state.get("llm_response", {})

    rows: list    = exec_result.get("rows", [])
    columns: list = exec_result.get("columns", [])

    rows, columns = _apply_employee_format(rows, columns)

    # Columns without aggregate keywords → dimensions
    dimensions = [col for col in columns if not any(kw in col.lower() for kw in _METRIC_KW)]

    filterable_cols = filter_recommender.filterable_columns(rows=rows, columns=columns)
    # Keep the legacy flat list for any consumers that still reference it
    recommended_column_filters = [fc["column"] for fc in filterable_cols]

    page        = state.get("page", 1)
    page_size   = state.get("page_size", settings.PAGE_SIZE)
    total_rows  = state.get("total_rows", len(rows))
    total_pages = state.get("total_pages", 1)

    formatted = {
        "sql_query": state.get("refined_sql", ""),
        "explanation": llm_resp.get("explanation", ""),
        "dimensions": dimensions,
        "recommended_column_filters": recommended_column_filters,
        "filterable_columns": filterable_cols,
        "filter_instruction": "client_side_only",
        "llm_call_on_filter": False,
        "data": rows,
        "row_count": len(rows),
        "execution_time": state.get("execution_time", 0.0),
        # Pagination metadata
        "page":          page,
        "page_size":     page_size,
        "total_rows":    total_rows,
        "total_pages":   total_pages,
        "has_next_page": state.get("has_next_page", False),
        "has_prev_page": state.get("has_prev_page", False),
    }

    step = {
        "node": "result_formatter",
        "status": "success",
        "row_count": len(rows),
        "total_rows": total_rows,
        "page": page,
        "total_pages": total_pages,
        "filter_columns": len(filterable_cols),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(
        f"[result_formatter] user={state['user_id']} rows={len(rows)} "
        f"total={total_rows} page={page}/{total_pages} filter_cols={len(filterable_cols)}"
    )
    return {"formatted_result": formatted, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 9 — Memory Store
# ─────────────────────────────────────────────

def memory_store_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    user_id = state["user_id"]
    formatted = state.get("formatted_result", {})
    sql = state.get("refined_sql", "")

    row_count = formatted.get("row_count", 0)
    explanation = formatted.get("explanation", "Query completed.")
    cache_note = " [cache hit]" if state.get("cache_hit") else ""
    assistant_msg = f"{explanation} (Returned {row_count} rows{cache_note})"

    memory_manager.add_interaction(
        user_id=user_id,
        user_message=state["user_query"],
        assistant_message=assistant_msg,
        sql_query=sql or formatted.get("sql_query", ""),
    )

    step = {
        "node": "memory_store",
        "status": "success",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[memory_store] user={user_id} stored interaction")
    return {"steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 10 — Error Handler
# ─────────────────────────────────────────────

def error_handler_node(state: AgentState) -> Dict[str, Any]:
    message = state.get("validation_message", "An unknown error occurred")
    retry_count = state.get("retry_count", 0)

    if state.get("execution_result", {}).get("error"):
        message = state["execution_result"]["error"]

    suggestion = _suggest(message)

    formatted_error = {
        "sql_query": state.get("refined_sql", ""),
        "explanation": "",
        "dimensions": [],
        "recommended_column_filters": [],
        "data": [],
        "row_count": 0,
        "execution_time": state.get("execution_time", 0.0),
        "error": f"{message} — {suggestion}",
    }

    step = {
        "node": "error_handler",
        "status": "error",
        "message": message,
        "retries_exhausted": retry_count >= settings.MAX_RETRY_COUNT,
    }
    logger.error(f"[error_handler] user={state['user_id']} error={message}")
    return {"formatted_result": formatted_error, "steps": _steps(state) + [step]}


def _suggest(error_message: str) -> str:
    msg = error_message.lower()
    if "unsafe" in msg or "forbidden" in msg:
        return "Only SELECT queries are allowed. Please rephrase your request."
    if "user_id" in msg or "rbac" in msg:
        return "Access control prevented this query. Contact support if this is unexpected."
    if "cartesian" in msg:
        return "Try a more specific query with explicit table relationships."
    if "table" in msg and "doesn't exist" in msg:
        return "The referenced table may not exist. Try refreshing the schema via POST /api/v1/refresh-schema."
    if "timeout" in msg:
        return "Query timed out. Try adding more specific filters to reduce result size."
    return "Please rephrase your query or contact support if the issue persists."


# ─────────────────────────────────────────────
# Node: Intent Detector (NEW — runs after cache miss)
# ─────────────────────────────────────────────

def intent_detector_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    user_query = state.get("user_query", "")
    user_role = state.get("user_role", "employee")

    result = intent_agent.classify(
        user_message=user_query,
        user_role=user_role,
        schema_summary=state.get("schema", ""),
        memory_context=state.get("memory_context", ""),
    )

    duration = round((time.time() - t0) * 1000, 1)
    step = {
        "node": "intent_detector",
        "status": "success",
        "track": result["track"],
        "confidence": result["confidence"],
        "duration_ms": duration,
    }
    logger.info(
        f"[intent_detector] user={state['user_id']} track={result['track']} "
        f"confidence={result['confidence']:.2f}"
    )
    return {
        "intent_track": result["track"],
        "intent_confidence": result["confidence"],
        "intent_reasoning": result.get("reasoning", ""),
        "greeting_message": result.get("greeting_message") or "",
        "off_topic_reason": result.get("off_topic_reason") or "",
        "off_topic_message": result.get("polite_block_message") or "",
        "enriched_prompt": result.get("enriched_prompt") or "",
        "extracted_filters": result.get("extracted_filters") or {},
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node: Greeting Handler (Track A — greeting)
# ─────────────────────────────────────────────

def greeting_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    message = state.get("greeting_message", "")
    formatted = {
        "status": "greeting",
        "message": message,
        "suggestions": [],
    }
    step = {
        "node": "greeting",
        "status": "success",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[greeting] user={state['user_id']}")
    return {"formatted_result": formatted, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node: Off-Topic Handler (Track B — off_topic)
# ─────────────────────────────────────────────

def off_topic_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    message = state.get("off_topic_message", "")
    formatted = {
        "status": "off_topic",
        "message": message,
        "suggestions": [],
    }
    step = {
        "node": "off_topic",
        "status": "success",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[off_topic] user={state['user_id']} reason={state.get('off_topic_reason')}")
    return {"formatted_result": formatted, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node: SQL Test Execution (LIMIT 0 dry-run)
# Runs between sql_validator and cache_write.
# Catches schema/syntax errors before any rows
# are fetched and feeds the DB error back to the
# LLM for targeted self-correction.
# ─────────────────────────────────────────────

def sql_test_node(state: AgentState) -> Dict[str, Any]:
    sql     = state.get("refined_sql", "")
    user_id = state.get("user_id", "")
    bound   = {"employee_id": user_id}
    t0      = time.time()

    passed, error = db_manager.test_execute(sql, bound)
    duration_ms   = round((time.time() - t0) * 1000, 1)

    step = {
        "node":        "sql_test",
        "status":      "passed" if passed else "failed",
        "duration_ms": duration_ms,
    }
    if not passed:
        step["error"] = error

    if passed:
        logger.info(f"[sql_test] PASSED user={user_id} ms={duration_ms}")
        return {
            "test_execution_passed": True,
            "test_execution_error":  "",
            "steps": _steps(state) + [step],
        }

    logger.warning(f"[sql_test] FAILED user={user_id}: {error}")
    return {
        "test_execution_passed": False,
        "test_execution_error":  error,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node: Increment Retry — Test Failure
# Counts the failed attempt and builds targeted
# feedback so the LLM can fix the offending SQL.
# ─────────────────────────────────────────────

def increment_retry_test_node(state: AgentState) -> Dict[str, Any]:
    new_count = state.get("retry_count", 0) + 1
    error     = state.get("test_execution_error", "Unknown test error")
    hint      = extract_column_hint(str(error))
    feedback  = (
        f"SQL dry-run (LIMIT 0) failed with error:\n{error}{hint}\n\n"
        f"Fix using EXACT table and column names from the DATABASE SCHEMA above.\n"
        f"Failed SQL:\n{state.get('refined_sql', '')}"
    )
    logger.warning(
        f"[retry_test] user={state['user_id']} attempt={new_count} error={error[:120]}"
    )
    return {"retry_count": new_count, "retry_feedback": feedback}


# ─────────────────────────────────────────────
# Conditional Edge Functions
# ─────────────────────────────────────────────

def route_after_validation(state: AgentState) -> str:
    status = state.get("validation_status", "")
    retry_count = state.get("retry_count", 0)

    if status == ValidationStatus.VALID.value:
        return "execute"
    if status == ValidationStatus.UNSAFE.value:
        return "stop"
    # INVALID — retry if budget remains
    if retry_count < settings.MAX_RETRY_COUNT:
        return "retry"
    return "stop"


def route_after_sql_test(state: AgentState) -> str:
    if state.get("test_execution_passed"):
        return "cache"
    if state.get("retry_count", 0) < settings.MAX_RETRY_COUNT:
        return "retry"
    return "stop"


def route_after_execution(state: AgentState) -> str:
    exec_error = state.get("execution_result", {}).get("error")
    retry_count = state.get("retry_count", 0)

    if not exec_error:
        return "format"
    if retry_count < settings.MAX_RETRY_COUNT:
        return "retry"
    return "stop"


def route_after_intent_detection(state: AgentState) -> str:
    track = state.get("intent_track", "clear")
    if track == "greeting":
        return "greeting"
    if track == "off_topic":
        return "off_topic"
    return "prompt_builder"


# ─────────────────────────────────────────────
# Node: Cache Write (NEW — after sql_validator, before execution_engine)
# ─────────────────────────────────────────────

def cache_write_node(state: AgentState) -> Dict[str, Any]:
    """
    Persists the post-validation, RBAC-secured SQL to Redis so that
    subsequent refresh requests can execute it directly, bypassing the
    entire LLM pipeline.  Failure is intentionally non-blocking.
    """
    t0 = time.time()
    session_id = state.get("session_id", "")
    user_id = state.get("user_id", "")
    secured_sql = state.get("refined_sql", "")

    stored = False
    if session_id and secured_sql:
        stored = sql_cache.store(
            session_id=session_id,
            user_id=user_id,
            secured_sql=secured_sql,
        )

    step = {
        "node": "cache_write",
        "status": "stored" if stored else "skipped",
        "session_id": session_id[:16] if session_id else "",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[cache_write] user={user_id} stored={stored} session={session_id[:16] if session_id else 'none'}")
    return {"steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node: Refresh Execution (NEW — refresh graph only)
# ─────────────────────────────────────────────

def refresh_execution_node(state: AgentState) -> Dict[str, Any]:
    """
    Reads cached SQL from Redis, validates user access, executes against
    MySQL, and populates state for response_node.  Never touches any LLM,
    validator, or RBAC node.
    """
    session_id = state.get("session_id", "")
    request_user_id = state.get("user_id", "")
    t0 = time.time()

    # ── 1. Read from Redis ────────────────────────────────────────────────────
    cached = sql_cache.get(session_id)
    if cached is None:
        logger.warning(f"[refresh] SESSION_EXPIRED session={session_id[:16]}")
        return {
            "formatted_result": {
                "status": "error",
                "error_code": "SESSION_EXPIRED",
                "message": "Cached SQL not found or expired. Please regenerate the report.",
            }
        }

    # ── 2. Validate user access ───────────────────────────────────────────────
    if cached["user_id"] != request_user_id:
        logger.warning(
            f"[refresh] ACCESS_DENIED session={session_id[:16]} "
            f"cached_user={cached['user_id']} request_user={request_user_id}"
        )
        return {
            "formatted_result": {
                "status": "error",
                "error_code": "ACCESS_DENIED",
                "message": "Access denied: user does not match the session owner.",
            }
        }

    secured_sql = cached["secured_sql"]

    # ── 3. Safety guard: only SELECT is allowed ───────────────────────────────
    if not secured_sql.strip().upper().startswith("SELECT"):
        logger.error(f"[refresh] non-SELECT SQL in cache session={session_id[:16]}")
        sql_cache.delete(session_id)
        return {
            "formatted_result": {
                "status": "error",
                "error_code": "ACCESS_DENIED",
                "message": "Cached query is not a SELECT statement.",
            }
        }

    page      = state.get("page", 1)
    page_size = state.get("page_size", settings.PAGE_SIZE)
    bound_params = {"employee_id": request_user_id}

    # ── 4. Execute against MySQL ──────────────────────────────────────────────
    try:
        rows, columns, total_rows, total_pages = db_manager.execute_paginated(
            secured_sql, page=page, page_size=page_size, params=bound_params
        )
    except OperationalError as exc:
        logger.error(f"[refresh] SCHEMA_CHANGED session={session_id[:16]}: {exc}")
        sql_cache.delete(session_id)
        return {
            "formatted_result": {
                "status": "error",
                "error_code": "SCHEMA_CHANGED",
                "message": "Database schema has changed. Please regenerate the report.",
            }
        }
    except Exception as exc:
        logger.error(f"[refresh] execution error session={session_id[:16]}: {exc}")
        return {
            "formatted_result": {
                "status": "error",
                "error_code": "EXECUTION_ERROR",
                "message": f"Query execution failed: {exc}",
            }
        }

    duration = round(time.time() - t0, 4)
    logger.info(
        f"[refresh] executed session={session_id[:16]} rows={len(rows)} "
        f"total={total_rows} page={page}/{total_pages} ms={round(duration*1000,1)}"
    )
    return {
        "execution_result": {"rows": rows, "columns": columns, "error": None},
        "execution_time": duration,
        "refined_sql": secured_sql,
        "total_rows":    total_rows,
        "total_pages":   total_pages,
        "has_next_page": page < total_pages,
        "has_prev_page": page > 1,
        # Minimal llm_response so response_node can format cleanly
        "llm_response": {"explanation": "Live data refresh", "columns": columns, "filters": []},
    }


# ─────────────────────────────────────────────
# Node: Response (NEW — refresh graph only)
# ─────────────────────────────────────────────

def response_node(state: AgentState) -> Dict[str, Any]:
    """
    Formats the refresh execution result using the same logic as
    result_formatter_node and adds refresh metadata.  If the previous
    node set an error_code, passes it through unchanged.
    """
    # Pass error results straight through
    existing = state.get("formatted_result", {})
    if existing.get("error_code"):
        return {}

    exec_result = state.get("execution_result", {})
    llm_resp = state.get("llm_response", {})
    rows: list = exec_result.get("rows", [])
    columns: list = exec_result.get("columns", [])

    rows, columns = _apply_employee_format(rows, columns)

    dimensions      = [col for col in columns if not any(kw in col.lower() for kw in _METRIC_KW)]
    filterable_cols = filter_recommender.filterable_columns(rows=rows, columns=columns)
    recommended_filters = [fc["column"] for fc in filterable_cols]

    refreshed_at = datetime.now(timezone.utc).isoformat()
    page         = state.get("page", 1)
    page_size    = state.get("page_size", settings.PAGE_SIZE)
    total_rows   = state.get("total_rows", len(rows))
    total_pages  = state.get("total_pages", 1)

    formatted = {
        "status": "success",
        "sql_query": "",  # never expose cached SQL
        "explanation": llm_resp.get("explanation", "Live data refresh"),
        "dimensions": dimensions,
        "recommended_column_filters": recommended_filters,
        "filterable_columns": filterable_cols,
        "filter_instruction": "client_side_only",
        "llm_call_on_filter": False,
        "data": rows,
        "row_count": len(rows),
        "execution_time": state.get("execution_time", 0.0),
        "refresh_mode": True,
        "refreshed_at": refreshed_at,
        "cache_hit": False,
        # Pagination metadata
        "page":          page,
        "page_size":     page_size,
        "total_rows":    total_rows,
        "total_pages":   total_pages,
        "has_next_page": state.get("has_next_page", False),
        "has_prev_page": state.get("has_prev_page", False),
    }

    logger.info(f"[response] refresh formatted rows={len(rows)} total={total_rows} page={page}/{total_pages} at={refreshed_at}")
    return {
        "formatted_result": formatted,
        "refresh_mode": True,
        "refreshed_at": refreshed_at,
    }
